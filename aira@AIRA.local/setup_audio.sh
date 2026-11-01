#!/usr/bin/env bash
# I²S audio bring-up for AIRA (INMP441 mic + PCM5102A DAC), following docs/HARDWARE.md step by step.
# Run ON AIRA, from the raspberry_pi folder:
#
#   bash setup_audio.sh                  # check only (default): wiring pins, overlays, sound cards. Changes nothing.
#   sudo bash setup_audio.sh --dac       # step 1: DAC only   -> adds  dtparam=i2s=on + dtoverlay=hifiberry-dac
#   sudo bash setup_audio.sh --duplex    # step 3: mic + DAC  -> swaps hifiberry-dac for googlevoicehat-soundcard
#   bash setup_audio.sh --test           # after a reboot: play a tone, record 3 s from the mic, play it back
#   sudo bash setup_audio.sh --undo      # put back config.txt as it was before this script's last change
#                                        # (run again to step back further)
#
# Safety: backs up /boot/firmware/config.txt before every change, adds lines only BELOW the
# existing dtoverlay=tft35a line, never touches the display/KMS lines, never reboots by itself.
# Only one audio overlay is active at a time. The display journal still applies: do not re-run LCD35-show.
set -euo pipefail

CFG="${AIRA_CONFIG_TXT:-/boot/firmware/config.txt}"     # overridable only for the repo's tests
OVL="${AIRA_OVERLAYS_DIR:-/boot/firmware/overlays}"
MARK="# AIRA audio (setup_audio.sh)"
mode="${1:---check}"

say()  { printf '%s\n' "$*"; }
need_root() { if [[ $EUID -ne 0 && -z "${AIRA_CONFIG_TXT:-}" ]]; then say "Run with sudo for this step."; exit 1; fi; }

card() {   # first real sound card (not HDMI / headphones)
  "$1" -l 2>/dev/null | awk '/^card [0-9]+:/ && tolower($0) !~ /hdmi|vc4|bcm2835|headphones/ {sub(":","",$3); print $3; exit}'
}

check() {
  say "== Overlays available on this kernel ($(uname -r))"
  for o in hifiberry-dac googlevoicehat-soundcard; do
    if [[ -f "$OVL/$o.dtbo" ]]; then say "   ok       $o"; else say "   MISSING  $o (this step cannot be used as-is)"; fi
  done
  say "== Audio lines in $CFG"
  grep -nE '^\s*(dtparam=i2s|dtoverlay=(hifiberry|googlevoicehat|i2s))' "$CFG" || say "   (none yet)"
  grep -nE '^\s*dtoverlay=tft35a' "$CFG" >/dev/null && say "   display overlay present (left untouched)" \
    || say "   WARNING: no dtoverlay=tft35a line found; this script will not edit an unexpected config"
  say "== GPIO 16-21 and 26 (I²S pins 18-21, amp enable 16, button 26)"
  if command -v pinctrl >/dev/null; then pinctrl get 16-21,26; else say "   pinctrl not installed"; fi
  say "== Sound cards"
  local p c
  p=$(card aplay || true); c=$(card arecord || true)
  say "   speaker: ${p:-none}    microphone: ${c:-none}"
  if [[ -n "$p" && -n "$c" ]]; then say "   AIRA will use them automatically: sudo systemctl restart aira-pi"; fi
}

backup() {
  local b="$CFG.bak-aira-$(date +%Y%m%d-%H%M%S-%N)"
  cp "$CFG" "$b"
  say "   backup: $b"
}

apply() {   # $1 = overlay line to have (replacing any audio overlay this script added)
  need_root
  grep -qE '^\s*dtoverlay=tft35a' "$CFG" || { say "No dtoverlay=tft35a line in $CFG: not editing an unexpected config."; exit 1; }
  local name="${1#dtoverlay=}"
  [[ -f "$OVL/$name.dtbo" ]] || { say "Overlay $name is not available on this kernel ($OVL). Stopping."; exit 1; }
  backup
  local tmp
  tmp=$(mktemp)
  # drop lines this script added before, then insert the new block right after the tft35a line
  awk -v mark="$MARK" -v line="$1" '
    $0 == mark { skip = 3 }
    skip > 0  { skip--; next }
    { print }
    /^[[:space:]]*dtoverlay=tft35a/ && !done { print mark; print "dtparam=i2s=on"; print line; done = 1 }
  ' "$CFG" > "$tmp"
  if [[ $(grep -cE '^\s*dtoverlay=(hifiberry|googlevoicehat|i2s)' "$tmp") -gt 1 ]]; then
    rm -f "$tmp"; say "Another audio overlay is already in $CFG (not added by this script). Remove it by hand first."; exit 1
  fi
  cat "$tmp" > "$CFG"; rm -f "$tmp"
  say "   added:  dtparam=i2s=on / $1   (below the tft35a line)"
  say "Reboot to apply:  sudo reboot     Then check:  bash setup_audio.sh  and  bash setup_audio.sh --test"
  say "If the 3.5\" screen stays white after the reboot: sudo bash setup_audio.sh --undo && sudo reboot"
}

test_audio() {
  local p c
  p=$(card aplay || true); c=$(card arecord || true)
  [[ -n "$p" ]] || { say "No I²S speaker card yet (run: bash setup_audio.sh)."; exit 1; }
  say "== Playing a 2 s tone on $p (turn the amplifier volume down first)"
  speaker-test -D "plughw:CARD=$p" -c 2 -t sine -f 660 -l 1 -p 2000 >/dev/null 2>&1 || say "   speaker-test failed"
  if [[ -n "$c" ]]; then
    say "== Recording 3 s from $c: say something"
    arecord -q -D "plughw:CARD=$c" -f S16_LE -r 16000 -c 1 -d 3 /tmp/aira-mic.wav
    say "== Playing it back"
    aplay -q -D "plughw:CARD=$p" /tmp/aira-mic.wav
    rm -f /tmp/aira-mic.wav
  else
    say "No microphone card (DAC-only overlay, or the mic is not wired yet)."
  fi
}

undo() {
  need_root
  local last
  last=$(ls -1 "$CFG".bak-aira-* 2>/dev/null | grep -v '\.used$' | sort | tail -1 || true)
  [[ -n "$last" ]] || { say "No backup made by this script."; exit 1; }
  cp "$last" "$CFG"
  mv "$last" "$last.used"           # the next --undo steps back one change further
  say "Restored $last -> $CFG. Reboot to apply:  sudo reboot"
}

case "$mode" in
  --check|-c) check ;;
  --dac)      apply "dtoverlay=hifiberry-dac" ;;
  --duplex)   apply "dtoverlay=googlevoicehat-soundcard" ;;
  --test)     test_audio ;;
  --undo)     undo ;;
  *) sed -n '2,12p' "$0"; exit 1 ;;
esac
