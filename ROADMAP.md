# World Clock — Roadmap & Future Tasks

Running notes for porting the clock to more hardware and adding features.

## Platform status

| Platform | Display | Status |
| --- | --- | --- |
| MatrixPortal S3 | 64x64 (single panel) | ✅ Working |
| MatrixPortal M4 | 32x32 (single panel) | ✅ Working (deployed, tom-thumb font) |
| Raspberry Pi 4 + Adafruit RGB Matrix HAT | 6 × 32x16 panels | 🔜 Hardware built, PSU borrowed (not powered yet) — **the main next build** |
| ~~Pimoroni Interstate 75~~ | — | ❌ Dropped — original (non-W) board has no onboard WiFi |

## Highest-leverage first step: extract a shared core

Before porting, split the project so the logic is written once:

- [ ] Create `worldclock_core` (pure logic, no hardware imports):
  - `ZONES` definitions, color constants
  - `offset_hours()`, `us_dst()`, `eu_dst()`, `day_of_week()`, `nth_sunday()`, `last_sunday()`
- [ ] Keep each platform's `code.py` as a thin display + render-loop shim around the core.
- [ ] (Bonus) The core is plain Python, so it can be unit-tested on a desktop.

This turns "port the whole app 3×" into "write small display shims around one brain."

## Port 1 — MatrixPortal M4 (32x32) ✅ DEPLOYED

Done and running on the device. How it actually went:

- [x] **Networking:** the M4 has **no native `wifi`** (SAMD51 + ESP32 "AirLift"
      co-processor). Instead of `adafruit_esp32spi` + `adafruit_ntp` (NTP/UDP is
      unreliable over the AirLift), used `adafruit_matrixportal.network.Network`
      (auto-manages the ESP32) + `network.get_local_time("Etc/UTC")` to set the RTC
      to UTC, then the **same DST/offset math** as the S3 build. Requires
      `ADAFRUIT_AIO_USERNAME` / `ADAFRUIT_AIO_KEY` in `settings.toml`.
- [x] **CircuitPython version gotcha:** the M4 runs **CircuitPython 9.2.6**; the S3
      runs 10.2.1. `.mpy` bytecode is NOT cross-compatible, so deployed the **9.x M4
      libraries from `matrixportal-m4/code-backup/lib`**, not the S3's 10.x `lib/`.
- [x] **Address pins:** 4 pins (A–D), no `MTX_ADDRE`, no E-line jumper.
- [x] **Dimensions:** `base_width = 32`, `base_height = 32`.
- [x] **Layout:** 3 rows, one per zone, `CODE HH:MM` (`US` / `AM` / `SP`),
      color-coded. Only ~8 chars fit at 32px wide. Font = `tom-thumb.bdf`
      (cleaner than `4x6` here). `bit_depth = 4`.
- [x] Lives in `32x32-matrixportal-m4/`.

Known / cosmetic:
- Colons render as a single pixel in `tom-thumb` at this size — fine for now.
- This M4 is the original ~4-year-old board and is flaky (CircuitPython upgrade
  failed). Likely its last project — don't sink time into upgrading it.

## Port 2 — Raspberry Pi 4 + Adafruit RGB Matrix HAT (6 × 32x16)

- [ ] **Different stack entirely:** not CircuitPython. Use
      [hzeller `rpi-rgb-led-matrix`](https://github.com/hzeller/rpi-rgb-led-matrix)
      Python bindings + Pillow for text/graphics. `displayio` / `rgbmatrix` /
      `framebufferio` / BDF loader all go away.
- [ ] **Panel arrangement (decide):** 6 × 32x16 (32 wide × 16 tall each) →
      e.g. 96x32 (3 wide × 2 tall), 192x16 (chain of 6), or 64x48 (2 wide × 3 tall).
      Configure via the library's `--led-rows=16`, `--led-cols=32`, `--led-chain`,
      `--led-parallel`. Note the Adafruit HAT is a single chain (1 parallel channel),
      and 1/8-scan 32x16 panels may need `--led-row-addr-type` / multiplexing flags —
      verify panel scan rate when wiring up.
- [ ] **Time/DST gets much simpler:** use Python `zoneinfo` (full timezone DB) and
      **delete the hand-rolled DST code** — `ZoneInfo("America/New_York")`,
      `ZoneInfo("Europe/Amsterdam")`, `ZoneInfo("Europe/Moscow")`, etc. System NTP
      handles sync.
- [ ] Best home for the heavy feature set (more RAM/CPU, threads, `requests`, Pillow).
- [ ] **Hardware reminder:** PSU is currently borrowed — needs ample amperage for
      6 panels before powering on.
- [ ] Lives in a new `rpi4-adafruit-hat/` (or similar) folder.

## DST accuracy note (CircuitPython boards)

The hand-rolled DST currently switches by date, so on the single changeover day each
spring/fall a zone can be off by up to an hour for part of the day. Could be made
hour-exact, but the Pi sidesteps this entirely via `zoneinfo`.

## Feature ideas (mostly for the Pi, some on S3/M4)

- [ ] Per-city weather (temp/conditions/icon) — `OPENWEATHER_TOKEN` already in settings.
- [ ] Auto-brightness / night dimming (schedule-based, or ambient light sensor).
- [ ] Sunrise/sunset, date / day-of-week, optional seconds.
- [ ] "Business hours" coloring per zone.
- [ ] Custom NTP server (the `NTP_SERVER` hook is already templated — one-liner to wire in).
- [ ] Scrolling headlines / extra info rows.
- [ ] Runtime config without reflashing: `settings.toml` on the boards; a config file or
      small web UI on the Pi.
- [ ] **Custom BDF font** tuned for big, low-res pixels (fork `tom-thumb`; e.g. a 4×7
      with a heavier stroke and a full-height 2-pixel colon, since tom-thumb's colon
      is a single pixel at this size). A preview tool exists at
      `matrixportal-m4/fonts/_preview.py` — renders any BDF as LED dots at the real
      32px width for side-by-side comparison.

## Suggested order

1. Extract `worldclock_core` from the S3 `code.py`.
2. ~~M4 port~~ ✅ done (used the `Network` + `get_local_time("Etc/UTC")` approach + 32x32 layout).
3. RPi4 port (new frontend on the same zone definitions; `zoneinfo` replaces DST helpers).
4. Layer in features, starting on the Pi.
