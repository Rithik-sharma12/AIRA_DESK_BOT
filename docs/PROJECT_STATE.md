# AIRA — current project state

Last updated: 1 Nov 2026. **Current build: AIRA Half** (laptop + Pi screen only; you talk on the
web page). **AIRA Full** is the complete handset with microphone, speaker and battery.
First real link on 1 Nov: the Pi paired over HTTPS and showed AIRA following web conversations.
From the hardware handover (31 Oct): **Verified** means seen on the live
device. Everything else is **planned** until a real test confirms it. No passwords, keys
or IP addresses belong in this file.

## Raspberry Pi — verified

| Property | Value |
|---|---|
| Board | Raspberry Pi Zero 2 W Rev 1.0 (4 × Cortex-A53 @ 1 GHz, 512 MB; ~415 MiB usable) |
| OS | Debian GNU/Linux 13 (Trixie), aarch64 — **existing install, do not re-image** |
| Kernel | `6.18.50+rpt-rpi-v8` |
| Hostname / user | `AIRA` / `aira` |
| Storage | SanDisk Ultra 64 GB microSD: FAT32 `bootfs` (mounted at `/boot/firmware`), ext4 `rootfs` |
| Swap | ~415 MiB |
| Boot config | `arm_64bit=1`, `dtparam=audio=on`, `dtoverlay=tft35a:rotate=90`; `vc4-kms-v3d` commented out (31 Oct); regulatory domain `IN` |
| Display | **Working (31 Oct 2026):** 3.5" ILI9486 (panel HSD035577C3) as `/dev/fb1`, Linux console on VT1 via `aira-tft-console.service`; touch (ADS7846) detected. **1 Nov:** AIRA's face service runs and shows the mascot interface on the TFT. Touch sends **nothing**: 0 events and 0 interrupts while pressing; GPIO17 (pen IRQ) reads `lo` all the time, also with the Pi's pull-up on (`gpio=17=ip,pu` added under the `tft35a` line, backup made). Reading the chip directly over SPI (`touch_spi.sh test`) returns **all zeros** for X, Y, Z1 and Z2 at 2 MHz, 500, 100 and 50 kHz, pressed or not; SPI0 pins verified correct (GPIO9 = SPI0_MISO, 10 = MOSI, 11 = SCLK, `pinctrl`). **Conclusion: the XPT2046 touch controller does not respond (hardware): no power to the chip, header pin 26 (touch CS) not making contact, or a faulty chip/board.** SPI reader switched back off (`touch_spi.sh disable`). Next: inspect / reseat header pins 11, 17, 21, 26; measure 3.3 V on XPT2046 pin 1; else replace the screen (same overlay, no software change). AIRA runs without touch (web page controls). Details: [`journal/2026-10-31-display-setup.md`](journal/2026-10-31-display-setup.md) |
| Network | NetworkManager on `wlan0`; a profile for the phone hotspot exists (root:root, 0600). IP is DHCP and changes |
| SSH | `openssh-server` installed and enabled at boot (reachability still to test) |
| Remote access | Raspberry Pi Connect started, account signed in |

Several of these details were captured once and could have changed since.
`raspberry_pi/collect_baseline.sh` refreshes them without printing secrets.

## Laptop — verified / to verify

| Item | State |
|---|---|
| HP Omen 16, Ryzen 7, RTX 4060 8 GB, 16 GB RAM, Windows 11 | Verified (owner) |
| Ollama installed; models seen earlier: `qwen3:4b`, `qwen2.5-coder:7b` | Verified earlier |
| Gemma 4 model pulled | **To verify** (repo default `gemma4:e4b-it-qat`) |
| AIRA server (this repo) running on port 8765 | **Verified 31 Oct**: web UI paired and chatting (was slow with `gemma4:12b` + thinking on; fixed by `AIRA_LLM_THINK=false`, model choice in HOW-TO-RUN 2b) |
| Task automation (apps, status, media, timers, power) | Built and tested with a dry-run backend; **to verify on Windows** |
| Offline voices (Piper Tamil + English) | Download script ready; **to verify on the laptop** |

## Requirements from the owner

