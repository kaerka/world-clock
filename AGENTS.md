# AGENTS.md — World Clock

Guidance for AI agents working in this repo. Read this first.

## What this is

A multi-timezone "world clock" for HUB75 RGB LED matrix panels, built for three
different hardware targets. Each lives in its own folder and is a thin
display/render shim around the same idea (three zones: US Eastern, Amsterdam,
Saint Petersburg/Moscow).

| Folder | Hardware | Stack | Status |
| --- | --- | --- | --- |
| `64x64-matrixportal-s3/` | MatrixPortal S3, 64×64 | CircuitPython (`displayio`) | working |
| `32x32-matrixportal-m4/` | MatrixPortal M4, 32×32 | CircuitPython (`displayio`) | working |
| `rpi4-adafruit-hat/` | **Pi 4 + Adafruit HAT, 6×16×32** | Python + hzeller lib + Pillow | **working, deployed** |
| `mqtt-display/` | same Pi/matrix | reuses the Pi build + `paho-mqtt` | **optional add-on** (see below) |
| _(future)_ Spectra wall | 2× Pi4 + Electrodragon, 18×64×64 | Python + hzeller lib | planned — see below |

The two CircuitPython builds run on-device (CIRCUITPY drive) and hand-roll DST
math (no tz database). The Pi build is the active/main target — see below.

## Future target: Spectra wall (read this when moving to that system)

This repo's Pi build was the **prototype** for a much larger 18-panel wall
("Spectra": 2× Pi4 + Electrodragon boards, dedicated PSU, stratum-1 NTP). The
long-term plan is to **merge the world-clock into the setup already running
there**, then add sensor/metric dashboards. The full carry-over context —
hardware/stack specifics, the Electrodragon vs. Adafruit-HAT differences, the
NPU-controller plan (accelerated OpenCV for image splitting), and the open
design questions — lives in **`ROADMAP.md` → "Port 3 — Spectra wall"**. Start
there. (This pointer exists on purpose: context didn't carry over when we moved
from prototyping to the mini build, so it's written down for the next move.)

## Pi build (`rpi4-adafruit-hat/`) — the important one

This is the build you'll usually be asked to work on. It runs on the Pis this
repo is checked out on (`matrixpi-mini-1`, `matrixpi-mini-2`). For a fresh Pi,
the full, verified bring-up (apt deps, building the `rgbmatrix` bindings, sound
blacklist, governor, optional HAT RTC) is in
[`rpi4-adafruit-hat/SETUP.md`](rpi4-adafruit-hat/SETUP.md).

### Architecture
- `worldclock.py` renders a logical **64×48** image with **Pillow**, then
  `remap_to_ribbon()` folds it onto the physical **192×16** single chain and
  pushes it via hzeller `rpi-rgb-led-matrix`. There is **no library
  pixel-mapper** — we remap in Python.
- Time zones use **`zoneinfo`** (full OS tz DB, DST is automatic); the system
  clock is NTP-synced (`systemd-timesyncd`), and there's a DS1307 RTC. Do not
  re-add the hand-rolled DST code from the CircuitPython builds.
- `panel_probe.py` is a hardware diagnostic (chain order + grid/mapper checks).

### Hardware facts (confirmed on hardware — don't re-derive)
- 6 panels, `rows=16 cols=32 chain_length=6 parallel=1`,
  `hardware_mapping="adafruit-hat"`, `gpio_slowdown=5`.
- Physical layout is 2 wide × 3 tall. Confirmed chain→cell mapping
  (`CELL_TO_CHAIN`): chain runs bottom→middle→top, left→right, all panels
  upright → `chain = (2 - grid_row) * 2 + grid_col`. No internal pixel flips.
- Sound (`snd_bcm2835`) is **blacklisted** + `dtparam=audio=off`, so the HAT
  uses **hardware PWM** (flicker-free). `worldclock.py` auto-detects the module
  and falls back to software pulsing if it ever reloads (so it always starts).
- CPU governor is set to `performance`. `sudo` is passwordless here.

### Running & operating
```bash
# The matrix needs /dev/mem, so everything runs under sudo.
sudo systemctl status worldclock      # the deployed service (enabled on boot)
sudo systemctl restart worldclock     # after editing worldclock.py
journalctl -u worldclock -b           # logs (a lone "isolcpus" note is harmless)

# Manual run / mapping test (STOP the service first — only one owner of GPIO):
sudo systemctl stop worldclock
cd ~/world-clock/rpi4-adafruit-hat
sudo python3 worldclock.py            # live clock
sudo python3 worldclock.py test       # corner test pattern (TL/TR/BL/BR)
sudo python3 panel_probe.py chain     # 6 colored, numbered blocks
```
The systemd unit lives in the repo (`worldclock.service`) and is installed to
`/etc/systemd/system/`.

### Editing the display
- Colors: `TITLE_COLOR`, `LABEL_COLOR`, `BORDER_COLOR`, per-zone color in `ZONES`.
- Layout: `TITLE_Y`, `ZONE_TOP`, `LABEL_DY`, `TIME_DY` (canvas is only 48px tall).
- Fonts: `tom-thumb.bdf` (title/labels), `5x7.bdf` (times). BDF is loaded via
  Pillow's `BdfFontFile` at runtime; the generated `*.pil`/`*.pbm` are gitignored.
- Verify a layout offline before deploying (no hardware needed):
  ```bash
  python3 -c "import worldclock as wc; from datetime import datetime; \
    s=wc.load_bdf('tom-thumb.bdf'); b=wc.load_bdf('5x7.bdf'); \
    wc.render_clock(s,b,datetime(2026,1,1,tzinfo=wc.UTC)).resize((640,480)).save('/tmp/p.png')"
  ```

## Optional add-on: MQTT display (`mqtt-display/`)

**Not part of the clock** — a separate app kept in this repo only to reuse the Pi
build's rendering. `display.py` imports `worldclock` (geometry, `build_matrix`,
`remap_to_ribbon`, `render_clock`, `load_bdf`) and runs one loop: shows MQTT
messages (plain text or JSON `{text,ttl,color}`, word-wrapped, vertical-scrolled)
and **falls back to the clock when idle** (`idle_seconds`, default 30). The
dependency is one-way (this imports the clock, never the reverse), so the clock
is unaffected if you ignore this folder.

