# AIRA Wi-Fi keyboard

Use your phone as a real keyboard for AIRA over Wi-Fi. No SSH, no Bluetooth, no screen
mirroring, and no app to install: the Pi serves a web page, and every key you press arrives
in Linux as a keyboard event, through a virtual keyboard made with `uinput`. It types into the
text console on the TFT without a desktop, exactly like a USB keyboard would.

```
Phone (Chrome) ──Wi-Fi / hotspot──► aira-keyboard.service on the Pi ──► /dev/uinput ──► console on the TFT
  web page: typing box + Esc/Tab/Ctrl/Alt/Shift/Super/arrows/F1–F12        "AIRA Wi-Fi Keyboard" input device
```

## What it supports

- Normal typing from Gboard or any phone keyboard, including autocorrect, word suggestions and backspace. The typing box mirrors every change.
- Esc, Tab, Enter, Backspace, Delete, Insert, Home, End, PgUp, PgDn, the arrows (they repeat while held) and F1–F12.
- **Ctrl, Alt, Shift, Super** as sticky keys: tap once to apply to the next key, tap twice to lock, tap again to release.
- Shortcuts: Ctrl+C/D/Z/L/A/E/R/U, and switching text consoles (Ctrl+Alt+F1–F3).
- The Pi's console layout (`gb` or `us`, read from `/etc/default/keyboard`), so `@`, `"`, `#`, `\` and `|` come out right.

What it cannot do: type characters the Linux text console cannot type from a keyboard
(Tamil script, emoji). The page tells you when it skipped any.

## Install (on AIRA)

Copy or clone the repo onto the Pi (USB stick, `scp`, or Raspberry Pi Connect), then:

```bash
# [AIRA]
cd ~/AIRA_DESK_BOT
python3 -m raspberry_pi.wifi_keyboard --dry-run --port 8780   # optional: try it without uinput; Ctrl+C to stop
sudo bash raspberry_pi/wifi_keyboard/install.sh
```

The installer adds `python3-evdev`, a no-login service user, the `uinput` kernel module, a
udev rule for `/dev/uinput` and `aira-keyboard.service`. It does not touch `/boot/firmware`,
the display setup or the network. To remove everything: `sudo bash raspberry_pi/wifi_keyboard/install.sh --uninstall`.

## Use (on the phone)

1. Connect the phone and the Pi to the same network: the phone's hotspot (the Pi joins it) or the same Wi-Fi.
2. AIRA's screen shows `open on your phone: http://<ip>:8780` and a 6-digit **pairing code**.
   On the hotspot, the Pi's address is also listed under the phone's *Hotspot → Connected devices*.
3. Open that address in Chrome, enter the code and a name for the phone, then tap **Pair**.
   - The phone remembers the pairing.
   - Next time it connects straight away.
4. Optional: Chrome menu → **Add to Home screen**, so it opens like an app.

## Security

Anything that can type into a logged-in console can run commands. So:

- **Pairing needs the code shown on AIRA's own screen**, so someone has to be physically in front of AIRA to read it.
  - The code works once and changes every time the service starts.
  - After 5 wrong attempts, pairing locks until the service is restarted.
- Paired phones keep a random token. The Pi stores only its SHA-256 hash, in `/var/lib/aira-keyboard/devices.json`.
- See or revoke paired phones:
  ```bash
  cd /opt/aira-keyboard
  sudo -u aira-kbd python3 -m wifi_keyboard --list
  sudo -u aira-kbd python3 -m wifi_keyboard --revoke-all && sudo systemctl restart aira-keyboard
  ```
- Only private and link-local addresses are served (hotspot or home network). Never forward port 8780 from a router.
- Only allow-listed keys are injected. Magic SysRq is excluded. Messages are size- and rate-limited, and all keys are released when the phone disconnects.
- The service runs as an unprivileged user with systemd sandboxing. It can reach only `/dev/uinput` and the network.
- Traffic is unencrypted on the local network. That is fine on your own hotspot. Don't use it on shared or public Wi-Fi.

## Troubleshooting

| Problem | Fix |
|---|---|
| Page does not open | Phone and Pi on different networks; check `systemctl status aira-keyboard`; check the IP |
| Pairing code not visible on the TFT | The console may not be on the TFT yet. Read it with `journalctl -u aira-keyboard -n 5 -o cat` (for example via Raspberry Pi Connect) |
| Keys arrive but symbols are wrong | Wrong layout: set `ARGS="--layout us"` (or `gb`) in `/etc/default/aira-keyboard`, then `sudo systemctl restart aira-keyboard` |
| `cannot open /dev/uinput` | `lsmod | grep uinput` and `ls -l /dev/uinput` (should be group `input`); re-run the installer |
| Typing feels slow | Turn off word suggestions in Gboard for this site, or type commands in a notes app and paste them |
| A key seems stuck | Shortcuts → **Release keys**, or reload the page |
