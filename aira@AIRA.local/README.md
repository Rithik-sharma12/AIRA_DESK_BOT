# raspberry_pi/: everything AIRA's Raspberry Pi needs

Copy **this one folder** to the Pi Zero 2 W. It is self-contained: nothing else from the
repo is needed on the Pi, and it installs only Debian packages (nothing is compiled).

| In this folder | What it does |
|---|---|
| `aira_pi/` | AIRA's touch screen (mascot, speech bubble, Chat / Music / Tools / System), the button, the link to the laptop, and the Pi's own voice once the I²S audio is wired |
| `setup_audio.sh` | step-by-step I²S audio setup with backups and undo |
| `aira_pi/assets/mascot/` | AIRA's sprites, one folder per emotion ([sprite spec](aira_pi/assets/mascot/README.md)) |
| `install.sh`, `aira-pi.service` | installs the face as a system service |
| `wifi_keyboard/` | use your phone as a keyboard for the Pi ([its README](wifi_keyboard/README.md)) |
| `check_hardware.py`, `collect_baseline.sh` | hardware checks and a read-only system report |

The laptop does all the thinking (AI model, speech recognition, AIRA's voice). The Pi
shows what AIRA is doing and sends your button presses (and later your voice) to it.

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

## 5. Using it

The screen uses the same neo-brutalist design as AIRA's web page (black strokes, teal /
magenta / yellow on paper) with the pixel-art mascot. The menu on the left switches screens:

| Screen | What it shows / does |
|---|---|
| **Home** | AIRA (idle, listening, thinking, speaking with a moving mouth, happy, excited, confused, sleepy, error; she blinks), her words in the speech bubble (Tamil or English, long answers turn pages), Pi CPU / RAM / temperature, ONLINE. Laptop away: a big clock with sleepy AIRA |
| **Chat** | The latest messages between you and AIRA |
| **Music** | Previous / play-pause / next, volume down / mute / up: these control the **laptop's** music |
| **Tools** | Laptop status, timer 5 min, reminders, lock laptop, open Chrome, open VS Code |
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

## 6. When the I²S audio is wired

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
| `bash setup_audio.sh` | I²S audio check; `--dac`, `--duplex`, `--test`, `--undo` |
| `python3 -m aira_pi discover` | can the Pi see the laptop's beacon? |
| `python3 -m aira_pi info` | saved settings (no secrets shown) |
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
| `pair` says "Not found" | Laptop server running with `AIRA_ALLOW_LAN=true`? Same hotspot? Windows firewall allows TCP 8765 in (Private network profile)? Or pass `--laptop https://<laptop-ip>:8765` |
| Face says "looking for the laptop" | Same as above; the laptop's server window must be open |
| "The laptop refused this Pi" | The laptop forgot its paired devices (data folder cleared): pair again |
| Certificate changed (you re-ran make_dev_cert.py) | Pair again; the Pi refuses the new certificate until you do |
| Screen shows the text console, not the face | `sudo systemctl start aira-pi`; `journalctl -u aira-pi` for the reason |
| Face upside down | set `"rotate": 180` in the config, restart the service |
| Tamil captions show boxes | `sudo apt install fonts-noto-core libraqm0` |
| Button does nothing | `python3 check_hardware.py`; is it wired GPIO26 (pin 37) to GND (pin 39)? |
| Taps land in the wrong place | `sudo systemctl stop aira-pi`, `python3 -m aira_pi calibrate`, start it again |
| Touch does nothing | `journalctl -u aira-pi` should say `Touch: ADS7846 ...`; re-run `sudo bash install.sh` (python3-evdev, input group), then reboot |
| "Not allowed" on Restart / Shut down | re-run `sudo bash install.sh` (adds the polkit rule); `sudo apt install polkitd` if it says polkit is missing |

The display journal (docs/journal/2026-10-31-display-setup.md) still applies: do not
re-run `LCD35-show`, and do not change `/boot/firmware/config.txt` for AIRA's face.
