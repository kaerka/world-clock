#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2020 John Park for Adafruit Industries
#
# SPDX-License-Identifier: MIT
#
# World clock for the Spectra wall (upper half): Raspberry Pi 4 + Electrodragon
# board driving 9 x 64x64 HUB75 panels as a 3x3 grid (192x192 logical canvas).
# This is the big-wall sibling of ../rpi4-adafruit-hat/worldclock.py (which is
# the mini 64x48 build). Same idea (Pillow render -> hzeller matrix, zoneinfo
# for always-correct DST), but:
#   - 192x192 canvas, large TrueType fonts (the mini's 64x48 bitmap layout is
#     far too small to scale up legibly here).
#   - Electrodragon driver board => hardware_mapping="regular" (NOT
#     "adafruit-hat"), 3 parallel chains of 3 panels, pixel_mapper "Rotate:270".
#   - NO custom serpentine CELL_TO_CHAIN remap: the panels form a clean 3x3 grid
#     and Rotate:270 covers the geometry, so we push the 192x192 image directly.
#
# Matrix config is the known-good one already in use on this wall (see
# ~/led-matrix-display/display-weather-multi-cal-gif.py and ROADMAP "Port 3").
#
# rgbmatrix is imported from the prebuilt hzeller build on this box; run via:
#   PYTHONPATH=~/led-matrix-display/rpi-rgb-led-matrix/bindings/python \
#       python3 spectra_clock.py [test]

import sys
import time
from datetime import datetime
from zoneinfo import ZoneInfo

from PIL import Image, ImageDraw, ImageFont
from rgbmatrix import RGBMatrix, RGBMatrixOptions

BLINK = True
DEBUG = False

# --- Geometry (9 x 64x64, 3x3) -------------------------------------------
PANEL_ROWS = 64
PANEL_COLS = 64
CHAIN_LENGTH = 3          # Electrodragon: 3 chains...
PARALLEL = 3              # ...x 3 parallel = 9 panels
PIXEL_MAPPER = "Rotate:270"
HARDWARE_MAPPING = "regular"   # Electrodragon (NOT adafruit-hat)

# Native is cols*chain x rows*parallel = 192x192; Rotate:270 keeps it 192x192.
CANVAS_W = 192
CANVAS_H = 192

GPIO_SLOWDOWN = 5
PWM_BITS = 11
PWM_LSB_NS = 50
BRIGHTNESS = 70           # clock is sparse; bump toward 100 if you want it brighter

# --- Fonts (system DejaVu TTF; always present on Pi OS) -------------------
_FONT_DIR = "/usr/share/fonts/truetype/dejavu"
TITLE_FONT = ImageFont.truetype(f"{_FONT_DIR}/DejaVuSans-Bold.ttf", 22)
LABEL_FONT = ImageFont.truetype(f"{_FONT_DIR}/DejaVuSans-Bold.ttf", 14)
TIME_FONT = ImageFont.truetype(f"{_FONT_DIR}/DejaVuSansMono-Bold.ttf", 30)
DATE_FONT = ImageFont.truetype(f"{_FONT_DIR}/DejaVuSans.ttf", 11)

# --- Zones: (label, RGB time color, IANA timezone) ------------------------
ZONES = (
    ("US EAST", (0x00, 0xFF, 0x66), ZoneInfo("America/New_York")),
    ("AMSTERDAM", (0xFF, 0x80, 0x00), ZoneInfo("Europe/Amsterdam")),
    ("SPB", (0xFF, 0x30, 0x30), ZoneInfo("Europe/Moscow")),
)
UTC = ZoneInfo("UTC")

TITLE_COLOR = (0xFF, 0xAA, 0x00)   # orange
LABEL_COLOR = (0x40, 0x80, 0xFF)   # blue (city labels)
DATE_COLOR = (0x80, 0x80, 0x80)    # dim grey
DIVIDER_COLOR = (0x20, 0x20, 0x20)

# Vertical layout
TITLE_Y = 2
BLOCK_TOP = 32
BLOCK_H = 53                       # 32 + 3*53 = 191


