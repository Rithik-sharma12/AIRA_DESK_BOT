"""AIRA hardware check — run ON the Raspberry Pi after following docs/HARDWARE.md.

  sudo apt install -y python3-gpiozero alsa-utils iw   # usually already installed
  python3 raspberry_pi/check_hardware.py              # all checks, button on GPIO26
  python3 raspberry_pi/check_hardware.py --pin 5 --skip-audio

Checks: board model, power (under-voltage flags), Wi-Fi band and signal, display
framebuffer, touch input device, I²S (or USB) audio playback + recording, and the push button.
Prints PASS / WARN / FAIL per item and a summary to paste into docs/HARDWARE.md.
Uses only the standard library plus gpiozero for the button.
"""

from __future__ import annotations

import argparse
import glob
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

results: list[tuple[str, str, str]] = []


def report(status: str, name: str, detail: str = "") -> None:
    results.append((status, name, detail))
    print(f"[{status:<4}] {name}" + (f" — {detail}" if detail else ""))


def run(cmd: list[str], timeout: float = 15) -> tuple[int, str]:
    if not shutil.which(cmd[0]):
        return 127, f"{cmd[0]} not installed"
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return p.returncode, (p.stdout + p.stderr).strip()
    except subprocess.TimeoutExpired:
        return 124, "timed out"


def check_board() -> None:
    model = Path("/proc/device-tree/model")
    text = model.read_text(errors="ignore").strip("\x00\n") if model.exists() else "unknown"
    report("PASS" if "Zero 2" in text else "WARN", "Board", text)
    code, out = run(["vcgencmd", "get_throttled"])
    if code == 0:
        value = out.split("=")[-1]
        report("PASS" if value == "0x0" else "WARN", "Power",
               "no under-voltage" if value == "0x0" else f"throttled={value}: use a 5 V 2.5 A supply")
    else:
        report("WARN", "Power", out)


def check_wifi() -> None:
    code, out = run(["iw", "dev", "wlan0", "link"])
    if code != 0 or "Not connected" in out:
        report("FAIL", "Wi-Fi", out or "not connected")
        return
    freq = re.search(r"freq:\s*([\d.]+)", out)
    sig = re.search(r"signal:\s*(-?\d+)", out)
    ssid = re.search(r"SSID:\s*(.+)", out)
    f = float(freq.group(1)) if freq else 0
    s = int(sig.group(1)) if sig else None
    status = "PASS" if s is None or s > -70 else "WARN"
    report(status, "Wi-Fi", f"{ssid.group(1) if ssid else '?'} · {f:.0f} MHz · signal {s} dBm"
           + ("" if status == "PASS" else " (weak; move closer to the router)"))


def check_display() -> None:
    fbs = sorted(glob.glob("/dev/fb*"))
    if not fbs:
        report("FAIL", "Display framebuffer", "no /dev/fb*: add a display overlay (docs/HARDWARE.md 6.2)")
        return
    details = []
    for fb in fbs:
        name = Path(f"/sys/class/graphics/{Path(fb).name}/name")
        size = Path(f"/sys/class/graphics/{Path(fb).name}/virtual_size")
        n = name.read_text().strip() if name.exists() else "?"
        sz = size.read_text().strip() if size.exists() else "?"
        details.append(f"{fb}={n} {sz}")
    tft = any(("480" in d and "320" in d) or "ili" in d.lower() or "fb_" in d for d in details)
    report("PASS" if tft else "WARN", "Display framebuffer", "; ".join(details)
           + ("" if tft else " (no 480x320 / ILI device seen: TFT overlay not active yet, see docs/HARDWARE.md 6.2)"))


def check_touch() -> None:
    names = Path("/proc/bus/input/devices")
    text = names.read_text() if names.exists() else ""
    hit = [line.split("=", 1)[1].strip('"') for line in text.splitlines()
           if line.startswith("N: Name=") and re.search(r"ads7846|xpt2046|touch", line, re.I)]
    report("PASS" if hit else "WARN", "Touch controller", ", ".join(hit) or "not found (optional; the button covers core use)")


AUDIO_PREFERRED = re.compile(r"voicehat|hifiberry|i2s|dac|pcm5102|snd_rpi|simple|usb", re.I)
AUDIO_EXCLUDED = re.compile(r"hdmi|vc4|bcm2835|headphones", re.I)  # built-in outputs (KMS on or off)


