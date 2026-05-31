# Matrix MQTT display (Pi4 + Adafruit HAT)

> **Optional add-on — not required for the world clock.** This is a separate
> application that happens to live in the `world-clock` repo so it can reuse the
> Pi build's rendering and panel geometry. The clock
> ([`../rpi4-adafruit-hat/`](../rpi4-adafruit-hat/)) runs perfectly well on its
> own; **if you don't want MQTT, skip this folder entirely** — nothing in the
> clock imports from here, and you don't need `paho-mqtt` or a broker. This app
> *does* import the clock (for its idle screen), not the other way around.

A single program that owns the 6×(16×32) HUB75 matrix and shows:

- **Idle:** the [world clock](../rpi4-adafruit-hat/) (its rendering and panel
  geometry are imported directly, so the clock is identical).
- **On a message:** text received over **MQTT** — word-wrapped to the 64×48
  panel, scrolling vertically if it's too tall. After `idle_seconds` with no
  newer message, it auto-reverts to the clock.

Only one process can drive the matrix (it holds the GPIO/PWM), so this app
**supersedes** `worldclock.service` — the clock simply becomes its idle screen.

> Host setup (rgbmatrix bindings, Pillow, sound blacklist, governor, RTC) is in
> [`../rpi4-adafruit-hat/SETUP.md`](../rpi4-adafruit-hat/SETUP.md). This app only
> adds `paho-mqtt`:
> ```bash
> sudo apt-get install -y python3-paho-mqtt
> ```

## Configure

Copy the example and edit it (the real file is gitignored):

```bash
cp config.example.json config.local.json
$EDITOR config.local.json
```

| Key | Meaning |
| --- | --- |
| `broker` / `port` | MQTT broker address and port (1883 plain, 8883 TLS) |
| `username` / `password` | Optional auth (`null` for anonymous) |
| `tls` | `true` to connect over TLS |
| `topics` | List of topics to subscribe to |
| `idle_seconds` | Default time a message stays before falling back to the clock |
| `text_color` | Default `[r,g,b]` for message text |
| `font` | `"tom-thumb"` (dense, more text) or `"5x7"` (more legible) |
| `fps` | Render loop rate (smooth scroll); 20 is plenty |
| `scroll_px_per_sec` | Vertical scroll speed for overflowing messages |

Secrets stay out of git: `config.local.json` matches the repo `.gitignore`.

## Message format

Publishers may send **plain UTF-8 text**:

```bash
mosquitto_pub -h localhost -t whisper/transcript -m "the quick brown fox"
```

…or **JSON** to override per-message behavior:

```bash
mosquitto_pub -h localhost -t whisper/transcript \
  -m '{"text": "BUILD PASSED", "ttl": 10, "color": [0,255,80]}'
```

| JSON field | Default | Notes |
| --- | --- | --- |
| `text` (or `message`) | — | the string to show |
| `ttl` | `idle_seconds` | seconds to keep showing before reverting to clock |
| `color` | `text_color` | `[r,g,b]` |

Anything that isn't a JSON object (a bare string, a number, etc.) is shown as
plain text.

## Run

```bash
# manual (Ctrl-C to stop)
sudo python3 display.py
```

Deploy as a service (replacing the clock service):

```bash
sudo systemctl disable --now worldclock.service          # free the matrix
sudo systemctl enable /home/kaerka/world-clock/mqtt-display/mqtt-display.service
sudo systemctl daemon-reload
sudo systemctl start mqtt-display.service
sudo systemctl status mqtt-display.service --no-pager
```

To go back to the plain clock, reverse it: disable `mqtt-display`, re-enable
`worldclock`.

## Notes

- `paho-mqtt` is the 1.6.x (v1 callback API) build from Debian apt.
- The MQTT client reconnects automatically (1–30 s backoff), so a broker
  restart or network blip is handled without dropping the clock.
- Newest message wins: a new publish replaces whatever's showing and resets the
  timer.
