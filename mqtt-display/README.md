# Matrix MQTT display

> **Optional add-on — not required for the world clock.** This is a separate
> application that happens to live in the `world-clock` repo so it can reuse a
> clock build's rendering and panel geometry. The clocks run perfectly well on
> their own; **if you don't want MQTT, skip this folder entirely** — nothing in
> the clock imports from here, and you don't need `paho-mqtt` or a broker. This
> app *does* import the clock (for its idle screen), not the other way around.

A single program that owns the LED matrix and shows:

- **Idle:** the world clock (its rendering and geometry are imported directly, so
  the clock is identical to the standalone version).
- **On a message:** text received over **MQTT** — word-wrapped to the panel,
  scrolling vertically if it's too tall. After `idle_seconds` with no newer
  message, it auto-reverts to the clock.

Only one process can drive the matrix (it holds the GPIO/PWM), so this app
**supersedes** the clock service — the clock simply becomes its idle screen.

## Backends

The `backend` config key picks which clock build (hardware + geometry + fonts)
to reuse:

| `backend` | Hardware | Canvas | Reuses |
| --- | --- | --- | --- |
| `"spectra"` *(default)* | `matrixpi-upper`: Pi4 + Electrodragon, 9×(64×64) | 192×192, TTF fonts, no remap | [`../rpi4-electrodragon/spectra_clock.py`](../rpi4-electrodragon/) |
| `"adafruit-hat"` | the minis: Pi4 + Adafruit HAT, 6×(16×32) | 64×48, BDF fonts, serpentine remap | [`../rpi4-adafruit-hat/worldclock.py`](../rpi4-adafruit-hat/) |

## Install the dependency

Host setup (rgbmatrix bindings, Pillow, sound blacklist, governor) is per-box —
see [`../rpi4-adafruit-hat/SETUP.md`](../rpi4-adafruit-hat/SETUP.md) (minis) or the
notes in [`../rpi4-electrodragon/README.md`](../rpi4-electrodragon/) (Spectra). This
app only adds `paho-mqtt`:

```bash
sudo apt-get install -y python3-paho-mqtt      # Debian ships 1.6.x (v1 callback API)
```

The code also works with `paho-mqtt` 2.x (it selects the v1 callback API
automatically), so a `pip` install is fine too.

> **On `matrixpi-upper` (Spectra):** `rgbmatrix` is not installed system-wide —
> it's imported from the prebuilt hzeller build via `PYTHONPATH`
> (`/home/kaerka/led-matrix-display/rpi-rgb-led-matrix/bindings/python`). The
> service unit and `selftest`/run examples below set this for you.

## Configure

Copy the example and edit it (the real file is gitignored):

```bash
cp config.example.json config.local.json
$EDITOR config.local.json
```

| Key | Meaning |
| --- | --- |
| `backend` | `"spectra"` (this wall) or `"adafruit-hat"` (the minis) |
| `broker` / `port` | MQTT broker address and port (1883 plain, 8883 TLS) |
| `username` / `password` | Optional auth (`null` for anonymous) |
| `tls` | `true` to connect over TLS |
| `topics` | List of topics to subscribe to |
| `idle_seconds` | Default time a message stays before falling back to the clock |
| `text_color` | Default `[r,g,b]` for message text |
| `text_font_size` | **spectra:** DejaVuSans-Bold px size for messages (default 18) |
| `font` | **adafruit-hat:** `"tom-thumb"` (dense) or `"5x7"` (more legible) |
| `fps` | Render loop rate (smooth scroll); 20 is plenty |
| `scroll_px_per_sec` | Vertical scroll speed for overflowing messages |

Secrets stay out of git: `config.local.json` matches the repo `.gitignore`.

## Message format

Publishers may send **plain UTF-8 text**:

```bash
mosquitto_pub -h <broker> -t whisper/transcript -m "the quick brown fox"
```

…or **JSON** to override per-message behavior:

```bash
mosquitto_pub -h <broker> -t whisper/transcript \
  -m '{"text": "BUILD PASSED", "ttl": 10, "color": [0,255,80]}'
```

| JSON field | Default | Notes |
| --- | --- | --- |
| `text` (or `message`) | — | the string to show |
| `ttl` | `idle_seconds` | seconds to keep showing before reverting to clock |
| `color` | `text_color` | `[r,g,b]` |

Anything that isn't a JSON object (a bare string, a number, etc.) is shown as
plain text.

## Verify without hardware

`selftest` renders the idle clock and a sample (wrapped) message to PNGs — no
matrix, broker, or `paho-mqtt` required. Handy for checking layout/fonts:

```bash
PYTHONPATH=/home/kaerka/led-matrix-display/rpi-rgb-led-matrix/bindings/python \
  python3 display.py selftest        # -> /tmp/mqtt_clock.png, /tmp/mqtt_message.png
```

## Run

The matrix driver mmaps `/dev/mem`, so it must run as **real root**.

```bash
# Spectra (matrixpi-upper): set PYTHONPATH for the prebuilt bindings
sudo PYTHONPATH=/home/kaerka/led-matrix-display/rpi-rgb-led-matrix/bindings/python \
  python3 display.py

# minis: rgbmatrix is installed system-wide, so just
sudo python3 display.py
```

Deploy as a service (replacing the clock service — free the matrix first):

```bash
# Spectra:
sudo systemctl disable --now spectra-clock.service
# minis:
# sudo systemctl disable --now worldclock.service

sudo systemctl enable /home/kaerka/world-clock/mqtt-display/mqtt-display.service
sudo systemctl daemon-reload
sudo systemctl start mqtt-display.service
journalctl -u mqtt-display.service -b
```

To go back to the plain clock, reverse it: disable `mqtt-display`, re-enable the
clock service for that box.

## Notes

- `paho-mqtt` 1.6.x (apt) and 2.x (pip) are both supported.
- The MQTT client reconnects automatically (1–30 s backoff), so a broker restart
  or network blip is handled without dropping the clock.
- Newest message wins: a new publish replaces whatever's showing and resets the
  timer.
