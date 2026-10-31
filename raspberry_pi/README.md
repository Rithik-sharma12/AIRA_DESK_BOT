# raspberry_pi/ — Phase 5 (Physical buddy)

Lightweight client for the Raspberry Pi Zero 2 W: pygame face engine on the ILI9486
framebuffer, wake-word detection, audio capture/playback, button/touch input, and the
WebSocket client speaking `shared/protocol`. It never runs an LLM or STT model.
Installs only its own requirements (not the laptop server's).

Hardware wiring and bring-up: `docs/HARDWARE.md`. On the Pi (Debian 13, keep the existing install):

```
bash raspberry_pi/collect_baseline.sh        # read-only report, no secrets
python3 raspberry_pi/check_hardware.py       # after each subsystem is wired
```

## Wi-Fi keyboard

Use the phone as a real keyboard for AIRA over Wi-Fi (no SSH, no Bluetooth): see
`raspberry_pi/wifi_keyboard/README.md`. Install on the Pi with
`sudo bash raspberry_pi/wifi_keyboard/install.sh`.
