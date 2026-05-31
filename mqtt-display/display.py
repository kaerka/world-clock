#!/usr/bin/env python3
"""
Matrix MQTT display — Raspberry Pi 4 + Adafruit RGB Matrix HAT (6x16x32 -> 64x48).

A single process that owns the LED matrix and shows two things:

  * IDLE: the world clock (rendering + panel geometry are reused directly from
    ../rpi4-adafruit-hat/worldclock.py, so the clock looks identical).
  * MESSAGE: text received over MQTT. A new message takes over the panel,
    word-wrapped to fit, scrolling vertically if it overflows. After
    ``idle_seconds`` with no newer message, it auto-reverts to the clock.

Why one process: the matrix holds the GPIO/PWM, so only one program can drive
it at a time. This app therefore *supersedes* worldclock.service (the clock
lives on as the idle screen here).

Payloads may be plain UTF-8 text, or JSON:

    {"text": "hello world", "ttl": 30, "color": [255, 80, 0]}

A bare string, or any payload that isn't a JSON object, is treated as plain
text using the configured defaults.

Config lives in config.local.json (gitignored) — copy config.example.json.
"""
import json
import os
import sys
import time
import threading
from datetime import datetime

from PIL import Image, ImageDraw
import paho.mqtt.client as mqtt

# --- Reuse the world-clock rendering + panel geometry ---------------------
WC_DIR = os.path.normpath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "rpi4-adafruit-hat")
)
sys.path.insert(0, WC_DIR)
import worldclock as wc  # noqa: E402

CANVAS_W = wc.CANVAS_W
CANVAS_H = wc.CANVAS_H
BORDER_COLOR = wc.BORDER_COLOR
UTC = wc.UTC

CONFIG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.local.json")

DEFAULTS = {
    "broker": "localhost",
    "port": 1883,
    "username": None,
    "password": None,
    "tls": False,
    "topics": ["whisper/transcript"],
    "client_id": "matrix-mqtt-display",
    "idle_seconds": 30,        # message lingers this long after the last one, then -> clock
    "text_color": [255, 255, 255],
    "font": "tom-thumb",       # "tom-thumb" (dense) or "5x7" (more legible)
    "fps": 20,                 # render loop rate (smooth scroll)
    "scroll_px_per_sec": 14,   # vertical scroll speed for overflowing text
}


def load_config():
    if not os.path.exists(CONFIG_PATH):
        sys.exit(
            "Missing {}.\nCopy config.example.json to config.local.json and edit it."
            .format(CONFIG_PATH)
        )
    with open(CONFIG_PATH) as fp:
        user = json.load(fp)
    cfg = dict(DEFAULTS)
    cfg.update(user)
    return cfg


class MessageState:
    """Latest message, shared between the MQTT thread and the render loop."""
    def __init__(self):
        self.lock = threading.Lock()
        self.text = None
        self.color = (255, 255, 255)
        self.ttl = 30.0
        self.arrived = 0.0
        self.version = 0          # bumped on every new message (cache key)
        self.connected = False

    def update(self, text, color, ttl):
        with self.lock:
            self.text = text
            self.color = color
            self.ttl = ttl
            self.arrived = time.time()
            self.version += 1

    def snapshot(self):
        with self.lock:
            return (self.version, self.text, self.color, self.ttl,
                    self.arrived, self.connected)


def parse_payload(payload, cfg):
    """Return (text, ttl, color). Accepts plain text or a JSON object."""
    raw = payload.decode("utf-8", "replace").strip()
    text = raw
    ttl = float(cfg["idle_seconds"])
    color = tuple(cfg["text_color"])
    try:
        obj = json.loads(raw)
    except (ValueError, TypeError):
        obj = None
    if isinstance(obj, dict):
        text = str(obj.get("text", obj.get("message", ""))).strip()
        if "ttl" in obj:
            try:
                ttl = float(obj["ttl"])
            except (ValueError, TypeError):
                pass
        c = obj.get("color")
        if isinstance(c, (list, tuple)) and len(c) == 3:
            try:
                color = tuple(max(0, min(255, int(v))) for v in c)
            except (ValueError, TypeError):
                pass
    elif isinstance(obj, str):
        text = obj.strip()
    return text, ttl, color


# --- Text layout ----------------------------------------------------------
_SCRATCH = ImageDraw.Draw(Image.new("RGB", (1, 1)))


def _line_height(font):
    bbox = _SCRATCH.textbbox((0, 0), "Ayg", font=font)
    return (bbox[3] - bbox[1]) + 1


def _split_long_word(word, font, max_w):
    chunks = []
    chunk = ""
    for ch in word:
        if not chunk or _SCRATCH.textlength(chunk + ch, font=font) <= max_w:
            chunk += ch
        else:
            chunks.append(chunk)
            chunk = ch
    if chunk:
        chunks.append(chunk)
    return chunks


