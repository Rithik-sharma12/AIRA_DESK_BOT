"""AIRA on the Raspberry Pi.

  python3 -m aira_pi pair            pair this Pi with the laptop (once)
  python3 -m aira_pi run             show the face and link to the laptop (the service runs this)
  python3 -m aira_pi calibrate       line up the touch screen (once; stop the service first)
  python3 -m aira_pi touchtest       show what the touch controller sends (when taps do nothing)
  python3 -m aira_pi discover        check that the laptop can be found on this network
  python3 -m aira_pi demo --out DIR  render every expression to PNG (works on any PC)
  python3 -m aira_pi info            print the saved settings (without secrets)
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import signal
import sys
from pathlib import Path

from . import __version__
from .config import PiConfig, config_path


def _setup_logging(verbose: bool) -> None:
    logging.basicConfig(level=logging.DEBUG if verbose else logging.INFO,
                        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s", datefmt="%H:%M:%S")


def cmd_run(args, cfg: PiConfig) -> int:
    from .app import PiApp
    from .display import Framebuffer, NullDisplay, find_framebuffer

    fb = find_framebuffer(args.framebuffer or cfg.framebuffer)
    if fb:
        display = Framebuffer(fb, rotate=cfg.rotate)
    else:
        logging.getLogger("aira_pi").warning("No framebuffer found; running without a screen")
        display = NullDisplay()
    app = PiApp(cfg, display)

    async def main() -> None:
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, app.stop)
        await app.run()

    asyncio.run(main())
    if app.exit_to_console:
        print("AIRA face stopped on request (button held). Start again: sudo systemctl start aira-pi")
    return 0


def cmd_pair(args, cfg: PiConfig) -> int:
    from .app import pair

    return asyncio.run(pair(cfg, args.laptop, args.code, args.name))


def cmd_calibrate(args, cfg: PiConfig) -> int:
    """Tap four targets; fit raw touch -> screen; then a 15 s test where dots follow your taps."""
    import subprocess
    import time

    from PIL import Image, ImageDraw

    from .display import Framebuffer, find_framebuffer
    import evdev

    from .touch import (PressReader, apply_calibration, find_touch_device, fit_calibration, input_report,
                        read_raw_tap, touch_irq_count)

    try:
        active = subprocess.run(["systemctl", "is-active", "--quiet", "aira-pi"], check=False).returncode == 0
    except OSError:
        active = False
    if active:
        print("The AIRA face service is using the screen. Stop it first:  sudo systemctl stop aira-pi")
        return 1
    fb_path = find_framebuffer(args.framebuffer or cfg.framebuffer)
    dev = find_touch_device(cfg.touch_device)
    if not fb_path or dev is None:
        print("Need both the 3.5\" screen and its touch controller.")
        for line in input_report():
            print("  " + line)
        return 1
    print(f"Touch: {dev.name} ({dev.path})")
    fb = Framebuffer(fb_path, rotate=cfg.rotate)
    w, h = fb.size
    paper, black, teal = (245, 245, 220), (0, 0, 0), (0, 194, 203)

    def screen(msg: str, target=None, dots=()):
        img = Image.new("RGB", (w, h), paper)
        d = ImageDraw.Draw(img)
        d.text((w // 2, h // 2 - 10), msg, fill=black, anchor="mm")
        if target:
            x, y = target
            d.line((x - 18, y, x + 18, y), fill=black, width=3)
            d.line((x, y - 18, x, y + 18), fill=black, width=3)
            d.rectangle((x - 6, y - 6, x + 6, y + 6), outline=black, width=2, fill=teal)
        for x, y in dots:
            d.ellipse((x - 5, y - 5, x + 5, y + 5), fill=black)
        fb.show(img)

    targets = [(40, 40), (w - 40, 40), (w - 40, h - 40), (40, h - 40)]
    raw = []
    try:
        for i, tgt in enumerate(targets, 1):
            screen(f"TAP THE TARGET ({i}/4) - use a stylus or fingernail", tgt)
            print(f"Tap target {i}/4 ...")
            irq0 = touch_irq_count()
            reader = PressReader(evdev.ecodes)
            got = read_raw_tap(dev, timeout=60, reader=reader)
            if got is None:
                irq1 = touch_irq_count()
                print(f"No tap within 60 s; calibration cancelled ({reader.summary()}).")
                if irq0 is not None and irq1 is not None:
                    print(f"  touch interrupts during the wait: {irq1 - irq0}")
                print("  Run  python3 -m aira_pi touchtest  and send me what it prints.")
                return 1
            raw.append(got)
            time.sleep(0.3)
        cal = fit_calibration(raw, targets)
        cfg.touch_cal = cal
        path = cfg.save()
        print(f"Saved to {path}. Test: tap around the screen for 15 s; dots should land under your finger.")
        dots = []
        end = time.monotonic() + 15
        while time.monotonic() < end:
            screen("TEST: DOTS SHOULD LAND WHERE YOU TAP", dots=dots)
            got = read_raw_tap(dev, timeout=max(0.1, end - time.monotonic()))
            if got:
                dots.append(apply_calibration(cal, *got, size=(w, h)))
        screen("TOUCH CALIBRATED")
        print("Done. Start the face again:  sudo systemctl start aira-pi")
        return 0
    finally:
        fb.close()


def cmd_touchtest(args, cfg: PiConfig) -> int:
    """Shows what the touch controller sends: devices, ranges, interrupts and each press (raw and on screen)."""
    import time

    print("Input devices:")
    from .touch import (PressReader, apply_calibration, default_calibration, find_touch_device, input_report,
                        read_raw_tap, touch_irq_count)

    for line in input_report():
        print("  " + line)
    dev = find_touch_device(cfg.touch_device)
    if dev is None:
        print("No touch controller found. Check:  dmesg | grep -i ads7846")
        return 1
    import evdev

    e = evdev.ecodes
    ax, ay = dev.absinfo(e.ABS_X), dev.absinfo(e.ABS_Y)
    print(f"Using {dev.path}: {dev.name}   raw X {ax.min}..{ax.max}, raw Y {ay.min}..{ay.max}")
    cal = cfg.touch_cal or default_calibration((ax.min, ax.max), (ay.min, ay.max))
    print("Calibration: " + ("saved" if cfg.touch_cal else "not yet (using a guess)"))
    irq0 = touch_irq_count()
    print(f"Press the screen a few times in the next {args.seconds:.0f} s (the AIRA service may keep running)...")
    reader = PressReader(e)
    end = time.monotonic() + args.seconds
    presses = 0
    while time.monotonic() < end:
        got = read_raw_tap(dev, timeout=max(0.1, end - time.monotonic()), reader=reader)
        if got:
            presses += 1
            x, y = apply_calibration(cal, *got)
            print(f"  press {presses}: raw ({got[0]:.0f}, {got[1]:.0f})  ->  screen ({x}, {y})")
    irq1 = touch_irq_count()
    c = reader.counts
    print(f"Summary: {presses} presses; {c['events']} events ({c['xy']} position, {c['btn']} touch up/down, "
          f"{c['pressure']} pressure)" + ("" if irq0 is None or irq1 is None
                                          else f"; {irq1 - irq0} touch interrupts"))
    if not c["events"]:
        print("Nothing arrived from the controller. If interrupts stayed at 0 too, the touch chip is not "
              "signalling the Pi (driver / overlay), not an AIRA problem.")
    dev.close()
    return 0 if presses else 1


def cmd_discover(args, cfg: PiConfig) -> int:
    from .net import discover, pretty_fingerprint

    print(f"Listening for the laptop's beacon on UDP {cfg.discovery_port} for {args.timeout:.0f} s...")
    b = asyncio.run(discover(cfg.discovery_port, timeout=args.timeout))
    if not b:
        print("Nothing heard. Is the laptop server running with AIRA_ALLOW_LAN=true on the same network?")
        return 1
    match = "matches the paired laptop" if cfg.fingerprint and b.fingerprint == cfg.fingerprint else \
        ("DIFFERENT from the paired laptop" if cfg.fingerprint else "not paired yet")
    print(f"Found {b.host} at {b.url}  certificate {pretty_fingerprint(b.fingerprint)} ({match})")
    return 0


def cmd_demo(args, cfg: PiConfig) -> int:
    import time

    from .display import PreviewDisplay
    from .protocol import EXPRESSIONS
    from .theme import create_renderer

    out = PreviewDisplay(Path(args.out))
    face = create_renderer(args.theme or cfg.theme)
    face.state.linked = True
    now = time.monotonic()
    for e in EXPRESSIONS:
        face.set_expression(e, now=now)
        face.set_caption(f"This is AIRA's {e} face.", now=now)
        out.show(face.render(now + 0.4), name=f"{e}.png")
    face.set_caption("", now=now)
    face.notify("Connected to the laptop")
    out.show(face.render(), name="notification.png")
    face.show_info(["Pi: AIRA", "Wi-Fi: hotspot", "Link: connected"])
    out.show(face.render(), name="status.png")
    face.state.info_until = 0
    if hasattr(face.state, "notice_until"):
        face.state.notice_until = 0
    face.state.linked = False
    out.show(face.render(), name="offline.png")
    print(f"Wrote {len(EXPRESSIONS) + 3} PNGs ({type(face).__name__}) to {args.out}")
    return 0


def cmd_info(args, cfg: PiConfig) -> int:
    from .net import pretty_fingerprint

    print(f"aira_pi {__version__}, config {config_path()}")
    print(f"  device: {cfg.name} ({cfg.device_id})")
    print(f"  paired: {'yes' if cfg.paired else 'no'}   laptop (last known): {cfg.laptop_url or '-'}")
    print(f"  laptop certificate: {pretty_fingerprint(cfg.fingerprint)}")
    print(f"  button GPIO{cfg.button_gpio}, audio={cfg.audio}, fps={cfg.fps}, rotate={cfg.rotate}, theme={cfg.theme}")
    print(f"  touch: {cfg.touch}, {'calibrated' if cfg.touch_cal else 'not calibrated (python3 -m aira_pi calibrate)'}")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python3 -m aira_pi", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("-v", "--verbose", action="store_true")
    sub = ap.add_subparsers(dest="cmd")
    r = sub.add_parser("run")
    r.add_argument("--framebuffer", help="e.g. /dev/fb1 (default: auto-detect the ILI9486)")
    p = sub.add_parser("pair")
    p.add_argument("--laptop", help="https://<laptop-ip>:8765 (default: find it automatically)")
    p.add_argument("--code", help="the 6-digit code (default: ask)")
    p.add_argument("--name", help="name shown on the laptop, default 'AIRA Pi'")
    c = sub.add_parser("calibrate")
    c.add_argument("--framebuffer")
    t = sub.add_parser("touchtest", help="show what the touch controller sends")
    t.add_argument("--seconds", type=float, default=20)
    d = sub.add_parser("discover")
    d.add_argument("--timeout", type=float, default=10)
    m = sub.add_parser("demo")
    m.add_argument("--out", default="aira-face-demo")
    m.add_argument("--theme", choices=["auto", "brutal", "mascot", "simple"], help="default: from the config")
    sub.add_parser("info")
    args = ap.parse_args(argv)
    _setup_logging(args.verbose)
    cfg = PiConfig.load()
    handler = {"run": cmd_run, "pair": cmd_pair, "calibrate": cmd_calibrate, "touchtest": cmd_touchtest,
               "discover": cmd_discover, "demo": cmd_demo,
               "info": cmd_info}.get(args.cmd or "run")
    if args.cmd is None:
        args.framebuffer = None
    try:
        return handler(args, cfg)
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())
