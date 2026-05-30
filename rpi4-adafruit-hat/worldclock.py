#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2020 John Park for Adafruit Industries
#
# SPDX-License-Identifier: MIT
#
# World clock for Raspberry Pi 4 + Adafruit RGB Matrix HAT, driving six
# 16x32 HUB75 panels wired as a boustrophedon (serpentine) into a 64x48 canvas
# (2 wide x 3 tall). This is the Pi port of the CircuitPython builds in
# ../64x64-matrixportal-s3 and ../32x32-matrixportal-m4.
#
# On the Pi we drop CircuitPython's displayio stack and the hand-rolled DST
# math entirely:
#   - Time zones use the OS timezone database via zoneinfo, so DST is always
#     correct. The system clock is kept in sync by NTP (timesyncd).
#   - Rendering is done with Pillow onto a logical 64x48 image, which is then
#     remapped to the physical 192x16 chain (see PHYSICAL WIRING below) and
#     pushed with hzeller/rpi-rgb-led-matrix.
#
# PHYSICAL WIRING (confirmed on hardware via panel_probe.py, front view):
#   Chain index -> physical cell:
#     P0 bottom-left   P1 bottom-right
#     P2 mid-left      P3 mid-right
#     P4 top-left      P5 top-right
#   i.e. the chain runs bottom row -> middle row -> top row, left-to-right in
#   each row, all panels upright. So the remap is a pure position permutation
#   (no internal pixel flipping): chain = (2 - grid_row) * 2 + grid_col.

import os
import sys
import time
import tempfile
from datetime import datetime
from zoneinfo import ZoneInfo

from PIL import Image, ImageDraw, ImageFont, BdfFontFile
from rgbmatrix import RGBMatrix, RGBMatrixOptions

BLINK = True
DEBUG = False

# Hardware PWM (flicker-free) requires the onboard sound module (snd_bcm2835)
# to be absent. We auto-detect it below, so the clock always starts: hardware
# pulsing when sound is gone, software pulsing (a little flicker) if it's
# loaded. Set this True to force software pulsing regardless (see README.md).
FORCE_DISABLE_HW_PULSE = False


def sound_module_loaded():
    """True if snd_bcm2835 (which conflicts with the HAT's PWM) is loaded."""
    try:
        with open("/proc/modules") as fp:
            return any(line.startswith("snd_bcm2835") for line in fp)
    except OSError:
        return False

# --- Geometry -------------------------------------------------------------
PANEL_ROWS = 16          # each panel is 16 tall...
PANEL_COLS = 32          # ...and 32 wide
CHAIN_LENGTH = 6         # Adafruit HAT: single chain of 6
PARALLEL = 1
GRID_COLS = 2            # 2 panels across
GRID_ROWS = 3            # 3 panels down

CANVAS_W = GRID_COLS * PANEL_COLS   # 64
CANVAS_H = GRID_ROWS * PANEL_ROWS   # 48
RIBBON_W = CHAIN_LENGTH * PANEL_COLS  # 192

GPIO_SLOWDOWN = 5
BRIGHTNESS = 70

# (grid_row, grid_col) -> chain index. grid_row 0=top, grid_col 0=left.
CELL_TO_CHAIN = {
    (0, 0): 4,  # top-left    -> P4
    (0, 1): 5,  # top-right   -> P5
    (1, 0): 2,  # mid-left    -> P2
    (1, 1): 3,  # mid-right   -> P3
    (2, 0): 0,  # bottom-left -> P0
    (2, 1): 1,  # bottom-right-> P1
}

# --- Zones: (label, RGB time color, IANA timezone) ------------------------
ZONES = (
    ("US EAST", (0x00, 0xFF, 0x66), ZoneInfo("America/New_York")),
    ("AMSTERDAM", (0xFF, 0x80, 0x00), ZoneInfo("Europe/Amsterdam")),
    ("SPB", (0xFF, 0x00, 0x00), ZoneInfo("Europe/Moscow")),
)

UTC = ZoneInfo("UTC")

# --- Title / border / label colors (from the 64x64 S3 build) --------------
TITLE_COLOR = (0xFF, 0xAA, 0x00)   # orange
LABEL_COLOR = (0x30, 0x60, 0xFF)   # blue (city labels)
BORDER_COLOR = (0x30, 0x30, 0x30)  # dim grey 1px frame

# Vertical layout on the 48px-tall canvas (inside the 1px border).
# Title uses the small font; each zone is a small label over a 5x7 time.
TITLE_Y = 1
ZONE_TOP = (7, 20, 33)   # top of each zone block (label row), 13px apart
LABEL_DY = 0             # label offset within a zone (small font, ~6px)
TIME_DY = 6              # time offset within a zone (5x7 font, ~7px)


