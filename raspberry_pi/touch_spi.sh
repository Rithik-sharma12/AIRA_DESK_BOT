#!/usr/bin/env bash
# Read the touch chip directly over SPI, for screens whose pen-interrupt line (GPIO17) never fires.
# Run ON AIRA, in this folder, with sudo:
#
#   sudo bash touch_spi.sh status    which driver owns the touch chip, GPIO17 level
#   sudo bash touch_spi.sh test      switch to SPI until the next reboot and show live readings + verdict
#   sudo bash touch_spi.sh enable    use SPI at every boot (installs aira-touch-spi.service), restart AIRA
#   sudo bash touch_spi.sh disable   back to the kernel touch driver (ads7846), at once and at boot
#
# What it changes: which kernel driver is bound to spi0.1 (the touch chip's SPI address), through
# /sys driver_override. It does NOT edit /boot/firmware/config.txt or the TFT overlay; the display
# (spi0.0) is not touched. A reboot undoes "test"; "disable" undoes "enable".
set -euo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEV=spi0.1
SYS=/sys/bus/spi/devices/$DEV
NODE=/dev/spidev0.1
UNIT=/etc/systemd/system/aira-touch-spi.service
USER_NAME="${SUDO_USER:-aira}"

need_root() { if [[ $EUID -ne 0 ]]; then echo "Run with sudo:  sudo bash touch_spi.sh $1"; exit 1; fi; }
driver() { local l; if l="$(readlink "$SYS/driver" 2>/dev/null)"; then basename "$l"; else echo none; fi; }
group() { for g in spi gpio; do getent group "$g" >/dev/null && { echo "$g"; return; }; done; echo root; }

bind_spidev() {
  [[ -e $SYS ]] || { echo "No $DEV: is the tft35a overlay loaded? (ls /sys/bus/spi/devices)"; exit 1; }
  modprobe spidev
  echo spidev > "$SYS/driver_override"
  if [[ "$(driver)" != spidev ]]; then
    [[ -e $SYS/driver ]] && echo "$DEV" > "$SYS/driver/unbind"
    echo "$DEV" > /sys/bus/spi/drivers_probe
  fi
  for _ in 1 2 3 4 5 6 7 8 9 10; do [[ -e $NODE ]] && break; sleep 0.2; done
  [[ -e $NODE ]] || { echo "spidev bound but $NODE did not appear"; exit 1; }
  chgrp "$(group)" "$NODE" && chmod 660 "$NODE"
}

bind_kernel() {
  [[ -e $SYS ]] || return 0
  echo "" > "$SYS/driver_override"
  if [[ "$(driver)" == spidev ]]; then echo "$DEV" > "$SYS/driver/unbind"; fi
  echo "$DEV" > /sys/bus/spi/drivers_probe || true
}

case "${1:-status}" in
  status)
    echo "Touch chip ($DEV) driver: $(driver)"
    command -v pinctrl >/dev/null && echo "Pen signal: $(pinctrl get 17 2>/dev/null)"
    systemctl is-enabled --quiet aira-touch-spi.service 2>/dev/null && echo "SPI touch at boot: enabled" \
      || echo "SPI touch at boot: not enabled"
    ;;
  bind)        # used by aira-touch-spi.service at boot
    need_root bind; bind_spidev ;;
  test)
    need_root test
    bind_spidev
    echo "Touch chip now on SPI (until reboot). Press the screen in different places for 20 s..."
    cd "$DIR"
    sudo -u "$USER_NAME" -g "$(group)" python3 -m aira_pi touchtest --seconds 20 || true
    echo
    echo "If the verdict says touch works:  sudo bash touch_spi.sh enable"
    echo "To go back now:                   sudo bash touch_spi.sh disable"
    ;;
  enable)
    need_root enable
    bind_spidev
    g="$(group)"; [[ $g != root ]] && usermod -aG "$g" "$USER_NAME"
    cat > "$UNIT" <<EOF
[Unit]
Description=AIRA: read the touch chip directly over SPI (pen signal not needed)
Before=aira-pi.service
After=systemd-modules-load.service

[Service]
Type=oneshot
ExecStart=/bin/bash $DIR/touch_spi.sh bind
RemainAfterExit=yes

[Install]
WantedBy=multi-user.target
EOF
    systemctl daemon-reload
    systemctl enable aira-touch-spi.service >/dev/null
    systemctl try-restart aira-pi.service || true
    echo "SPI touch enabled (now and at every boot). Line it up once:"
    echo "    sudo systemctl stop aira-pi && python3 -m aira_pi calibrate && sudo systemctl start aira-pi"
    ;;
  disable)
    need_root disable
    systemctl disable aira-touch-spi.service >/dev/null 2>&1 || true
    rm -f "$UNIT"; systemctl daemon-reload
    bind_kernel
    systemctl try-restart aira-pi.service || true
    echo "Back to the kernel touch driver: $(driver)"
    ;;
  *) echo "usage: sudo bash touch_spi.sh status|test|enable|disable"; exit 1 ;;
esac
