# World Clock — Roadmap & Future Tasks

Running notes for porting the clock to more hardware and adding features.

## Platform status

| Platform | Display | Status |
| --- | --- | --- |
| MatrixPortal S3 | 64x64 (single panel) | ✅ Working |
| MatrixPortal M4 | 32x32 (single panel) | ✅ Working (deployed, tom-thumb font) |
| Raspberry Pi 4 + Adafruit RGB Matrix HAT | 6 × 16x32 panels (64x48, 2w×3t) | ✅ Working — live on hardware (`rpi4-adafruit-hat/`); mapping confirmed via probe |
| Spectra (full wall) | 18 × 64x64 (2× Pi4, Electrodragon, 3×3 each) | 🔭 Future — merge target for world-clock + sensor metrics |
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

## Port 2 — Raspberry Pi 4 + Adafruit RGB Matrix HAT (6 × 16x32) ✅ WORKING

Live on hardware in `rpi4-adafruit-hat/` (`worldclock.py`, `panel_probe.py`,
README, `tom-thumb.bdf` + `5x7.bdf`). On this Pi the hzeller bindings are
already installed system-wide and the OS clock is NTP-synced.

- [x] **Stack swapped:** hzeller `rpi-rgb-led-matrix` instead of `displayio`.
- [x] **DST deleted:** zones are now `zoneinfo` (`America/New_York`,
      `Europe/Amsterdam`, `Europe/Moscow`); no hand-rolled Sakamoto math.
- [x] **Arrangement:** 64x48 (2 wide × 3 tall), `chain_length=6`, `parallel=1`,
      `hardware_mapping="adafruit-hat"`, `gpio_slowdown=5`.
- [x] **Rendering:** Pillow draws a logical 64x48 image; `remap_to_ribbon()`
      folds it onto the physical 192x16 chain (stock mappers can't express this
      layout, so we remap in Python — also sets up weather/icons later).
- [x] **Mapping confirmed on hardware** via `panel_probe.py` + `worldclock.py
      test`: chain runs bottom→middle→top, left→right, all panels upright;
      `chain = (2 - grid_row) * 2 + grid_col`. (Physical input is the TR panel;
      logical order is reversed vs. physical, hence the reversal in the map.)
- [x] **First light verified** (dedicated 5V/300W PSU; all 6 panels, no dead
      pixels).

Polish (done):

- [x] **Hardware PWM (flicker-free):** `snd_bcm2835` blacklisted + `dtparam=audio=off`.
      `worldclock.py` auto-detects the module (`FORCE_DISABLE_HW_PULSE` override),
      so it always starts. CPU governor set to `performance`.
- [x] **Autostart:** `worldclock.service` (in the build folder) installed +
      enabled; verified it auto-starts on boot.
- [x] **Title + 1px border restored** (S3 look), tom-thumb title/labels over 5x7
      times to fit the 48px height.

Still open:

- [ ] (Optional) `isolcpus=3` in cmdline for a steadier refresh.
- [ ] Layer in features — weather, brightness schedule, alternate fonts, etc.

Original notes:

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

## Port 3 — Spectra wall (18 × 64x64, 2× Pi4 + Electrodragon) 🔭 FUTURE

The big wall-screen. The 6-panel HAT build (Port 2) was literally its prototype.
Long-term home for the world-clock *and* live sensor/metric dashboards. The goal
is to **merge the world-clock into the display setup already running there**, not
replace it.