def find_card(tool: str) -> tuple[str, str] | None:
    """(card number, description) of the I²S (or USB) card for playback ('aplay') or capture ('arecord').
    HDMI outputs are ignored; a card whose name looks like the I²S DAC/mic is preferred."""
    code, out = run([tool, "-l"])
    if code != 0:
        return None
    cards = []
    for line in out.splitlines():
        m = re.match(r"card (\d+): (\S+) \[(.+?)\]", line)
        if m and not AUDIO_EXCLUDED.search(m.group(2) + m.group(3)):
            cards.append((m.group(1), line.strip()))
    preferred = [c for c in cards if AUDIO_PREFERRED.search(c[1])]
    return (preferred or cards or [None])[0]


def check_audio() -> None:
    play, cap = find_card("aplay"), find_card("arecord")
    if play is None:
        report("FAIL", "Audio playback device",
               "only HDMI or nothing: add dtparam=i2s=on + the DAC overlay (docs/HARDWARE.md 6.3)")
    else:
        report("PASS", "Audio playback device", play[1])
    if cap is None:
        report("WARN", "Audio capture device",
               "none yet: expected until the mic + a full-duplex overlay are set up (6.3 step 3)")
    else:
        report("PASS", "Audio capture device", cap[1])
    if play is None:
        return
    with tempfile.TemporaryDirectory() as tmp:
        wav = f"{tmp}/t.wav"
        if cap is not None:
            input("  Audio test: press Enter, then say 'Hey AIRA, testing one two three' (4 s)... ")
            # plughw converts the I²S mic's 32-bit stereo frames to 16 kHz mono.
            code, out = run(["arecord", "-D", f"plughw:{cap[0]},0", "-f", "S16_LE", "-r", "16000",
                             "-c", "1", "-d", "4", wav], 10)
            if code != 0:
                report("FAIL", "Recording", out[-200:])
                return
            report("PASS", "Recording", "captured 4 s")
            code, out = run(["aplay", "-D", f"plughw:{play[0]},0", wav], 10)
            prompt = "  Did you hear your recording from the speakers? [y/n] "
        else:
            code, out = run(["speaker-test", "-D", f"plughw:{play[0]},0", "-c", "2", "-t", "wav", "-l", "1"], 20)
            prompt = "  Did you hear 'front left' and 'front right' from the speakers? [y/n] "
        if code != 0:
            report("FAIL", "Playback", out[-200:])
            return
    heard = input(prompt).strip().lower() == "y"
    report("PASS" if heard else "FAIL", "Playback", "heard" if heard else
           "not heard: check SCK->GND and XSMT on the DAC, amp power, volume (alsamixer)")


def check_button(pin: int, seconds: int) -> None:
    try:
        from gpiozero import Button
    except ImportError:
        report("WARN", "Button", "gpiozero missing: sudo apt install python3-gpiozero")
        return
    try:
        b = Button(pin, pull_up=True, bounce_time=0.05)
    except Exception as exc:  # pin busy (used by the display driver) or no GPIO access
        report("FAIL", "Button", f"GPIO{pin} unavailable: {exc}")
        return
    if b.is_pressed:
        report("WARN", "Button", f"GPIO{pin} reads pressed at rest: short circuit or wrong pin?")
        return
    print(f"  Press the button now (waiting {seconds} s)...")
    ok = b.wait_for_press(timeout=seconds)
    report("PASS" if ok else "FAIL", "Button", f"GPIO{pin} press detected" if ok else
           f"no press on GPIO{pin}: check wiring to pins 37/39 (or the pin you used)")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--pin", type=int, default=26, help="BCM GPIO of the button (default 26 = physical pin 37)")
    ap.add_argument("--button-timeout", type=int, default=15)
    ap.add_argument("--skip-audio", action="store_true")
    ap.add_argument("--skip-button", action="store_true")
    a = ap.parse_args()
    check_board()
    check_wifi()
    check_display()
    check_touch()
    if not a.skip_audio:
        check_audio()
    if not a.skip_button:
        check_button(a.pin, a.button_timeout)
    fails = sum(r[0] == "FAIL" for r in results)
    print(f"\n{fails} fail, {sum(r[0] == 'WARN' for r in results)} warn")
    print("\nPaste into docs/HARDWARE.md section 5:")
    for status, name, detail in results:
        print(f"| {name} | {detail} ({status}) |")
    return 1 if fails else 0


if __name__ == "__main__":
    raise SystemExit(main())
