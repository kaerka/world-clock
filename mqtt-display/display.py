#!/usr/bin/env python3
"""
Matrix MQTT display — clock-idle screen + on-demand MQTT messages.

A single process owns the LED matrix and shows two things:

  * IDLE: the world clock (rendering + geometry are reused directly from the
    chosen clock build, so the clock looks identical to the standalone clock).
  * MESSAGE: text received over MQTT. A new message takes over the panel,
    word-wrapped to fit, scrolling vertically if it overflows. After
    ``idle_seconds`` with no newer message, it auto-reverts to the clock.

Why one process: the matrix holds the GPIO/PWM, so only one program can drive
it at a time. This app therefore *supersedes* the clock service (the clock
lives on as the idle screen here).

Two hardware backends, selected by the ``backend`` config key:
  * "spectra"      -> ../rpi4-electrodragon/spectra_clock.py  (9x 64x64, 192x192,
                      Electrodragon, TTF fonts, no remap)        [default]
  * "adafruit-hat" -> ../rpi4-adafruit-hat/worldclock.py        (6x 16x32, 64x48,
                      Adafruit HAT, BDF fonts, serpentine remap)

Payloads may be plain UTF-8 text, or JSON:

    {"text": "hello world", "ttl": 30, "color": [255, 80, 0]}

A bare string, or any payload that isn't a JSON object, is treated as plain
text using the configured defaults.

Config lives in config.local.json (gitignored) — copy config.example.json.

Run a no-hardware render check (no matrix, no broker, no paho needed):
    python3 display.py selftest      # writes /tmp/mqtt_*.png
"""
import json
import os
import sys
import time
import threading
from datetime import datetime
from zoneinfo import ZoneInfo

from PIL import Image, ImageDraw, ImageFont

HERE = os.path.dirname(os.path.abspath(__file__))
HAT_DIR = os.path.normpath(os.path.join(HERE, "..", "rpi4-adafruit-hat"))
SPECTRA_DIR = os.path.normpath(os.path.join(HERE, "..", "rpi4-electrodragon"))
DEJAVU = "/usr/share/fonts/truetype/dejavu"

UTC = ZoneInfo("UTC")
CONFIG_PATH = os.path.join(HERE, "config.local.json")

# Canvas geometry + border are filled in from the selected backend at startup;
# the render helpers below read these module globals.
CANVAS_W = None
CANVAS_H = None
BORDER_COLOR = (0x20, 0x20, 0x20)

DEFAULTS = {
    "backend": "spectra",       # "spectra" (this wall) or "adafruit-hat" (the minis)
    "broker": "localhost",
    "port": 1883,
    "username": None,
    "password": None,
    "tls": False,
    "topics": ["whisper/transcript"],
    "client_id": "matrix-mqtt-display",
    "idle_seconds": 30,         # message lingers this long after the last one, then -> clock
    "text_color": [255, 255, 255],
    "font": "tom-thumb",        # adafruit-hat only: "tom-thumb" (dense) or "5x7"
    "text_font_size": 18,       # spectra only: DejaVuSans-Bold px size for messages
    "fps": 20,                  # render loop rate (smooth scroll)
    "scroll_px_per_sec": 18,    # vertical scroll speed for overflowing text
}


# --- Hardware backends ----------------------------------------------------
class SpectraBackend:
    """Spectra wall: Pi4 + Electrodragon, 9x 64x64 -> 192x192, TTF fonts, no remap."""
    name = "spectra"
    border = (0x20, 0x20, 0x20)

    def __init__(self):
        if SPECTRA_DIR not in sys.path:
            sys.path.insert(0, SPECTRA_DIR)
        import spectra_clock as sc  # noqa: E402
        self.sc = sc
        self.w, self.h = sc.CANVAS_W, sc.CANVAS_H

    def build_matrix(self):
        return self.sc.build_matrix()

    def message_font(self, cfg):
        size = int(cfg.get("text_font_size", 18))
        return ImageFont.truetype(f"{DEJAVU}/DejaVuSans-Bold.ttf", size)

    def render_clock(self, now):
        return self.sc.render_clock(now)

    def to_canvas(self, frame):
        return frame                      # clean 3x3 grid; Rotate:270 handles geometry


