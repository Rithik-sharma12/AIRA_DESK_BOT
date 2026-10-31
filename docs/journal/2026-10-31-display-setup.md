# AIRA Display Setup --- Raspberry Pi Zero 2 W

> Reference notes for rebuilding, troubleshooting, or extending the AIRA
> display configuration. This records the setup that was confirmed
> working.

## 1. Hardware

  -----------------------------------------------------------------------
  Component                           Confirmed details
  ----------------------------------- -----------------------------------
  Raspberry Pi                        Raspberry Pi Zero 2 W, Rev 1.0

  Display                             3.5-inch SPI TFT touchscreen

  LCD controller                      ILI9486

  Touch controller                    XPT2046-compatible; overlay exposes
                                      it as `ti,ads7846`

  Panel marking                       HSD035577C3

  Resolution                          480 × 320

  Pixel format                        RGB565

  LCD framebuffer                     `/dev/fb1` (`fb_ili9486`)

  Touch input                         ADS7846; previously appeared as
                                      `/dev/input/event0`

  OS                                  Debian 13 (trixie), 64-bit

  Kernel at setup                     `6.18.50+rpt-rpi-v8`
  -----------------------------------------------------------------------

## 2. SPI and GPIO pin mapping

The working overlay uses SPI0:

  Signal                      BCM GPIO   Physical header pin
  ------------------------- ---------- ---------------------
  MOSI                          GPIO10                    19
  MISO                           GPIO9                    21
  SCLK                          GPIO11                    23
  LCD chip select (CE0)          GPIO8                    24
  Touch chip select (CE1)        GPIO7                    26
  LCD data/command (DC)         GPIO24                    18
  LCD reset                     GPIO25                    22
  Touch interrupt (IRQ)         GPIO17                    11

**Note:** BCM GPIO numbers and physical header pin numbers are
different. Do not mix them up.

## 3. Device-tree overlay

The vendor display package supplied the `tft35a` overlay
(`tft35a-overlay.dtb`).

Relevant overlay properties:

-   LCD compatible: `ilitek,ili9486`
-   Touch compatible: `ti,ads7846`
-   LCD uses `spi0.0`; touch uses `spi0.1`
-   LCD reset: GPIO25
-   LCD DC: GPIO24
-   Touch IRQ: GPIO17
-   Rotation: 90 degrees
-   Frame rate: 30 FPS
-   SPI maximum frequency: 16 MHz

Active setting in `/boot/firmware/config.txt`:

``` ini
dtoverlay=tft35a:rotate=90
```

The standard KMS overlay was commented out:

``` ini
#dtoverlay=vc4-kms-v3d
```

The vendor `LCD35-show` script was run successfully during initial
setup. **Do not rerun it casually**; it can rewrite boot/display
configuration.

## 4. Framebuffer and Linux console

The system exposes `/dev/fb0` and `/dev/fb1`. The TFT is `/dev/fb1`,
driven by `fb_ili9486`, at 480 × 320 RGB565.

Linux virtual terminal 1 (VT1, normally `/dev/tty1`) is mapped to
framebuffer 1. The commands used to apply the mapping manually were:

``` bash
sudo con2fbmap 1 1
sudo chvt 1
```

Console-output test:

``` bash
echo "AIRA - Raspberry Pi Console" | sudo tee /dev/tty1
```

There is no X11/Wayland desktop session in this configuration. The TFT
is being used as a Linux text console, not as a mirrored desktop.

## 5. Persistent systemd service

A systemd oneshot service restores the VT1-to-TFT mapping at boot.

File: `/etc/systemd/system/aira-tft-console.service`

``` ini
[Unit]
Description=AIRA TFT Console Mapping
After=systemd-user-sessions.service
Before=getty@tty1.service

[Service]
Type=oneshot
ExecStart=/usr/bin/con2fbmap 1 1
RemainAfterExit=yes

[Install]
WantedBy=multi-user.target
```

The service was enabled. Inspect it with:

``` bash
systemctl status aira-tft-console.service
systemctl is-enabled aira-tft-console.service
```

Reapply the mapping manually:

``` bash
sudo systemctl restart aira-tft-console.service
sudo chvt 1
```

## 6. Confirmed working state

-   TFT displays the Linux text console.
-   TFT framebuffer is `/dev/fb1`, identified as `fb_ili9486`.
-   Framebuffer dimensions are 480 × 320.
-   VT1 is mapped to framebuffer 1.
-   The mapping survives reboot through `aira-tft-console.service`.
-   The ADS7846 touchscreen driver appears in the Linux input-device
    list.
-   No desktop environment is configured.

## 7. Diagnostic commands

Run these before changing settings:

``` bash
# OS and kernel
cat /etc/os-release
uname -a

# Framebuffer devices and driver names
ls -l /dev/fb*
cat /proc/fb

# Framebuffer details (if fbset is installed)
fbset -fb /dev/fb1

# VT1 mapping
con2fbmap 1

# Service status
systemctl status aira-tft-console.service
systemctl is-enabled aira-tft-console.service

# Touch and other input devices
cat /proc/bus/input/devices
ls -l /dev/input/

# Relevant boot settings
grep -nE 'dtoverlay|vc4-kms|dtparam=spi' /boot/firmware/config.txt
```

## 8. Backup and recovery

A copy of the original boot configuration was saved at:

`/home/aira/aira-config-before-tft.txt`

Before editing `/boot/firmware/config.txt` in the future, create a fresh
backup:

``` bash
sudo cp /boot/firmware/config.txt \
  /boot/firmware/config.txt.backup.$(date +%Y%m%d-%H%M%S)
```

After changing an overlay or framebuffer setting, reboot and verify the
framebuffer and console mapping. Change one setting at a time so that
failures are easier to isolate.

## 9. Project constraints and cautions

-   Preserve the current Debian installation, Wi-Fi configuration, SSH
    administration access, and working TFT setup.
-   Do not reinstall the OS just to configure the display.
-   Do not rerun `LCD35-show` unless deliberately rebuilding the display
    setup and a backup is available.
-   Touch input is detected, but a touch-driven graphical interface has
    not been configured as part of this setup.
-   Keep future audio and button GPIO assignments clear of the display
    pins listed above.

------------------------------------------------------------------------

**Reference note:** This describes the configuration observed working on
AIRA. If the display is rewired or the OS/kernel is upgraded, verify
actual device names and GPIO assignments rather than assuming they are
unchanged.
