# AIRA — current project state

Last updated: 31 Oct 2026, from the hardware handover. **Verified** means seen on the live
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
| Display | **Working (31 Oct 2026):** 3.5" ILI9486 (panel HSD035577C3) as `/dev/fb1`, Linux console on VT1 via `aira-tft-console.service`; touch (ADS7846) detected. Details: [`journal/2026-10-31-display-setup.md`](journal/2026-10-31-display-setup.md) |
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
| AIRA server (this repo) running on port 8765 | **To verify**; it binds to 127.0.0.1 until the Pi link is set up |

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
| 3 | Display, then touch | ✅ console on the TFT (31 Oct). Touch detected; check events with `evtest` when a touch UI is needed |
| 4 | Audio, one module at a time: DAC → amplifier + speakers → microphone → full duplex | Each test in `docs/HARDWARE.md` §6 passes |
| 5 | Button | `check_hardware.py` detects presses |
| 6 | Power: test cells, then charger + boost on the bench | Voltage, ripple, temperature and charge-while-running validated under real load |
| 7 | AI client on the Pi talking to the laptop server | Pi gets a reply from the laptop over Wi-Fi with timeouts and offline handling |
| 8 | Enclosure | Measured parts fit with airflow, mic-speaker separation and USB-C/microSD access |

## Open questions

- [x] Exact display board and revision: HSD035577C3 panel, ILI9486 + XPT2046, vendor `tft35a` overlay.
- [ ] Which network the Pi is on right now, and its current address.
- [ ] Are INMP441, PCM5102A, PAM8403, speakers and button bought?
- [ ] Condition of both 18650 cells (capacity, internal resistance, resting voltage).
- [ ] Is the AIRA server running on the laptop, and which model will it use?
