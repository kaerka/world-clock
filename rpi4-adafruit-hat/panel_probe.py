#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
#
# Geometry / pixel-mapper probe for the 6-panel (16x32) Adafruit-HAT build.
#
# The Adafruit HAT drives all six panels as one chain, so by default the
# library sees a 192x16 ribbon. To turn that into the intended 64x48 (2 wide x
# 3 tall) block we need a pixel-mapper that matches how the ribbon physically
# snakes between panels. This tool helps you discover and verify that mapping
# on the lit hardware.
#
# Usage:
#   sudo python3 panel_probe.py chain        # label each chain position 0..5
#   sudo python3 panel_probe.py grid         # draw a 64x48 coordinate frame
#   sudo python3 panel_probe.py grid "U-mapper"   # ...with a candidate mapper
#
# In "chain" mode each 32x16 segment of the raw ribbon is filled a distinct
# color and stamped with its chain index. Note where each numbered/colored
# block physically lands (top-left, top-right, middle-left, ...). That mapping
# tells you the snake order, which determines the PIXEL_MAPPER in worldclock.py.
#
# In "grid" mode we apply a candidate mapper and draw a border + corner marks
# on a 64x48 logical canvas; if the mapper is right you'll see one clean
# rectangle with corners labeled TL/TR/BL/BR.

import sys
import time

from rgbmatrix import RGBMatrix, RGBMatrixOptions, graphics

PANEL_ROWS = 16
PANEL_COLS = 32
CHAIN_LENGTH = 6
PARALLEL = 1
GPIO_SLOWDOWN = 5

COLORS = [
    graphics.Color(255, 0, 0),
    graphics.Color(0, 255, 0),
    graphics.Color(0, 0, 255),
    graphics.Color(255, 255, 0),
    graphics.Color(255, 0, 255),
    graphics.Color(0, 255, 255),
]
WHITE = graphics.Color(255, 255, 255)


def build_matrix(pixel_mapper=""):
    options = RGBMatrixOptions()
    options.rows = PANEL_ROWS
    options.cols = PANEL_COLS
    options.chain_length = CHAIN_LENGTH
    options.parallel = PARALLEL
    if pixel_mapper:
        options.pixel_mapper_config = pixel_mapper
    options.hardware_mapping = "adafruit-hat"
    options.gpio_slowdown = GPIO_SLOWDOWN
    options.brightness = 60
    options.drop_privileges = False
    # Onboard sound (snd_bcm2835) is loaded on this Pi, so hardware PWM is
    # unavailable until it's blacklisted + rebooted. Disable hardware pulsing so
    # the probe still runs (a little flicker is fine for a test pattern).
    options.disable_hardware_pulsing = True
    return RGBMatrix(options=options)


def probe_chain():
    matrix = build_matrix("")  # raw 192x16 ribbon
    font = graphics.Font()
    font.LoadFont("tom-thumb.bdf")
    canvas = matrix.CreateFrameCanvas()
    canvas.Clear()
    for idx in range(CHAIN_LENGTH):
        x0 = idx * PANEL_COLS
        color = COLORS[idx % len(COLORS)]
        for y in range(PANEL_ROWS):
            for x in range(x0, x0 + PANEL_COLS):
                canvas.SetPixel(x, y, color.red, color.green, color.blue)
        graphics.DrawText(canvas, font, x0 + 2, 6, WHITE, "P{}".format(idx))
    matrix.SwapOnVSync(canvas)
    print("Chain probe: 6 colored/numbered blocks on the raw 192x16 ribbon.")
    print("Record where each P0..P5 physically lands. Ctrl-C to exit.")
    _hold()


def probe_grid(pixel_mapper):
    matrix = build_matrix(pixel_mapper)
    w, h = matrix.width, matrix.height
    font = graphics.Font()
    font.LoadFont("tom-thumb.bdf")
    canvas = matrix.CreateFrameCanvas()
    canvas.Clear()
    for x in range(w):
        canvas.SetPixel(x, 0, 80, 80, 80)
        canvas.SetPixel(x, h - 1, 80, 80, 80)
    for y in range(h):
        canvas.SetPixel(0, y, 80, 80, 80)
        canvas.SetPixel(w - 1, y, 80, 80, 80)
    graphics.DrawText(canvas, font, 1, 6, COLORS[0], "TL")
    graphics.DrawText(canvas, font, w - 9, 6, COLORS[1], "TR")
    graphics.DrawText(canvas, font, 1, h - 1, COLORS[2], "BL")
    graphics.DrawText(canvas, font, w - 9, h - 1, COLORS[3], "BR")
    matrix.SwapOnVSync(canvas)
    print("Grid probe: logical canvas is {}x{}, mapper={!r}.".format(
        w, h, pixel_mapper or "(none)"))
    print("A correct mapper shows ONE clean rectangle with TL/TR/BL/BR in the")
    print("right corners. Ctrl-C to exit.")
    _hold()


def _hold():
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        sys.exit(0)


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "chain"
    if mode == "chain":
        probe_chain()
    elif mode == "grid":
        mapper = sys.argv[2] if len(sys.argv) > 2 else ""
        probe_grid(mapper)
    else:
        print(__doc__)
        sys.exit(1)
