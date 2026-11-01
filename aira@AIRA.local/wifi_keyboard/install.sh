#!/usr/bin/env bash
# Install the AIRA Wi-Fi keyboard service on the Pi.   Run ON AIRA, from the repo:
#     sudo bash raspberry_pi/wifi_keyboard/install.sh            # install / update
#     sudo bash raspberry_pi/wifi_keyboard/install.sh --uninstall
#
# What it changes (and nothing else — no boot config, no display/TFT config, no network):
#   apt package  python3-evdev
#   system user  aira-kbd (no login, member of group "input")
#   kernel       loads the uinput module now and at boot  (/etc/modules-load.d/aira-uinput.conf)
#   udev rule    /dev/uinput readable/writable by group "input" (/etc/udev/rules.d/60-aira-uinput.rules)
#   files        /opt/aira-keyboard/  (code), /etc/default/aira-keyboard (options)
#   service      aira-keyboard.service, listening on TCP 8780
set -euo pipefail

SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEST=/opt/aira-keyboard
UNIT=/etc/systemd/system/aira-keyboard.service
RULE=/etc/udev/rules.d/60-aira-uinput.rules
MODS=/etc/modules-load.d/aira-uinput.conf

if [[ $EUID -ne 0 ]]; then echo "Run with sudo."; exit 1; fi

if [[ "${1:-}" == "--uninstall" ]]; then
  systemctl disable --now aira-keyboard.service 2>/dev/null || true
  rm -f "$UNIT" "$RULE" "$MODS"
  rm -rf "$DEST"
  systemctl daemon-reload
  udevadm control --reload-rules || true
  echo "Removed. Paired-phone records remain in /var/lib/aira-keyboard (delete it to forget them)."
  exit 0
fi

echo "== 1/6 package: python3-evdev"
if ! python3 -c "import evdev" 2>/dev/null; then
  apt-get update -qq
  apt-get install -y python3-evdev
fi

echo "== 2/6 service user"
if ! id aira-kbd >/dev/null 2>&1; then
  useradd --system --no-create-home --shell /usr/sbin/nologin --user-group aira-kbd
fi
usermod -aG input aira-kbd

echo "== 3/6 uinput module + permissions"
echo uinput > "$MODS"
modprobe uinput
cat > "$RULE" <<'EOF'
KERNEL=="uinput", SUBSYSTEM=="misc", GROUP="input", MODE="0660", OPTIONS+="static_node=uinput"
EOF
udevadm control --reload-rules
udevadm trigger --subsystem-match=misc --sysname-match=uinput || true
ls -l /dev/uinput

echo "== 4/6 files"
mkdir -p "$DEST/wifi_keyboard/static"
install -m 0644 "$SRC"/__init__.py "$SRC"/__main__.py "$SRC"/keymap.py "$SRC"/injector.py "$SRC"/server.py "$DEST/wifi_keyboard/"
install -m 0644 "$SRC"/static/index.html "$DEST/wifi_keyboard/static/"
install -m 0644 "$SRC"/README.md "$DEST/README.md"
if [[ ! -f /etc/default/aira-keyboard ]]; then
  cat > /etc/default/aira-keyboard <<'EOF'
# Options for aira-keyboard.service. Layout is read from /etc/default/keyboard if not given.
# Examples:  ARGS="--layout us"     ARGS="--port 8780"
ARGS=""
EOF
fi

echo "== 5/6 service"
install -m 0644 "$SRC"/aira-keyboard.service "$UNIT"
systemctl daemon-reload
systemctl enable aira-keyboard.service >/dev/null
systemctl restart aira-keyboard.service
sleep 2

echo "== 6/6 status"
systemctl --no-pager --lines=0 status aira-keyboard.service || true
echo
journalctl -u aira-keyboard.service -n 6 --no-pager -o cat || true
echo
echo "Open the URL above in Chrome on the phone (same Wi-Fi/hotspot) and enter the pairing code."
echo "The code is also printed on AIRA's text console each time a new phone connects."
