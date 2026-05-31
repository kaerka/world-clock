# Raspberry Pi 4 matrix host setup

Step-by-step bring-up for a **Raspberry Pi 4 + Adafruit RGB Matrix HAT** driving the
6× (16×32) HUB75 panel build, going from a fresh Raspberry Pi OS install to a
flicker-free, auto-starting display.

These were captured live while setting up `matrixpi-mini-2` and are verified to work on:

- **Board:** Raspberry Pi 4 Model B
- **OS:** Raspberry Pi OS (Bookworm, 64-bit), Python 3.11
- **HAT:** Adafruit RGB Matrix HAT (`adafruit-hat` mapping)
- **Panels:** 6× 16×32 HUB75, wired in the serpentine chain documented in [`README.md`](README.md)

> Part A (host bring-up) is **reusable for any matrix app on this Pi** — including the
> planned MQTT display. Part B deploys the world clock specifically.

---

## Assumptions / starting point

- Raspberry Pi OS flashed, SSH enabled, on the network, time syncing via
  `systemd-timesyncd` (no hardware RTC is required — verify with `timedatectl`).
- The user (`kaerka`) is in the `gpio`, `video`, and `sudo` groups (the default `pi`/admin
  user already is). Confirm with `id`.
- Repos are cloned into the home directory:
  ```bash
  git clone https://github.com/hzeller/rpi-rgb-led-matrix.git ~/rpi-rgb-led-matrix
  git clone https://github.com/kaerka/world-clock.git ~/world-clock
  ```
- Set the local timezone once (drives the "local" zone if you add one, and logs):
  ```bash
  sudo timedatectl set-timezone America/New_York
  ```

---

## Part A — Matrix host bring-up (reusable)

### A1. System packages (apt)

Bookworm Python is **PEP-668 "externally managed"**, so install library packages with
`apt` where possible. Pillow from apt (9.4.0) is what we use for rendering.

```bash
sudo apt-get update
sudo apt-get install -y python3-pil python3-dev cython3 build-essential python3-pip
```

### A2. Build + install the rgbmatrix Python bindings

This fork of `rpi-rgb-led-matrix` builds with **scikit-build-core / CMake** via
`pip install .`. Installing system-wide (with `--break-system-packages`, again because of
PEP-668) puts `rgbmatrix` in the system `dist-packages` so the **root** systemd service can
import it. The build compiles the whole C++ library and takes a few minutes.

```bash
cd ~/rpi-rgb-led-matrix
sudo pip install . --break-system-packages
```

Verify it imports under the system interpreter that the service will use:

```bash
sudo python3 -c "import rgbmatrix, PIL; from rgbmatrix import RGBMatrix, RGBMatrixOptions; \
print('rgbmatrix OK ->', rgbmatrix.__file__, '| Pillow', PIL.__version__)"
# -> rgbmatrix OK -> /usr/local/lib/python3.11/dist-packages/rgbmatrix/__init__.py | Pillow 9.4.0
```

### A3. Flicker-free display tweaks

The HAT drives the panels with hardware PWM, which **conflicts with the onboard sound
driver** (`snd_bcm2835`). Blacklist it and turn off onboard audio. (`worldclock.py`
auto-detects the module and falls back to software pulsing, so the clock still runs before
this step — it just flickers. After this step + reboot it uses hardware PWM and is steady.)

```bash
# Blacklist the conflicting sound module
echo "blacklist snd_bcm2835" | sudo tee /etc/modprobe.d/rgb-matrix-blacklist.conf

# Turn off onboard audio (it reloads the module otherwise)
sudo sed -i 's/^dtparam=audio=on/dtparam=audio=off/' /boot/firmware/config.txt
grep -n "dtparam=audio" /boot/firmware/config.txt   # -> dtparam=audio=off
```

Pin the CPU governor to `performance` for a steadier refresh (persistent across reboots via
a tiny oneshot service):

```bash
sudo tee /etc/systemd/system/cpu-performance.service >/dev/null <<'EOF'
[Unit]
Description=Set CPU governor to performance (stable LED matrix refresh)
After=multi-user.target

[Service]
Type=oneshot
ExecStart=/bin/sh -c 'for c in /sys/devices/system/cpu/cpu*/cpufreq/scaling_governor; do echo performance > "$c"; done'
RemainAfterExit=yes

[Install]
WantedBy=multi-user.target
EOF
sudo systemctl enable --now cpu-performance.service
cat /sys/devices/system/cpu/cpu0/cpufreq/scaling_governor   # -> performance
```