def wrap_text(text, font, max_w):
    lines = []
    for para in text.split("\n"):
        cur = ""
        for word in para.split():
            tokens = ([word] if _SCRATCH.textlength(word, font=font) <= max_w
                      else _split_long_word(word, font, max_w))
            for tok in tokens:
                trial = tok if not cur else cur + " " + tok
                if _SCRATCH.textlength(trial, font=font) <= max_w:
                    cur = trial
                else:
                    lines.append(cur)
                    cur = tok
        lines.append(cur)
    return lines


def build_message_image(text, font, color, max_w):
    """A full-width image holding the wrapped, horizontally-centered text."""
    lines = wrap_text(text, font, max_w)
    line_h = _line_height(font)
    img = Image.new("RGB", (max_w, max(1, line_h * len(lines))), (0, 0, 0))
    draw = ImageDraw.Draw(img)
    y = 0
    for ln in lines:
        w = _SCRATCH.textlength(ln, font=font)
        draw.text((max(0, (max_w - w) // 2), y), ln, font=font, fill=color)
        y += line_h
    return img


def render_message_frame(tall, elapsed, scroll_pps):
    """Compose one 64x48 frame: bordered, with the message (scrolled if tall)."""
    img = Image.new("RGB", (CANVAS_W, CANVAS_H), (0, 0, 0))
    draw = ImageDraw.Draw(img)
    draw.rectangle((0, 0, CANVAS_W - 1, CANVAS_H - 1), outline=BORDER_COLOR)
    inner_w, inner_h = CANVAS_W - 2, CANVAS_H - 2
    inner = Image.new("RGB", (inner_w, inner_h), (0, 0, 0))
    content_h = tall.height
    if content_h <= inner_h:
        inner.paste(tall, (0, (inner_h - content_h) // 2))
    else:
        period = content_h + inner_h          # scroll fully off, gap, repeat
        off = int(elapsed * scroll_pps) % period
        inner.paste(tall, (0, -off))
        inner.paste(tall, (0, -off + period))
    img.paste(inner, (1, 1))
    return img


# --- MQTT -----------------------------------------------------------------
def make_client(cfg, state):
    client = mqtt.Client(client_id=cfg["client_id"], clean_session=True)
    if cfg.get("username"):
        client.username_pw_set(cfg["username"], cfg.get("password"))
    if cfg.get("tls"):
        client.tls_set()

    def on_connect(c, _u, _flags, rc):
        with state.lock:
            state.connected = (rc == 0)
        for topic in cfg["topics"]:
            c.subscribe(topic)
        print("[mqtt] connected rc={} -> subscribed {}".format(rc, cfg["topics"]), flush=True)

    def on_disconnect(_c, _u, rc):
        with state.lock:
            state.connected = False
        print("[mqtt] disconnected rc={}".format(rc), flush=True)

    def on_message(_c, _u, msg):
        text, ttl, color = parse_payload(msg.payload, cfg)
        if not text:
            return
        state.update(text, color, ttl)
        print("[mqtt] {}: {!r} (ttl={}s)".format(msg.topic, text, ttl), flush=True)

    client.on_connect = on_connect
    client.on_disconnect = on_disconnect
    client.on_message = on_message
    client.reconnect_delay_set(min_delay=1, max_delay=30)
    client.connect_async(cfg["broker"], int(cfg["port"]), keepalive=60)
    return client


def main():
    cfg = load_config()
    matrix = wc.build_matrix()
    canvas = matrix.CreateFrameCanvas()

    small_font = wc.load_bdf(os.path.join(WC_DIR, "tom-thumb.bdf"))  # clock title/labels
    time_font = wc.load_bdf(os.path.join(WC_DIR, "5x7.bdf"))         # clock times
    msg_font = small_font if cfg["font"] == "tom-thumb" else time_font

    state = MessageState()
    client = make_client(cfg, state)
    client.loop_start()
    print("[display] idle=clock, message TTL default {}s, font={}".format(
        cfg["idle_seconds"], cfg["font"]), flush=True)

    frame_dt = 1.0 / float(cfg["fps"])
    scroll_pps = float(cfg["scroll_px_per_sec"])
    cached_ver, tall = -1, None

    while True:
        now = time.time()
        version, text, color, ttl, arrived, _conn = state.snapshot()
        active = text is not None and (now - arrived) < ttl

        if active:
            if version != cached_ver:
                tall = build_message_image(text, msg_font, color, CANVAS_W - 2)
                cached_ver = version
            frame = render_message_frame(tall, now - arrived, scroll_pps)
        else:
            frame = wc.render_clock(small_font, time_font, datetime.now(UTC))

        canvas.SetImage(wc.remap_to_ribbon(frame))
        canvas = matrix.SwapOnVSync(canvas)
        time.sleep(frame_dt)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(0)