1. Compact and light: small solderable modules, not bulky USB peripherals.
2. Enclosure target about **10 × 10 cm**. Not yet confirmed: the display (about 86 × 56 mm on common 3.5" boards), the 18650 cells (18 × 65 mm each), the speakers and the wiring set the real size. Measure before designing it.
3. Keep the existing Raspberry Pi OS installation.
4. Network through the phone hotspot when needed; the laptop must join the same network.
5. **USB-C charging while AIRA is running.**
6. All AI inference on the HP Omen; nothing large on the Pi.
7. Extra components around **₹1,000**. A safe charge-while-running power stage will probably exceed this on its own; that trade-off is the owner's call.
8. Prefer India-based suppliers (Robu.in, Robocraze, Quartz Components and similar). Check current datasheets, pinouts, ratings, price and stock before buying.
9. Small, testable steps; one subsystem at a time.

## Planned components (not yet confirmed as bought, wired or working)

| Subsystem | Parts |
|---|---|
| Display | ✅ working — see the journal; touch UI not built yet |
| Audio out | PCM5102A I²S DAC → PAM8403 class-D amplifier → 2 × 28 mm speakers (4 Ω, 3 W examples) |
| Audio in | INMP441 I²S MEMS microphone |
| Control | Momentary push button on GPIO26 (pin 37) + GND (pin 39) |
| Power | USB-C input → power-path charger → protected 1S pack from two used `MR 18650-2000mAh` cells (**only if tested and matched**) → regulated 5.1 V boost |

This replaces the USB speakerphone proposed in the original design (design doc, sections C and J).
The trade-off: I²S modules are smaller and cheaper, but the microphone and speakers no longer
come with hardware echo cancellation. Echo cancellation must be done in software (Phase 4),
and the microphone must be mounted away from the speakers.

## Bring-up order on the Pi

These steps sit inside roadmap phases 5 (Raspberry Pi) and 6 (device link). Each step needs a passing test before the next one starts.

| Step | What | Done when |
|---|---|---|
| 1 | Baseline: `bash raspberry_pi/collect_baseline.sh` on AIRA | Report saved; SPI/I²S/overlay availability known |
| 2 | Network + SSH: Pi and laptop on the same hotspot, `ssh aira@AIRA.local` from Windows | Laptop and Pi can reach each other both ways |
| 2b | Optional: phone as Wi-Fi keyboard (`raspberry_pi/wifi_keyboard`) for typing on the TFT console | Paired phone types into the console; Ctrl/arrows/F-keys work |
| 3 | Display, then touch | ✅ console on the TFT (31 Oct). Touch menu built (Home / Chat / Music / Tools / Emotions / About / System) and showing on the Pi (1 Nov); also driven by the web page and, planned, an ESP32 joystick. Touch controller not responding: hardware fault suspected (see Display); calibrate after the fix or a replacement screen |
| 4 | Audio, one module at a time: DAC → amplifier + speakers → microphone → full duplex (`raspberry_pi/setup_audio.sh`) | Each test in `docs/HARDWARE.md` §6 passes; `setup_audio.sh --test` plays and records |
| 5 | Button | `check_hardware.py` detects presses |
| 5b | Wireless joystick: ESP32 WROOM + KY-023 (`esp32_joystick/`; firmware compiles for ESP32, not yet run on hardware) | Serial Monitor shows directions (stage 1); `python3 -m aira_pi joystick` moves the Pi's focus (stage 2); the ESP32 pairs and drives the menu (stage 3) |
| 6 | Power: test cells, then charger + boost on the bench | Voltage, ripple, temperature and charge-while-running validated under real load |
| 7 | AI client on the Pi talking to the laptop server (`raspberry_pi/aira_pi`, built; not yet run on the Pi) | `python3 -m aira_pi pair` succeeds; face shows "linked" and mirrors a laptop conversation; reconnects after the laptop restarts |
| 8 | Enclosure | Measured parts fit with airflow, mic-speaker separation and USB-C/microSD access |

## Open questions

- [x] Exact display board and revision: HSD035577C3 panel, ILI9486 + XPT2046, vendor `tft35a` overlay.
- [ ] Which network the Pi is on right now, and its current address.
- [ ] Are INMP441, PCM5102A, PAM8403, speakers and button bought?
- [ ] Condition of both 18650 cells (capacity, internal resistance, resting voltage).
- [ ] Is the AIRA server running on the laptop, and which model will it use?