def build_matrix():
    options = RGBMatrixOptions()
    options.rows = PANEL_ROWS
    options.cols = PANEL_COLS
    options.chain_length = CHAIN_LENGTH
    options.parallel = PARALLEL
    options.hardware_mapping = HARDWARE_MAPPING
    options.pixel_mapper_config = PIXEL_MAPPER
    options.gpio_slowdown = GPIO_SLOWDOWN
    options.pwm_bits = PWM_BITS
    options.pwm_lsb_nanoseconds = PWM_LSB_NS
    options.brightness = BRIGHTNESS
    options.drop_privileges = False
    if DEBUG:
        options.show_refresh_rate = True
    return RGBMatrix(options=options)


def _center(draw, cx, y, text, font, color, anchor="ma"):
    draw.text((cx, y), text, font=font, fill=color, anchor=anchor)


def render_clock(now_utc):
    """One 192x192 frame: title band + three stacked zone blocks."""
    img = Image.new("RGB", (CANVAS_W, CANVAS_H), (0, 0, 0))
    draw = ImageDraw.Draw(img)

    _center(draw, CANVAS_W // 2, TITLE_Y, "WORLD CLOCK", TITLE_FONT, TITLE_COLOR)
    draw.line((0, BLOCK_TOP - 3, CANVAS_W - 1, BLOCK_TOP - 3), fill=DIVIDER_COLOR)

    colon = ":" if (not BLINK or now_utc.second % 2) else " "
    for i, (label, color, tz) in enumerate(ZONES):
        top = BLOCK_TOP + i * BLOCK_H
        local = now_utc.astimezone(tz)
        tstr = "{:02d}{}{:02d}".format(local.hour, colon, local.minute)
        datestr = local.strftime("%a %d %b")
        draw.text((6, top + 1), label, font=LABEL_FONT, fill=LABEL_COLOR, anchor="la")
        draw.text((CANVAS_W - 6, top + 3), datestr, font=DATE_FONT,
                  fill=DATE_COLOR, anchor="ra")
        _center(draw, CANVAS_W // 2, top + 18, tstr, TIME_FONT, color)
        if i < len(ZONES) - 1:
            divy = top + BLOCK_H - 1
            draw.line((0, divy, CANVAS_W - 1, divy), fill=DIVIDER_COLOR)
        if DEBUG:
            print(label, tstr, datestr)
    return img


def render_test():
    """3x3 panel grid w/ per-panel coords + corner markers to verify mapping."""
    img = Image.new("RGB", (CANVAS_W, CANVAS_H), (0, 0, 0))
    draw = ImageDraw.Draw(img)
    draw.rectangle((0, 0, CANVAS_W - 1, CANVAS_H - 1), outline=(60, 60, 60))
    for gr in range(3):
        for gc in range(3):
            x0, y0 = gc * 64, gr * 64
            draw.rectangle((x0, y0, x0 + 63, y0 + 63), outline=(40, 40, 40))
            draw.text((x0 + 4, y0 + 4), f"r{gr}c{gc}", font=LABEL_FONT,
                      fill=(0, 200, 255), anchor="la")
    draw.text((2, 2), "TL", font=LABEL_FONT, fill=(255, 0, 0), anchor="la")
    draw.text((CANVAS_W - 2, 2), "TR", font=LABEL_FONT, fill=(0, 255, 0), anchor="ra")
    draw.text((2, CANVAS_H - 2), "BL", font=LABEL_FONT, fill=(0, 0, 255), anchor="ld")
    draw.text((CANVAS_W - 2, CANVAS_H - 2), "BR", font=LABEL_FONT,
              fill=(255, 255, 0), anchor="rd")
    return img


def main():
    test_mode = len(sys.argv) > 1 and sys.argv[1] == "test"
    matrix = build_matrix()
    canvas = matrix.CreateFrameCanvas()

    if test_mode:
        canvas.SetImage(render_test())
        matrix.SwapOnVSync(canvas)
        print("Test pattern up: 3x3 grid, panels labelled r{row}c{col}, "
              "corners TL/TR/BL/BR. Confirm orientation/order, then Ctrl-C.")
        while True:
            time.sleep(1)

    while True:
        frame = render_clock(datetime.now(UTC))
        canvas.SetImage(frame)
        canvas = matrix.SwapOnVSync(canvas)
        time.sleep(0.5)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(0)
