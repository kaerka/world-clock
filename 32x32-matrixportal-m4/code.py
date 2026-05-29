# SPDX-FileCopyrightText: 2020 John Park for Adafruit Industries
#
# SPDX-License-Identifier: MIT
#
# World clock for MatrixPortal M4 + 32x32 RGB matrix.
# Shows three time zones, one per row:
#   US  -> US Eastern    (EST/EDT, DST aware)
#   AM  -> Amsterdam      (Central Europe, DST aware)
#   SP  -> Saint Petersburg (Moscow time, no DST)
#
# NOTE: The MatrixPortal M4 (SAMD51) has NO native `wifi` module — it uses the
# ESP32 "AirLift" co-processor. We let adafruit_matrixportal.network.Network
# manage the ESP32 and fetch UTC over HTTP (Adafruit IO time service), setting
# the RTC to UTC. Per-zone local time is then computed with the same DST rules
# as the 64x64 S3 build. Requires ADAFRUIT_AIO_USERNAME / ADAFRUIT_AIO_KEY in
# settings.toml (in addition to the WiFi credentials).

import time
import gc
import board
import displayio
import framebufferio
import rgbmatrix
from adafruit_bitmap_font import bitmap_font
from adafruit_display_text.label import Label
from adafruit_matrixportal.network import Network

BLINK = True
DEBUG = False

gc.collect()

# --- Display setup (single 32x32 panel) ---
bit_depth = 4
base_width = 32
base_height = 32
chain_across = 1
tile_down = 1
serpentine = False

width = base_width * chain_across
height = base_height * tile_down

# 32x32 is 1/16 scan -> only FOUR address pins (no E line, no jumper).
addr_pins = [board.MTX_ADDRA, board.MTX_ADDRB, board.MTX_ADDRC, board.MTX_ADDRD]
rgb_pins = [
    board.MTX_R1,
    board.MTX_G1,
    board.MTX_B1,
    board.MTX_R2,
    board.MTX_G2,
    board.MTX_B2,
]
clock_pin = board.MTX_CLK
latch_pin = board.MTX_LAT
oe_pin = board.MTX_OE

displayio.release_displays()
matrix = rgbmatrix.RGBMatrix(
    width=width,
    height=height,
    bit_depth=bit_depth,
    rgb_pins=rgb_pins,
    addr_pins=addr_pins,
    clock_pin=clock_pin,
    latch_pin=latch_pin,
    output_enable_pin=oe_pin,
    tile=tile_down,
    serpentine=serpentine,
)
display = framebufferio.FramebufferDisplay(matrix, auto_refresh=True)

# --- Network (ESP32 AirLift co-processor is managed by Network) ---
network = Network(status_neopixel=board.NEOPIXEL, debug=False)

# --- Colors ---
US_COLOR = 0x00FF66
AMS_COLOR = 0xFF8000
SPB_COLOR = 0xFF0000


# --- Day-of-week + DST helpers (same rules as the S3 build) ---
def day_of_week(year, month, day):
    # Sakamoto's algorithm: 0=Sunday .. 6=Saturday
    t = (0, 3, 2, 5, 0, 3, 5, 1, 4, 6, 2, 4)
    if month < 3:
        year -= 1
    return (year + year // 4 - year // 100 + year // 400 + t[month - 1] + day) % 7


def nth_sunday(year, month, n):
    first_sunday = 1 + ((7 - day_of_week(year, month, 1)) % 7)
    return first_sunday + 7 * (n - 1)


def last_sunday(year, month, last_day):
    return last_day - day_of_week(year, month, last_day)


def us_dst(utc):
    y, m, d = utc[0], utc[1], utc[2]
    if m < 3 or m > 11:
        return False
    if 3 < m < 11:
        return True
    if m == 3:
        return d >= nth_sunday(y, 3, 2)
    return d < nth_sunday(y, 11, 1)


def eu_dst(utc):
    y, m, d = utc[0], utc[1], utc[2]
    if m < 3 or m > 10:
        return False
    if 3 < m < 10:
        return True
    if m == 3:
        return d >= last_sunday(y, 3, 31)
    return d < last_sunday(y, 10, 31)


def offset_hours(rule, utc):
    std, kind = rule
    if kind == "US":
        return std + (1 if us_dst(utc) else 0)
    if kind == "EU":
        return std + (1 if eu_dst(utc) else 0)
    return std


# (2-char code, color, (standard_offset_hours, dst_rule))
ZONES = (
    ("US", US_COLOR, (-5, "US")),
    ("AM", AMS_COLOR, (1, "EU")),
    ("SP", SPB_COLOR, (3, "NONE")),
)

font = bitmap_font.load_font("/tom-thumb.bdf")
group = displayio.Group()

# One row per zone (y is the vertical center of each line).
ROW_Y = (6, 16, 26)
rows = []
for (code, color, _), y in zip(ZONES, ROW_Y):
    lbl = Label(font, text="{} --:--".format(code), color=color)
    lbl.y = y
    group.append(lbl)
    rows.append(lbl)

display.root_group = group


def render():
    utc = time.localtime()  # RTC is held in UTC (see time sync below)
    epoch = time.mktime(utc)
    colon = ":" if (not BLINK or utc[5] % 2) else " "
    for (code, color, rule), lbl in zip(ZONES, rows):
        local = time.localtime(epoch + offset_hours(rule, utc) * 3600)
        lbl.text = "{} {:02d}{}{:02d}".format(code, local[3], colon, local[4])
        bbw = lbl.bounding_box[2]
        lbl.x = max(0, (width - bbw) // 2)
        if DEBUG:
            print(code, lbl.text)


last_sync = None
while True:
    if last_sync is None or time.monotonic() > last_sync + 3600:
        try:
            network.get_local_time("Etc/UTC")  # set the RTC to UTC
            last_sync = time.monotonic()
        except Exception as err:  # keep running on transient network errors
            print("time sync error:", err)
    render()
    gc.collect()
    time.sleep(0.5)