def load_bdf(path):
    """Convert a .bdf bitmap font to a Pillow font (in a temp dir)."""
    with open(path, "rb") as fp:
        bdf = BdfFontFile.BdfFontFile(fp)
    base = os.path.join(tempfile.mkdtemp(prefix="wc_font_"), "font")
    bdf.save(base)
    return ImageFont.load(base + ".pil")


def build_matrix():
    options = RGBMatrixOptions()
    options.rows = PANEL_ROWS
    options.cols = PANEL_COLS
    options.chain_length = CHAIN_LENGTH
    options.parallel = PARALLEL
    options.hardware_mapping = "adafruit-hat"
    options.gpio_slowdown = GPIO_SLOWDOWN
    options.pwm_bits = 10
    options.pwm_lsb_nanoseconds = 50
    options.brightness = BRIGHTNESS
    options.drop_privileges = False
    if FORCE_DISABLE_HW_PULSE or sound_module_loaded():
        options.disable_hardware_pulsing = True
    if DEBUG:
        options.show_refresh_rate = True
    return RGBMatrix(options=options)


def remap_to_ribbon(logical):
    """Fold the logical 64x48 image onto the physical 192x16 chain."""
    ribbon = Image.new("RGB", (RIBBON_W, PANEL_ROWS), (0, 0, 0))
    for (grow, gcol), chain in CELL_TO_CHAIN.items():
        cell = logical.crop((gcol * PANEL_COLS, grow * PANEL_ROWS,
                             (gcol + 1) * PANEL_COLS, (grow + 1) * PANEL_ROWS))
        ribbon.paste(cell, (chain * PANEL_COLS, 0))
    return ribbon


def draw_centered(draw, font, y, text, color):
    bbox = draw.textbbox((0, 0), text, font=font)
    w = bbox[2] - bbox[0]
    x = max(0, (CANVAS_W - w) // 2)
    draw.text((x, y), text, font=font, fill=color)


def render_clock(small_font, time_font, now_utc):
    """One 64x48 frame: bordered title + three zones (small label over time)."""
    img = Image.new("RGB", (CANVAS_W, CANVAS_H), (0, 0, 0))
    draw = ImageDraw.Draw(img)
    draw.rectangle((0, 0, CANVAS_W - 1, CANVAS_H - 1), outline=BORDER_COLOR)
    draw_centered(draw, small_font, TITLE_Y, "WORLD CLOCK", TITLE_COLOR)
    colon = ":" if (not BLINK or now_utc.second % 2) else " "
    for (label, color, tz), top in zip(ZONES, ZONE_TOP):
        local = now_utc.astimezone(tz)
        tstr = "{:02d}{}{:02d}".format(local.hour, colon, local.minute)
        draw_centered(draw, small_font, top + LABEL_DY, label, LABEL_COLOR)
        draw_centered(draw, time_font, top + TIME_DY, tstr, color)
        if DEBUG:
            print(label, tstr)
    return img


def render_test():
    """Corner/border pattern (through the same remap) to verify the mapping."""
    img = Image.new("RGB", (CANVAS_W, CANVAS_H), (0, 0, 0))
    draw = ImageDraw.Draw(img)
    draw.rectangle((0, 0, CANVAS_W - 1, CANVAS_H - 1), outline=(60, 60, 60))
    font = load_bdf("tom-thumb.bdf")
    draw.text((1, 1), "TL", font=font, fill=(255, 0, 0))
    draw.text((CANVAS_W - 9, 1), "TR", font=font, fill=(0, 255, 0))
    draw.text((1, CANVAS_H - 7), "BL", font=font, fill=(0, 0, 255))
    draw.text((CANVAS_W - 9, CANVAS_H - 7), "BR", font=font, fill=(255, 255, 0))
    return img


def main():
    test_mode = len(sys.argv) > 1 and sys.argv[1] == "test"
    matrix = build_matrix()
    canvas = matrix.CreateFrameCanvas()

    if test_mode:
        canvas.SetImage(remap_to_ribbon(render_test()))
        matrix.SwapOnVSync(canvas)
        print("Test pattern up: expect one clean 64x48 frame, corners TL/TR/BL/BR.")
        while True:
            time.sleep(1)

    small_font = load_bdf("tom-thumb.bdf")  # title + city labels
    time_font = load_bdf("5x7.bdf")         # the (more legible) times
    while True:
        frame = render_clock(small_font, time_font, datetime.now(UTC))
        canvas.SetImage(remap_to_ribbon(frame))
        canvas = matrix.SwapOnVSync(canvas)
        time.sleep(0.5)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(0)