- Because there's **one GPIO owner**, this app *supersedes* `worldclock.service`
  (the clock becomes its idle screen). Deploy = disable `worldclock`, enable
  `mqtt-display.service`; revert = the opposite. Don't run both.
- Config is `config.local.json` (gitignored; copy `config.example.json`). Broker
  creds live there, never in git.
- Extra dep only for this app: `sudo apt-get install -y python3-paho-mqtt`
  (Debian ships 1.6.x → the **v1** paho callback API).
- This is the piece slated to move to the **Spectra wall** next (more panels,
  local NTP, no RTC); see `ROADMAP.md`.

## Gotchas (these bit us — avoid them)
- **`pkill -f` self-match:** `pkill -f worldclock.py` also matches the shell
  command running it (which contains that string), killing your own session.
  Kill by **PID**, or stop the **service**, instead.
- **One GPIO owner:** the service and a manual run can't both drive the matrix.
  `systemctl stop worldclock` before manual runs, restart it after.
- **Reboots drop the SSH/agent session.** Make any boot-affecting change
  self-healing/unattended before rebooting (the service is enabled + the sound
  auto-detect makes boot safe). Verify after with `journalctl -u worldclock -b`.

## Conventions
- Don't commit secrets: `settings.toml`, `secrets.py`, `lib/` are gitignored.
- Keep the SPDX/Adafruit attribution header at the top of each `code.py` /
  `worldclock.py` (this project derives from Adafruit's Metro Matrix Clock).
- Update `ROADMAP.md` status and the relevant `README.md` when you change a build.
- Only create git commits when explicitly asked.
