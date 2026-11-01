# AIRA joystick (ESP32 + KY-023)

A wireless joystick for AIRA's Raspberry Pi screen. The ESP32 reads the stick and sends
navigation events over Wi-Fi; the Pi moves a magenta focus ring through its menu and buttons.
It works without the laptop and without touch.

```
KY-023 --wires--> ESP32 WROOM --Wi-Fi (same hotspot)--> AIRA Pi (aira_pi, port 8771) --> 3.5" screen
```

## Wiring

| KY-023 | ESP32 WROOM | |
|---|---|---|
| VCC | 3.3V | not 5 V: the ESP32's inputs take at most 3.3 V |
| GND | GND | |
| VRX | GPIO 32 | ADC1 (keeps working while Wi-Fi is on; ADC2 pins do not) |
| VRY | GPIO 33 | ADC1 |
| SW | GPIO 27 | press = connected to GND (internal pull-up used) |

## What it does on AIRA

| Joystick | On the Pi screen |
|---|---|
| up / down | move through the menu (Home, Chat, Music, Tools, Emotions, About, System) |
| right | into the screen's buttons |
| arrows | between buttons; left from the leftmost button = back to the menu |
| short press | select (the menu: into its buttons; a button: press it) |
| long press (0.8 s) | back to Home |
| hold a direction | repeats |

Restart and Shut down on the System screen still need two presses, as with touch.

## Setup

**1. Arduino IDE 2** (Windows): Boards Manager → install **esp32 by Espressif Systems**;
Library Manager → install **WebSockets** by Markus Sattler. Board: *ESP32 Dev Module*.

**2. Secrets.** In `aira_joystick/`, copy `secrets.h.example` to `secrets.h` and fill in your
hotspot name and password. Get the pairing token **on AIRA**:
```bash
cd ~/raspberry_pi
python3 -m aira_pi controller
```
and copy the `AIRA_TOKEN` line it prints. `secrets.h` is git-ignored; never commit it.

**3. Hotspot band.** The ESP32 only uses **2.4 GHz** Wi-Fi. On Xiaomi / Redmi phones:
Settings → Portable hotspot → set the AP band to 2.4 GHz (or turn on "Extend compatibility").
The Pi and the ESP32 must be on the same hotspot.

## Build it in three stages

**Stage 1: joystick only.** In `secrets.h` set `AIRA_SERIAL_ONLY 1`, upload, open the Serial
Monitor at 115200 baud. Leave the stick centred while it starts (it measures the centre).
Moving it prints `{"event":"navigate","direction":"up"}` etc.; pressing prints `select`,
holding prints `back`. If up/down or left/right come out swapped or reversed, change
`SWAP_XY`, `INVERT_X`, `INVERT_Y` at the top of the sketch.

**Stage 2: network, without the ESP32.** Test the Pi side first with the keyboard stand-in,
**on AIRA** (or any computer on the hotspot with Python and websockets):
```bash
python3 -m aira_pi joystick           # finds the Pi by its announcement; w a s d, e = select, b = back
```

**Stage 3: the real joystick.** Set `AIRA_SERIAL_ONLY 0`, upload. The Serial Monitor shows
`[wifi] connecting`, `[net] found AIRA at 10.x.x.x:8771`, `[ws] paired with AIRA`; the Pi
shows "Joystick connected" and its System screen says `Joystick: connected`.

## Troubleshooting

| Serial Monitor says | Fix |
|---|---|
| keeps `[wifi] connecting` | wrong name / password, or the hotspot is 5 GHz only (step 3) |
| `waiting for AIRA's announcement` | AIRA not running (`sudo systemctl status aira-pi`), or different networks. Last resort: set `AIRA_PI_HOST` to the Pi's address |
| `refused: wrong token` | copy the token again from `python3 -m aira_pi controller` |
| `refused: too many wrong tokens` | fix the token, then `sudo systemctl restart aira-pi` |
| directions jitter at rest | raise `DEAD_ZONE` (e.g. 1200) |

## How it talks to the Pi

The Pi announces `{"service":"aira-pi","v":1,"port":8771,"name":"AIRA Pi"}` on UDP 8767 every
2 s. The ESP32 opens `ws://<pi>:8771/`, sends `{"event":"hello","device":"aira-joystick","token":"..."}`,
then `{"event":"navigate","direction":"up"}`, `{"event":"select"}` or `{"event":"back"}`.
Full protocol: `raspberry_pi/aira_pi/controller.py`. Any device that speaks it can control AIRA.