**Optional** — dedicate CPU core 3 to the matrix refresh thread (the library suggests this
for a small quality bump). Append to the single line in `/boot/firmware/cmdline.txt`:

```bash
sudo sed -i 's/$/ isolcpus=3/' /boot/firmware/cmdline.txt   # only if not already present
```

### A4. Optional — hardware RTC on the HAT

The Adafruit RGB Matrix HAT has a footprint for a **DS1307 RTC** (battery-backed). If yours
is populated, you can have the Pi keep correct time across reboots **without a network** —
useful for the clock and for timestamping offline. This replicates what Adafruit's
`rgb-matrix.sh` installer does when you answer "yes" to the RTC prompt.

Confirm the chip is present first (responds at `0x68` on bus 1):

```bash
sudo apt-get install -y i2c-tools
sudo dtparam i2c_arm=on && sudo modprobe i2c-dev   # enable I2C for this boot to probe
sudo i2cdetect -y 1                                 # expect a device at 0x68
```

Enable it persistently:

```bash
# 1. Enable I2C + load the RTC overlay
sudo sed -i 's/^#dtparam=i2c_arm=on/dtparam=i2c_arm=on/' /boot/firmware/config.txt
grep -q '^dtoverlay=i2c-rtc,ds1307' /boot/firmware/config.txt || \
  sudo sed -i '/^dtparam=i2c_arm=on/a dtoverlay=i2c-rtc,ds1307' /boot/firmware/config.txt

# 2. Let the kernel read the RTC at boot (comment the systemd early-exit in hwclock-set)
sudo sed -i '/if \[ -e \/run\/systemd\/system \] ; then/,/^fi$/ s/^/#/' /lib/udev/hwclock-set

# 3. Remove the software clock stand-in
sudo apt-get -y remove fake-hwclock
sudo update-rc.d -f fake-hwclock remove 2>/dev/null || true
```

After the reboot in Part C, seed the chip once from the (NTP-synced) system clock and read
it back:

```bash
ls /dev/rtc0          # device node now exists
sudo hwclock -w       # write system time -> RTC
sudo hwclock -r       # read it back to confirm
i2cdetect -y 1        # 0x68 now shows as "UU" (claimed by the kernel rtc driver)
```

> If `i2cdetect` shows nothing at `0x68`, the RTC footprint isn't populated on your HAT —
> skip this section and rely on NTP (the default on `matrixpi-mini-2`).

---

## Part B — Deploy the world clock

The app lives in `~/world-clock/rpi4-adafruit-hat/` and ships its own
[`worldclock.service`](worldclock.service). It needs no extra dependencies beyond Part A
(Pillow + rgbmatrix; timezones use the stdlib `zoneinfo`).

Quick manual smoke test (Ctrl-C to stop; flickers until after the reboot in A3):

```bash
cd ~/world-clock/rpi4-adafruit-hat
sudo python3 worldclock.py
```

Install + enable the service (symlinked so edits in the repo stay authoritative):

```bash
sudo systemctl enable /home/kaerka/world-clock/rpi4-adafruit-hat/worldclock.service
sudo systemctl daemon-reload
sudo systemctl start worldclock.service
sudo systemctl status worldclock.service --no-pager
```

---

## Part C — Reboot & verify

A reboot is required for the sound blacklist + `audio=off` to take effect (this is what
makes the display flicker-free).

```bash
sudo reboot
```

After it comes back:

```bash
lsmod | grep snd_bcm2835            # -> (no output: module is blacklisted)
systemctl is-enabled worldclock     # -> enabled
systemctl status worldclock         # -> active (running)
journalctl -u worldclock -b --no-pager | tail   # boot logs for the clock
```

The panel should show the world clock, steady (no flicker), within a few seconds of boot.

---

## Troubleshooting

| Symptom | Cause / fix |
| --- | --- |
| `ModuleNotFoundError: rgbmatrix` under `sudo` | Bindings not installed system-wide. Re-run A2 with `sudo pip install . --break-system-packages`. |
| Display flickers | Sound module still loaded. Check `lsmod \| grep snd_bcm2835`; confirm A3 and **reboot**. |
| `pip install` refuses (externally-managed) | Add `--break-system-packages` (intended here for a system-wide install). |
| Panels lit but layout scrambled | Physical chain order differs. Re-run `panel_probe.py chain` and update `CELL_TO_CHAIN` in `worldclock.py` (see [`README.md`](README.md)). |
| Wrong time / zone | `timedatectl` — confirm `System clock synchronized: yes` and the right timezone. |