Hardware / stack notes (carry-over context — capture before it's lost):
- **18 panels, 64x64**, split across **2× Pi4**, each Pi driving **9 panels via
  Electrodragon** boards as **3 chains × 3** (`parallel=3, chain_length=3`).
  Reference config already exists: `led-matrix-display/disp-multi-v3-9panel.py`
  (`rows=64, cols=64, parallel=3, chain_length=3, pixel_mapper_config="Rotate:270"`).
- **Electrodragon ≠ Adafruit HAT:** use `hardware_mapping="regular"` (not
  "adafruit-hat"), and 3 parallel chains instead of the HAT's single chain.
- **Dedicated PSU**; a **stratum-1 NTP server** lives on the wall's network —
  use it for tight, near-simultaneous frame flips across both Pis (no wired genlock).
- **Per-Pi remap puzzle** will recur (same kind of `CELL_TO_CHAIN` ordering we
  solved on the HAT build), though `Rotate:270` + parallel/chain may cover most.

Controller (replaces the removed Radxa NIO):
- The NIO orchestrated the two display Pis and was repurposed for another project.
  Needs a replacement SBC. It does **not** drive panels (the 2 Pis do), so no
  GPIO/HUB75 requirement — it's an orchestrator/content server.
- **Target board:** a **Pi5 or similar Radxa**, ideally **with an NPU**, because
  it should also run **accelerated OpenCV for video/image splitting** (slicing
  source frames into the two per-Pi / 18-panel regions).
  - Caveat to verify: OpenCV doesn't transparently use most NPUs. Acceleration
    is usually vendor-specific — **Rockchip RKNN** on Radxa boards, or a **Hailo**
    module on the Pi5 — with GPU/`cv2.UMat`(OpenCL) as a fallback. Confirm the
    chosen board's accel path before committing.
- **Wants:** wired Gigabit Ethernet, decent CPU/RAM (it may also host the metrics
  pipeline + a config/web UI), stable Linux, good NTP client behavior.

Open question:
- [ ] **Unified canvas vs. independent regions:** one logical 18-panel image
      (controller splits into two per-Pi halves) vs. two independent displays.
      Also: dedicated controller vs. promoting one display Pi to "primary".

Work items (rough order):
- [ ] Pick + provision the controller SBC (NPU board for accelerated OpenCV).
- [ ] Decide the unified-vs-independent model (open question above).
- [ ] Reuse the world-clock core; render to the wall geometry (per-Pi remap).
- [ ] Stand up the sensor/metrics pipeline (ingest → store → render).

## Sibling hardware (same panels, other roles)

The 6-panel 16×32 setup exists in **two** identical "mini-Spectra" units:

- **Mini-Spectra A** (`matrixpi-mini-1`) — runs the clock (`rpi4-adafruit-hat/`).
- **Mini-Spectra B** (`matrixpi-mini-2`) — a second identical unit (full
  bring-up done per `SETUP.md`, incl. HAT RTC). Intended as the **output device
  for a voice-to-text system with automatic translation** (a separate project) —
  a live caption/translation display fed text instead of `zoneinfo`.

### MQTT display — built ✅ (`mqtt-display/`)

The receiver/display side of that plan now exists: a Pillow + hzeller app that
subscribes to MQTT and shows messages (plain text or JSON `{text,ttl,color}`,
word-wrapped, vertical-scrolled), **falling back to the clock when idle**. Built
and tested end-to-end on `matrixpi-mini-2` against a local mosquitto broker
(clock → message → auto-revert), then that mini was reverted to the plain clock.

**Next: graduate it to the Spectra wall**, not a mini — the mini's 16×32 pixel
pitch is coarse for running text. Spectra has many more panels (more legible
text, room for layout) and a **local NTP server** (so time stays correct without
the HAT RTC the minis use — Spectra has **no RTC**). Carry-over notes:
- Re-derive the canvas/remap for the wall geometry (the mini's `CELL_TO_CHAIN`
  is specific to its 2×3 serpentine).
- Point `config.local.json` at the real broker (Whisper publisher); keep creds
  out of git (already gitignored).
- Same one-GPIO-owner rule: on Spectra it would supersede/merge with whatever
  clock/dashboard process owns the wall.

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
- [ ] **Sensor/metric dashboards (Spectra, longer-term):** display live metric
      data from sensor devices — ingest + aggregate (likely on the controller),
      then render tiles/graphs to the wall alongside or instead of the clock.

## Suggested order

1. Extract `worldclock_core` from the S3 `code.py`.
2. ~~M4 port~~ ✅ done (used the `Network` + `get_local_time("Etc/UTC")` approach + 32x32 layout).
3. ~~RPi4 port~~ ✅ done (`rpi4-adafruit-hat/`; `zoneinfo` + Pillow + Python remap).
4. Layer in features, starting on the Pi (weather, brightness, fonts).
5. Spectra wall: pick the NPU controller, merge the world-clock in, then sensor metrics.
