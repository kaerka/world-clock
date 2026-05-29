# SPDX-FileCopyrightText: 2020 John Park for Adafruit Industries
#
# SPDX-License-Identifier: MIT
#
# World clock for MatrixPortal S3 + 64x64 RGB matrix.
# Shows three time zones under a title:
#   US Eastern            (EST/EDT, DST aware)
#   Amsterdam, NL         (Central Europe, DST aware)
#   Saint Petersburg, RU  (Moscow time, no DST)

import time
import gc
import board
import displayio
import framebufferio
import rgbmatrix
import wifi
import adafruit_connection_manager
import adafruit_ntp
from os import getenv
from adafruit_bitmap_font import bitmap_font
from adafruit_display_text.label import Label

BLINK = True
DEBUG = False

gc.collect()
gc.enable()

ssid = getenv("CIRCUITPY_WIFI_SSID")
password = getenv("CIRCUITPY_WIFI_PASSWORD")
if None in [ssid, password]:
    raise RuntimeError(
        "WiFi settings are kept in settings.toml; add CIRCUITPY_WIFI_SSID "
        "and CIRCUITPY_WIFI_PASSWORD there."
    )

# --- Display setup (single 64x64 panel) ---
bit_depth = 4
base_width = 64
base_height = 64
chain_across = 1
tile_down = 1
serpentine = False

width = base_width * chain_across
height = base_height * tile_down

addr_pins = [
    board.MTX_ADDRA,
    board.MTX_ADDRB,
    board.MTX_ADDRC,
    board.MTX_ADDRD,
    board.MTX_ADDRE,
]
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

# --- Network / time source (UTC, auto re-sync hourly) ---
print("Connecting to {}...".format(ssid))
wifi.radio.connect(ssid, password)
pool = adafruit_connection_manager.get_radio_socketpool(wifi.radio)
ntp = adafruit_ntp.NTP(pool, tz_offset=0, cache_seconds=3600)

# --- Colors ---
TITLE_COLOR = 0xFFAA00
LABEL_COLOR = 0x3060FF
ATL_COLOR = 0x00FF66
AMS_COLOR = 0xFF8000
SPB_COLOR = 0xFF0000


# --- Day-of-week + DST helpers (CircuitPython has no timezone database) ---
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
    # 2nd Sunday of March through 1st Sunday of November (compared by date)
    y, m, d = utc[0], utc[1], utc[2]
    if m < 3 or m > 11:
        return False
    if 3 < m < 11:
        return True
    if m == 3:
        return d >= nth_sunday(y, 3, 2)
    return d < nth_sunday(y, 11, 1)


def eu_dst(utc):
    # Last Sunday of March through last Sunday of October (compared by date)
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


# Zone definition: (city text, color, (standard_offset_hours, dst_rule))
ZONES = (
    ("US EAST", ATL_COLOR, (-5, "US")),
    ("AMSTERDAM", AMS_COLOR, (1, "EU")),
    ("SPB", SPB_COLOR, (3, "NONE")),
)

font = bitmap_font.load_font("/5x7.bdf")
group = displayio.Group()

# 1px white-grey border around the whole panel
BORDER_COLOR = 0x303030
border_bitmap = displayio.Bitmap(width, height, 2)
border_palette = displayio.Palette(2)
border_palette[0] = 0x000000
border_palette[1] = BORDER_COLOR
for bx in range(width):
    border_bitmap[bx, 0] = 1
    border_bitmap[bx, height - 1] = 1
for by in range(height):
    border_bitmap[0, by] = 1
    border_bitmap[width - 1, by] = 1
group.append(displayio.TileGrid(border_bitmap, pixel_shader=border_palette))


def make_label(text, color, y):
    lbl = Label(font, text=text, color=color)
    _, _, bbw, _ = lbl.bounding_box
    lbl.x = max(0, (width - bbw) // 2)
    lbl.y = y
    group.append(lbl)
    return lbl


make_label("WORLD CLOCK", TITLE_COLOR, 8)

# (city-label center y, time center y) for each block
BLOCK_Y = ((16, 24), (33, 41), (50, 58))

time_labels = []
for (city, color, _), (label_y, time_y) in zip(ZONES, BLOCK_Y):
    make_label(city, LABEL_COLOR, label_y)
    time_labels.append(make_label("00:00", color, time_y))

display.root_group = group


def update():
    utc = ntp.datetime
    epoch_utc = time.mktime(utc)
    colon = ":" if (not BLINK or utc[5] % 2) else " "
    for (city, color, rule), tlabel in zip(ZONES, time_labels):
        local = time.localtime(epoch_utc + offset_hours(rule, utc) * 3600)
        tlabel.text = "{:02d}{}{:02d}".format(local[3], colon, local[4])
        _, _, bbw, _ = tlabel.bounding_box
        tlabel.x = max(0, (width - bbw) // 2)
        if DEBUG:
            print(city, tlabel.text)


while True:
    try:
        update()
    except Exception as err:  # keep the clock alive through transient errors
        print("update error:", err)
    gc.collect()
    time.sleep(0.5)
