#!/usr/bin/env bash
# AIRA — record a live baseline of the Pi before changing anything.  Run ON the Pi:
#     bash raspberry_pi/collect_baseline.sh
# Writes ~/aira-baseline-<time>.txt and prints the same to the screen.
#
# Read-only: changes nothing on the system. Never prints Wi-Fi passwords or keys:
# NetworkManager profiles are listed by name/type only (no --show-secrets).
# Some lines use sudo only to read; if sudo asks for a password, that is the Pi login.

set -u
OUT="$HOME/aira-baseline-$(date +%Y%m%d-%H%M%S).txt"

section() { printf '\n===== %s =====\n' "$1"; }
run() { printf '$ %s\n' "$*"; "$@" 2>&1 || printf '(exit %s)\n' "$?"; }
have() { command -v "$1" >/dev/null 2>&1; }

{
  echo "AIRA baseline — $(date -Is)"

  section "OS"
  run cat /etc/os-release
  run uname -a
  have hostnamectl && run hostnamectl

  section "HARDWARE"
  run tr -d '\0' < /proc/device-tree/model; echo
  have lscpu && run lscpu
  run free -h
  if have vcgencmd; then
    run vcgencmd version
    run vcgencmd get_throttled
    run vcgencmd measure_temp
    run vcgencmd measure_volts core
  fi

  section "STORAGE"
  run lsblk -o NAME,SIZE,FSTYPE,LABEL,MOUNTPOINTS
  run df -h /
  run swapon --show

  section "BOOT CONFIG (no secrets in these files)"
  run cat /boot/firmware/config.txt
  run cat /boot/firmware/cmdline.txt
  have dtoverlay && run sudo dtoverlay -l

  section "AVAILABLE OVERLAYS / DRIVERS (display + audio)"
  run sh -c "ls /boot/firmware/overlays | grep -Ei 'piscreen|fbtft|ads7846|waveshare|hifiberry-dac|googlevoicehat|i2s|rpi-dac' || echo none-matched"
  for m in ili9486 fb_ili9486 fbtft ads7846 snd_soc_hifiberry_dac snd_soc_googlevoicehat_codec snd_soc_rpi_simple_soundcard; do
    if modinfo "$m" >/dev/null 2>&1; then echo "module $m: available"; else echo "module $m: NOT found"; fi
  done
  run sh -c "lsmod | grep -Ei 'spi|i2s|snd|fbtft|ili|ads7846|drm' || true"

  section "GPIO STATE (display / I2S / button pins)"
  if have pinctrl; then run pinctrl get 7-26; else echo "pinctrl not installed"; fi

  section "NETWORK (no passwords)"
  run ip -br addr
  run ip route
  if have nmcli; then
    run nmcli -t -f DEVICE,TYPE,STATE,CONNECTION device status
    run nmcli -t -f NAME,TYPE,DEVICE,AUTOCONNECT connection show
    run sh -c "nmcli -t -f ACTIVE,SSID,CHAN,FREQ,SIGNAL device wifi list --rescan no | grep '^yes' || echo 'not connected'"
  fi
  have avahi-daemon && echo "avahi-daemon present (AIRA.local should resolve)"

  section "SERVICES"
  for s in ssh NetworkManager avahi-daemon rpi-connect; do
    printf '%s: ' "$s"; systemctl is-active "$s" 2>&1; printf '  enabled: '; systemctl is-enabled "$s" 2>&1
  done

  section "INTERFACES / AUDIO / INPUT"
  run sh -c "ls -l /dev/spidev* /dev/i2c-* /dev/fb* /dev/dri/* 2>/dev/null || echo none"
  run sh -c "ls /dev/snd/ 2>/dev/null || echo none"
  have aplay && run aplay -l
  have arecord && run arecord -l
  run sh -c "grep -E '^N: Name=' /proc/bus/input/devices || echo 'no input devices'"

  section "RECENT KERNEL MESSAGES (display / audio / power)"
  run sh -c "sudo dmesg 2>/dev/null | grep -Ei 'spi|i2s|snd|fbtft|ili|ads7846|voltage|throttl' | tail -40 || true"

  section "PYTHON"
  have python3 && run python3 --version
  run sh -c "python3 -c 'import gpiozero; print(\"gpiozero\", gpiozero.__version__)' 2>&1"
} 2>&1 | tee "$OUT"

echo
echo "Saved: $OUT"
echo "Check it before sharing: it should contain no passwords (it was not asked for any)."
