# raspberry_pi/: everything AIRA's Raspberry Pi needs

Copy **this one folder** to the Pi Zero 2 W. It is self-contained: nothing else from the
repo is needed on the Pi, and it installs only Debian packages (nothing is compiled).

| In this folder | What it does |
|---|---|
| `aira_pi/` | AIRA's touch screen (mascot, speech bubble, Chat / Music / Tools / System), the button, the link to the laptop, and the Pi's own voice once the I²S audio is wired |
| `setup_audio.sh` | step-by-step I²S audio setup with backups and undo |
| `touch_spi.sh` | touch without the pen signal: reads the touch chip directly over SPI |
| `aira_pi/online.py`, `chat_page.html` | AIRA on the Pi alone: online AI (your API key) and a chat page for your phone |
| `aira_pi/assets/mascot/` | AIRA's sprites, one folder per emotion ([sprite spec](aira_pi/assets/mascot/README.md)) |
| `install.sh`, `aira-pi.service` | installs the face as a system service |
| `wifi_keyboard/` | use your phone as a keyboard for the Pi ([its README](wifi_keyboard/README.md)) |
| `check_hardware.py`, `collect_baseline.sh` | hardware checks and a read-only system report |

The laptop does the thinking (AI model, speech recognition, AIRA's voice). The Pi
shows what AIRA is doing and sends your button presses (and later your voice) to it. Without
the laptop, the Pi can use an online AI by itself (section 6).

## 1. Copy the folder to the Pi

**Windows PowerShell** (on the laptop), if SSH is enabled on the Pi:
```powershell
scp -r "E:\Github Repo\AIRA_DESK_BOT\raspberry_pi" aira@AIRA.local:~/
```
No SSH? Copy the folder to a USB stick, plug it into the Pi (OTG adapter), and **on AIRA**:
```bash
lsblk                                   # find the stick, e.g. sda1
sudo mount /dev/sda1 /mnt && cp -r /mnt/raspberry_pi ~/ && sudo umount /mnt
```
To update later, copy the folder again the same way and re-run `install.sh`.

## 2. Install (on AIRA)
```bash
cd ~/raspberry_pi
sudo bash install.sh
```
It installs a few Debian packages, gives the `aira` user access to the screen, GPIO and
sound, and registers `aira-pi.service`. It does **not** touch the boot config, the TFT
overlay or the network. The face only starts once the Pi is paired.

## 3. Pair with the laptop (once)

On the laptop, the server must accept LAN connections. Use HTTPS so the Pi can pin the
laptop's certificate (HOW-TO-RUN.md, section 4).

**Quickest: one script on Windows** does the checks below and tells you what is missing:
```powershell
.\scripts\connect_pi.ps1              # check: .env, certificate, hotspot Private, firewall, server, the Pi
.\scripts\connect_pi.ps1 -Fix         # as Administrator: also fixes them
.\scripts\connect_pi.ps1 -Deploy      # copy this folder to the Pi (asks the Pi password)
.\scripts\connect_pi.ps1 -Pair        # pair over SSH while the server runs in another window
```
The manual steps, if you prefer:

**Windows PowerShell**, in the project folder (once; re-running it means pairing again):
```powershell
python scripts\make_dev_cert.py
```
**Windows**, in `.env` (all four lines; without `AIRA_HOST=0.0.0.0` only the laptop itself can connect):
```
AIRA_HOST=0.0.0.0
AIRA_ALLOW_LAN=true
AIRA_SSL_CERTFILE=certs/aira-dev.crt
AIRA_SSL_KEYFILE=certs/aira-dev.key
```
Allow TCP port 8765 in through the Windows firewall (**PowerShell as Administrator**, once):
```powershell
New-NetFirewallRule -DisplayName "AIRA web 8765" -Direction Inbound -Protocol TCP -LocalPort 8765 -Action Allow -Profile Private
```
The hotspot must be a *Private* network in Windows (Settings → Network → Wi-Fi → the hotspot).
The discovery beacon (UDP 8766) only goes out, so it needs no rule. Then restart the server:
its window shows `Announcing AIRA on the LAN`, the certificate fingerprint and the pairing code.

**On AIRA** (Pi and laptop on the same hotspot):
```bash
cd ~/raspberry_pi
python3 -m aira_pi pair
```
The Pi finds the laptop by itself and prints a short certificate fingerprint. Check that it
matches the start of the `Certificate fingerprint` line in the laptop's server window,
then type the 6-digit pairing code shown there. Start the face (now and at every boot):
```bash
sudo systemctl enable --now aira-pi
```

## 4. Line up the touch screen (once)

The 3.5" screen's touch layer has to be matched to the picture once. **On AIRA**, with the
face stopped:
```bash
sudo systemctl stop aira-pi
python3 -m aira_pi calibrate        # tap the 4 targets (a stylus or fingernail works best)
sudo systemctl start aira-pi
```
After the 4 targets, test for 15 s: dots should land under your finger. Run it again any
time taps feel off. Without touch, everything still works through the button and the laptop.

**Touch does nothing at all?** On some of these screens the touch chip's "pen down" signal
(GPIO17) never fires, so the kernel never reads a press (`python3 -m aira_pi touchtest` then
shows 0 events and 0 interrupts). AIRA can read the chip directly over SPI instead, without
that signal. **On AIRA**, from this folder:
```bash
sudo bash touch_spi.sh test       # switches until the next reboot, shows live readings and a verdict
sudo bash touch_spi.sh enable     # if the verdict says touch works: use it at every boot
sudo systemctl stop aira-pi && python3 -m aira_pi calibrate && sudo systemctl start aira-pi
sudo bash touch_spi.sh disable    # back to the kernel driver at any time
```
It changes only which driver owns the touch chip (no `config.txt` edit, the display is not
touched). The verdict also tells you if the chip reports a press all the time (something
pressing the screen's edge, or a damaged touch layer) or does not answer (wiring).

## 5. Using it

**AIRA Half** (the current build: no microphone, speaker or battery on the Pi yet). You talk to AIRA on the web
page (laptop or phone browser): the page is her ears and voice, the Pi is her face. Everything
said there shows on the Pi: listening, thinking, speaking with a moving mouth, and the words.
The page's top bar says **Pi screen on** when the Pi is connected, and on a wide screen the
**AIRA's screen (Pi)** buttons switch the Pi between Home / Chat / Music / Tools / System.
When the microphone, speaker and battery are in, it becomes **AIRA Full**: set `"edition": "full"`
in `~/.config/aira-pi/config.json` and `sudo systemctl restart aira-pi` (System screen and the
web page then say AIRA Full).

The screen uses the same neo-brutalist design as AIRA's web page (black strokes, teal /
magenta / yellow on paper) with the pixel-art mascot. The menu on the left switches screens:

| Screen | What it shows / does |
|---|---|
| **Home** | AIRA (idle, listening, thinking, speaking with a moving mouth, happy, excited, confused, sleepy, error; she blinks), her words in the speech bubble (Tamil or English, long answers turn pages), Pi CPU / RAM / temperature, ONLINE. Laptop away: a big clock with sleepy AIRA |
| **Chat** | The latest messages between you and AIRA |
| **Music** | Previous / play-pause / next, volume down / mute / up: these control the **laptop's** music |
| **Tools** | Laptop status, timer 5 min, reminders, lock laptop, open Chrome, open VS Code |
| **Emotions** (smiley) | Try AIRA's 9 emotions: tap one to see it, or **AUTO PLAY** to see each for 3 s. Opened from the web page it plays them automatically |
| **About** (i) | Version, release date, developer, and this Pi: board, CPU, RAM, storage, OS, kernel, screen, touch, uptime (read live). Developer details: `aira_pi/about.json` |
| **System** | Pi CPU, RAM, swap, temperature, IP, Wi-Fi, laptop link, and buttons: **Text console**, **Restart Pi**, **Shut down Pi** (restart and shut down ask you to tap twice) |

- Any screen returns to Home after 30 s, or when AIRA answers.
- "Connected to ..." pops up when the laptop link comes back. Reminders show excited AIRA.
- **Button** (GPIO26, pin 37, to GND, pin 39):
  - short press: talk. Without Pi audio, it switches on the microphone of the AIRA page
    open on the laptop/phone. With Pi audio, hold to talk and release to send.
  - hold 1.5 s: the System screen.
  - hold 6 s: back to the **text console** (for the phone keyboard). `sudo systemctl start aira-pi` brings the face back.
- The laptop's address changes on a hotspot; the Pi follows it automatically and only
  connects to the laptop whose certificate it pinned.
- Looks: `"theme"` in the config: `"brutal"` (default, this design), `"mascot"` (the purple
  night pixel theme, no touch menu) or `"simple"` (plain vector eyes). Sprites, blinks and
  the talking mouth live in `aira_pi/assets/mascot/` (see its README).

## 6. AIRA on the Pi alone (online AI)

Without the laptop, the Pi can answer by itself through an online AI with your API key
(Gemini, OpenAI, Groq, OpenRouter, DeepSeek, Anthropic or any OpenAI-compatible service).
You type to her on the Pi's **chat page** from your phone; the Pi shows her face and words,
and the phone's browser speaks the answer (AIRA Half has no speaker).
**On AIRA**, from this folder:
```bash
python3 -m aira_pi online setup     # service, model name, API key (typing hidden), mode; then a test
sudo systemctl restart aira-pi
```
Modes: `auto` (default after setup) uses the laptop whenever it is connected and the online
AI otherwise; `online` always uses the online AI. Open `http://<pi-ip>:8770/` on the phone
(same hotspot) and enter the 6-digit code from AIRA's **System** screen once. When the laptop
is connected, the same page sends your words to the laptop instead. Without the laptop she
cannot control it (no apps, volume or timers on the laptop).
`python3 -m aira_pi online status` shows the setup (never the key); `online off` removes the key.
What you type is sent to the online service you chose.

## 7. Wireless joystick (ESP32)

An ESP32 with a KY-023 joystick can drive the Pi screen over Wi-Fi: up/down through the menu,
right into a screen's buttons, press to select, long press for Home. A magenta ring shows
what is focused. The Pi announces itself (UDP 8767), the ESP32 finds it and pairs with a token.
**On AIRA**:
```bash
python3 -m aira_pi controller       # the token to put in the ESP32's secrets.h
python3 -m aira_pi joystick         # test with the keyboard first: w a s d, e = select, b = back
```
Firmware, wiring and the three test stages: [../esp32_joystick/README.md](../esp32_joystick/README.md).

## 8. When the I²S audio is wired

`setup_audio.sh` follows docs/HARDWARE.md one step at a time. It backs up `config.txt` before
every change, only adds lines below the display overlay, and never reboots by itself.
**On AIRA**, from this folder:
```bash
bash setup_audio.sh                 # check only: overlays available, pins, sound cards
sudo bash setup_audio.sh --dac      # step 1 (DAC wired):  then sudo reboot
bash setup_audio.sh --test          # tone on the speaker (and mic record + playback when wired)
sudo bash setup_audio.sh --duplex   # step 3 (mic wired too): then sudo reboot, --test again
sudo bash setup_audio.sh --undo     # if the screen stays white or audio misbehaves: undo, reboot
```
Then `sudo systemctl restart aira-pi`; the log shows `Pi audio: microphone=... speaker=...`.
Your speech goes to the laptop's Whisper, and AIRA's sentences come back as audio from the
laptop's voice (Azure, or offline Piper), in her sweet-girl style if that is set.

## Commands

| On AIRA | |
|---|---|
| `python3 -m aira_pi calibrate` | line up the touch screen (stop the service first) |
| `python3 -m aira_pi touchtest` | what the touch chip sends: presses, raw values, interrupts |
| `sudo bash touch_spi.sh test` / `enable` / `disable` | touch without the pen signal (direct SPI reader) |
| `python3 -m aira_pi online setup` / `status` / `off` | the Pi's own online AI and chat page |
| `bash setup_audio.sh` | I²S audio check; `--dac`, `--duplex`, `--test`, `--undo` |
| `python3 -m aira_pi discover` | can the Pi see the laptop's beacon? |
| `python3 -m aira_pi info` | saved settings (no secrets shown) |
| `python3 -m aira_pi about` | version, release date, developer, Pi hardware and OS |
| `python3 -m aira_pi controller` / `joystick` | ESP32 joystick token; keyboard stand-in for testing |
| `python3 -m aira_pi demo --out /tmp/faces` | render every expression and screen to PNG (works on a PC too) |
| `journalctl -u aira-pi -f` | live log |
| `sudo systemctl stop aira-pi` | stop the face, text console comes back |
| `sudo bash install.sh --uninstall` | remove the service |

Settings live in `~/.config/aira-pi/config.json` (only readable by `aira`). You can edit
`button_gpio`, `rotate` (180 if the face is upside down), `fps`, `audio` (`auto`/`off`) and
`theme` (`auto`/`mascot`/`simple`) there.

## Troubleshooting

| Problem | Fix |
|---|---|
| `pair` says "Not found" | On Windows run `.\scripts\connect_pi.ps1`: it checks the server is running, `.env`, firewall and Private network, and prints the `pair --laptop https://<laptop-ip>:8765` command |
| Face says "looking for the laptop" | Same as above; the laptop's server window must be open |
| "The laptop refused this Pi" | The laptop forgot its paired devices (data folder cleared): pair again |
| Certificate changed (you re-ran make_dev_cert.py) | Pair again; the Pi refuses the new certificate until you do |
| Screen shows the text console, not the face | `sudo systemctl start aira-pi`; `journalctl -u aira-pi` for the reason |
| Face upside down | set `"rotate": 180` in the config, restart the service |
| Tamil captions show boxes | `sudo apt install fonts-noto-core libraqm0` |
| Button does nothing | `python3 check_hardware.py`; is it wired GPIO26 (pin 37) to GND (pin 39)? |
| Taps land in the wrong place | `sudo systemctl stop aira-pi`, `python3 -m aira_pi calibrate`, start it again |
| Touch does nothing | `python3 -m aira_pi touchtest`. "0 events, 0 interrupts": the chip's pen signal never fires; try `sudo bash touch_spi.sh test` (section 4). Its verdict says whether touch works over SPI, the chip sees a constant press (something pressing the edge, damaged layer) or does not answer (wiring). AIRA works without touch |
| Chat page says "wrong code" | the code is on AIRA's System screen (`python3 -m aira_pi online status` shows it too) |
| Chat page: "no online AI is set up" | the laptop is not connected and `python3 -m aira_pi online setup` was not done |
| "Not allowed" on Restart / Shut down | re-run `sudo bash install.sh` (adds the polkit rule); `sudo apt install polkitd` if it says polkit is missing |

The display journal (docs/journal/2026-10-31-display-setup.md) still applies: do not
re-run `LCD35-show`, and do not change `/boot/firmware/config.txt` for AIRA's face.
