#!/usr/bin/env bash
# Install AIRA's face service on the Raspberry Pi.   Run ON THE PI, inside this folder:
#     sudo bash install.sh              # install / update (does not start the face until paired)
#     sudo bash install.sh --uninstall
#
# What it changes (and nothing else: no boot config, no TFT/display overlay, no network setup):
#   apt packages   python3-websockets python3-pil python3-numpy python3-gpiozero python3-lgpio python3-evdev
#                  fonts-noto-core fonts-dejavu-core fonts-unifont libraqm0 alsa-utils wireless-tools
#   groups         adds the user to video, gpio, audio, input (only groups that exist)
#   polkit rule    /etc/polkit-1/rules.d/50-aira-power.rules: this user may restart / shut down the Pi
#                  (the System screen's buttons); nothing else
#   service        /etc/systemd/system/aira-pi.service, running "python3 -m aira_pi run" from this folder
# Safe to run again after copying a newer version of this folder.
set -euo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
UNIT=/etc/systemd/system/aira-pi.service
USER_NAME="${SUDO_USER:-aira}"

if [[ $EUID -ne 0 ]]; then echo "Run with sudo:  sudo bash install.sh"; exit 1; fi

if [[ "${1:-}" == "--uninstall" ]]; then
  systemctl disable --now aira-pi.service 2>/dev/null || true
  rm -f "$UNIT" /etc/polkit-1/rules.d/50-aira-power.rules
  systemctl daemon-reload
  echo "Removed the service. Your pairing stays in /home/$USER_NAME/.config/aira-pi (delete it to forget the laptop)."
  exit 0
fi

if [[ ! -f "$DIR/aira_pi/__main__.py" ]]; then echo "Run this from the copied raspberry_pi folder."; exit 1; fi
if ! id "$USER_NAME" >/dev/null 2>&1; then echo "User $USER_NAME not found."; exit 1; fi

echo "== 1/4 packages (from Debian, nothing compiled on the Pi)"
apt-get update -qq || echo "   (apt update failed: is the Pi online? continuing with what is installed)"
for pkg in python3-websockets python3-pil python3-numpy python3-gpiozero python3-lgpio python3-evdev \
           fonts-noto-core fonts-dejavu-core fonts-unifont libraqm0 alsa-utils wireless-tools; do
  if dpkg -s "$pkg" >/dev/null 2>&1; then
    echo "   ok      $pkg"
  elif apt-get install -y -qq "$pkg" >/dev/null 2>&1; then
    echo "   added   $pkg"
  else
    echo "   MISSING $pkg (not available; AIRA runs without the feature it provides)"
  fi
done
python3 - <<'PY'
import importlib.util, sys
need = {"websockets": "link to the laptop", "PIL": "drawing the face", "numpy": "screen colour format"}
bad = [f"{m} ({why})" for m, why in need.items() if importlib.util.find_spec(m) is None]
if bad:
    print("   ERROR: missing Python modules: " + ", ".join(bad)); sys.exit(1)
PY

echo "== 2/4 permissions for $USER_NAME (screen, GPIO button, sound, touch)"
for g in video gpio audio input; do
  if getent group "$g" >/dev/null; then usermod -aG "$g" "$USER_NAME"; echo "   in group $g"; fi
done
if [[ -d /etc/polkit-1/rules.d ]]; then
  cat > /etc/polkit-1/rules.d/50-aira-power.rules <<RULE
// AIRA (raspberry_pi/install.sh): let $USER_NAME restart / shut down the Pi from AIRA's System screen.
polkit.addRule(function (action, subject) {
  var ids = ["org.freedesktop.login1.reboot", "org.freedesktop.login1.reboot-multiple-sessions",
             "org.freedesktop.login1.power-off", "org.freedesktop.login1.power-off-multiple-sessions"];
  if (ids.indexOf(action.id) >= 0 && subject.user == "$USER_NAME") { return polkit.Result.YES; }
});
RULE
  echo "   restart / shut down allowed from the System screen (polkit)"
else
  echo "   polkit not installed: the System screen's Restart / Shut down buttons will say 'Not allowed'"
fi

echo "== 3/4 service"
sed -e "s#@USER@#$USER_NAME#g" -e "s#@DIR@#$DIR#g" "$DIR/aira-pi.service" > "$UNIT"
systemctl daemon-reload
echo "   aira-pi.service installed (runs from $DIR)"

echo "== 4/4 status"
CFG="/home/$USER_NAME/.config/aira-pi/config.json"
if [[ -f "$CFG" ]] && grep -q '"token": "' "$CFG"; then
  systemctl enable aira-pi.service >/dev/null
  systemctl restart aira-pi.service
  echo "   Already paired: AIRA's face is starting on the 3.5\" screen (and at every boot)."
else
  cat <<EOF

   Not paired yet, so the face is NOT started or enabled (the text console stays on the screen).
   Next, as $USER_NAME (not root), with the laptop server running (AIRA_ALLOW_LAN=true):
       cd "$DIR"
       python3 -m aira_pi pair
   then line up the touch screen (once) and start the face now and at every boot:
       python3 -m aira_pi calibrate
       sudo systemctl enable --now aira-pi
EOF
fi
echo
echo "Useful:  journalctl -u aira-pi -f      (live log)"
echo "         python3 -m aira_pi calibrate  (touch; stop the service first)"
echo "         bash setup_audio.sh           (I2S audio check / setup, when it is wired)"
echo "         sudo systemctl stop aira-pi   (text console back; or hold the button 6 s)"