class AdafruitHatBackend:
    """The minis: Pi4 + Adafruit HAT, 6x 16x32 -> 64x48, BDF fonts, serpentine remap."""
    name = "adafruit-hat"

    def __init__(self):
        if HAT_DIR not in sys.path:
            sys.path.insert(0, HAT_DIR)
        import worldclock as wc  # noqa: E402
        self.wc = wc
        self.w, self.h = wc.CANVAS_W, wc.CANVAS_H
        self.border = wc.BORDER_COLOR
        self._small = wc.load_bdf(os.path.join(HAT_DIR, "tom-thumb.bdf"))
        self._time = wc.load_bdf(os.path.join(HAT_DIR, "5x7.bdf"))

    def build_matrix(self):
        return self.wc.build_matrix()

    def message_font(self, cfg):
        return self._small if cfg.get("font") == "tom-thumb" else self._time

    def render_clock(self, now):
        return self.wc.render_clock(self._small, self._time, now)

    def to_canvas(self, frame):
        return self.wc.remap_to_ribbon(frame)


def get_backend(name):
    if name in ("adafruit-hat", "hat", "mini"):
        return AdafruitHatBackend()
    if name in ("spectra", "electrodragon"):
        return SpectraBackend()
    sys.exit("Unknown backend {!r}; use 'spectra' or 'adafruit-hat'.".format(name))


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
    """Compose one frame: bordered, with the message (scrolled if it overflows)."""
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
    import paho.mqtt.client as mqtt   # lazy: not needed for selftest/rendering

    # paho 2.x requires an explicit callback API version; 1.6.x (Debian apt) uses
    # the old positional callbacks. Support both.
    try:
        client = mqtt.Client(
            mqtt.CallbackAPIVersion.VERSION1,
            client_id=cfg["client_id"], clean_session=True,
        )
    except (AttributeError, TypeError):
        client = mqtt.Client(client_id=cfg["client_id"], clean_session=True)

    if cfg.get("username"):
        client.username_pw_set(cfg["username"], cfg.get("password"))
    if cfg.get("tls"):
        client.tls_set()

    def on_connect(c, _u, _flags, rc, *_):
        with state.lock:
            state.connected = (rc == 0)
        for topic in cfg["topics"]:
            c.subscribe(topic)
        print("[mqtt] connected rc={} -> subscribed {}".format(rc, cfg["topics"]), flush=True)

    def on_disconnect(_c, _u, rc, *_):
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


def _set_geometry(backend):
    global CANVAS_W, CANVAS_H, BORDER_COLOR
    CANVAS_W, CANVAS_H, BORDER_COLOR = backend.w, backend.h, backend.border


def selftest(cfg):
    """Render the idle clock + a sample (overflowing) message to PNGs. No HW/paho."""
    backend = get_backend(cfg["backend"])
    _set_geometry(backend)
    font = backend.message_font(cfg)
    backend.render_clock(datetime.now(UTC)).save("/tmp/mqtt_clock.png")
    sample = ("MQTT message takes over the wall, word-wrapped and vertically "
              "scrolled when it overflows, then reverts to the clock.")
    tall = build_message_image(sample, font, tuple(cfg["text_color"]), CANVAS_W - 2)
    render_message_frame(tall, 0.0, cfg["scroll_px_per_sec"]).save("/tmp/mqtt_message.png")
    print("selftest: backend={} canvas={}x{} -> /tmp/mqtt_clock.png, /tmp/mqtt_message.png"
          .format(backend.name, CANVAS_W, CANVAS_H), flush=True)


def main():
    cfg = load_config()

    if len(sys.argv) > 1 and sys.argv[1] == "selftest":
        selftest(cfg)
        return

    backend = get_backend(cfg["backend"])
    _set_geometry(backend)
    matrix = backend.build_matrix()
    canvas = matrix.CreateFrameCanvas()
    msg_font = backend.message_font(cfg)

    state = MessageState()
    client = make_client(cfg, state)
    client.loop_start()
    print("[display] backend={} canvas={}x{} idle=clock TTL={}s".format(
        backend.name, CANVAS_W, CANVAS_H, cfg["idle_seconds"]), flush=True)

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
            frame = backend.render_clock(datetime.now(UTC))

        canvas.SetImage(backend.to_canvas(frame))
        canvas = matrix.SwapOnVSync(canvas)
        time.sleep(frame_dt)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(0)
