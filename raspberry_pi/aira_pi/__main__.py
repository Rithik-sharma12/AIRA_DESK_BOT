"""AIRA on the Raspberry Pi.

  python3 -m aira_pi pair            pair this Pi with the laptop (once)
  python3 -m aira_pi run             show the face and link to the laptop (the service runs this)
  python3 -m aira_pi calibrate       line up the touch screen (once; stop the service first)
  python3 -m aira_pi touchtest       show what the touch controller sends (when taps do nothing)
  python3 -m aira_pi online setup    online AI on the Pi (API key), so AIRA works without the laptop
  python3 -m aira_pi discover        check that the laptop can be found on this network
  python3 -m aira_pi demo --out DIR  render every expression to PNG (works on any PC)
  python3 -m aira_pi info            print the saved settings (without secrets)
  python3 -m aira_pi about           version, release date, developer, Pi hardware and OS
  python3 -m aira_pi controller      token and port for the ESP32 joystick
  python3 -m aira_pi joystick        keyboard stand-in for the joystick (test without the ESP32)
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
    from .touch import (PressReader, apply_calibration, find_touch_device, fit_calibration, input_report,
                        read_raw_tap, touch_irq_count)
    from .touch_spi import diagnose, open_spi_touch, read_raw_tap_spi

    try:
        active = subprocess.run(["systemctl", "is-active", "--quiet", "aira-pi"], check=False).returncode == 0
    except OSError:
        active = False
    if active:
        print("The AIRA face service is using the screen. Stop it first:  sudo systemctl stop aira-pi")
        return 1
    fb_path = find_framebuffer(args.framebuffer or cfg.framebuffer)
    chip = open_spi_touch() if cfg.touch in ("auto", "spi") else None
    dev = None if chip else find_touch_device(cfg.touch_device)
    if not fb_path or (dev is None and chip is None):
        print("Need both the 3.5\" screen and its touch controller.")
        for line in input_report():
            print("  " + line)
        return 1
    print("Touch: XPT2046 read directly over SPI" if chip else f"Touch: {dev.name} ({dev.path})")

    def read_tap(timeout: float, why: list):
        """One press, raw; on failure ``why`` gets the reason."""
        if chip:
            samples: list = []
            got = read_raw_tap_spi(chip, timeout, cfg.touch_spi_threshold, samples)
            if got is None:
                why.append(diagnose(samples, cfg.touch_spi_threshold))
            return got
        import evdev

        irq0, reader = touch_irq_count(), PressReader(evdev.ecodes)
        got = read_raw_tap(dev, timeout=timeout, reader=reader)
        if got is None:
            irq1 = touch_irq_count()
            why.append(reader.summary() + ("" if irq0 is None or irq1 is None
                                           else f"; touch interrupts during the wait: {irq1 - irq0}"))
        return got
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
            why: list = []
            got = read_tap(60, why)
            if got is None:
                print(f"No tap within 60 s; calibration cancelled ({why[0] if why else 'no reason'}).")
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
            got = read_tap(max(0.1, end - time.monotonic()), [])
            if got:
                dots.append(apply_calibration(cal, *got, size=(w, h)))
        screen("TOUCH CALIBRATED")
        print("Done. Start the face again:  sudo systemctl start aira-pi")
        return 0
    finally:
        fb.close()
        if chip:
            chip.spi.close()


def cmd_touchtest(args, cfg: PiConfig) -> int:
    """Shows what the touch controller sends: devices, ranges, interrupts and each press (raw and on screen)."""
    import time

    print("Input devices:")
    from .touch import (PressReader, apply_calibration, default_calibration, find_touch_device, input_report,
                        read_raw_tap, touch_irq_count)

    for line in input_report():
        print("  " + line)
    if cfg.touch in ("auto", "spi"):
        from .touch_spi import open_spi_touch, spi_bound_driver

        chip = open_spi_touch()
        if chip is not None:
            return _touchtest_spi(chip, cfg, args.seconds)
        print(f"Touch chip driver: {spi_bound_driver() or 'none'} (kernel route)")
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
        print("Nothing arrived from the controller. If interrupts stayed at 0 too, the chip's pen signal "
              "(GPIO17) never fires.\nTry reading the chip directly instead (no pen signal needed):\n"
              "    sudo bash touch_spi.sh test")
    dev.close()
    return 0 if presses else 1


def _touchtest_spi(chip, cfg: PiConfig, seconds: float) -> int:
    import time

    from .touch import apply_calibration, default_calibration
    from .touch_spi import RAW_RANGE, diagnose, read_raw_tap_spi

    cal = cfg.touch_cal or default_calibration(RAW_RANGE, RAW_RANGE)
    print(f"Reading the XPT2046 directly over SPI (pen signal not needed). Press threshold: {cfg.touch_spi_threshold}")
    print(f"Press the screen a few times in the next {seconds:.0f} s (the AIRA service may keep running)...")
    samples: list = []
    presses, shown = 0, 0
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        got = read_raw_tap_spi(chip, min(0.5, max(0.05, end - time.monotonic())), cfg.touch_spi_threshold, samples)
        if got:
            presses += 1
            x, y = apply_calibration(cal, *got)
            print(f"  press {presses}: raw ({got[0]:.0f}, {got[1]:.0f})  ->  screen ({x}, {y})")
        elif samples and len(samples) - shown >= 25:          # a live reading twice a second
            shown = len(samples)
            x, y, p, z1, z2 = samples[-1]
            print(f"    x {x:4d}  y {y:4d}  pressure {p:4d}  (z1 {z1}, z2 {z2})")
    chip.spi.close()
    pressures = sorted(p for _, _, p, _, _ in samples)
    if pressures:
        print(f"Summary: {presses} presses; pressure min {pressures[0]}, median {pressures[len(pressures) // 2]}, "
              f"max {pressures[-1]} over {len(samples)} readings")
    print("Verdict: " + diagnose(samples, cfg.touch_spi_threshold))
    return 0 if presses else 1


def cmd_online(args, cfg: PiConfig) -> int:
    """Set up / test / switch off AIRA's online AI on the Pi (works without the laptop)."""
    import getpass

    from .online import SERVICES, OnlineBrain, OnlineError, base_url_for

    if args.action == "status":
        print(f"Brain: {cfg.brain}   service: {cfg.online_service or cfg.online_base_url or '-'}   "
              f"model: {cfg.online_model or '-'}   API key: {'set' if cfg.online_api_key else 'not set'}")
        print(f"Chat page: {'on, port ' + str(cfg.chat_port) if cfg.chat_page else 'off'}   code: {cfg.chat_code or '-'}")
        return 0
    if args.action == "off":
        cfg.brain, cfg.online_api_key = "laptop", None
        print(f"Online AI off, API key removed from {cfg.save()}. Restart AIRA:  sudo systemctl restart aira-pi")
        return 0
    if args.action == "setup":
        print("Online AI for AIRA on the Pi. What you type to her is sent to that service.")
        print("Services: " + ", ".join(SERVICES) + ", or 'custom' for another OpenAI-compatible URL.")
        service = input(f"Service [{cfg.online_service or 'gemini'}]: ").strip().lower() or cfg.online_service or "gemini"
        if service == "custom":
            cfg.online_service = ""
            cfg.online_base_url = input("Base URL (ends in /v1 or similar): ").strip()
        elif service in SERVICES:
            cfg.online_service, cfg.online_base_url = service, ""
        else:
            print("Unknown service.")
            return 1
        model = input(f"Model name from the service's model list [{cfg.online_model or ''}]: ").strip()
        cfg.online_model = model or cfg.online_model
        key = getpass.getpass("API key (typing is hidden; Enter keeps the current one): ").strip()
        if key:
            cfg.online_api_key = key
        mode = input("Brain: 'auto' = laptop when connected, else online AI; 'online' = always online "
                     f"[{cfg.brain if cfg.brain != 'laptop' else 'auto'}]: ").strip().lower()
        cfg.brain = mode if mode in ("auto", "online", "laptop") else (cfg.brain if cfg.brain != "laptop" else "auto")
        print(f"Saved to {cfg.save()} (only your user can read it).")
    try:
        brain = OnlineBrain(base_url_for(cfg.online_service, cfg.online_base_url), cfg.online_api_key or "",
                            cfg.online_model)
        print("Testing... AIRA says: ", end="", flush=True)
        for piece in brain.stream("Say hello to me in one short sentence."):
            print(piece, end="", flush=True)
        print("\nOnline AI works. Restart AIRA to use it:  sudo systemctl restart aira-pi")
        return 0
    except OnlineError as exc:
        print(f"\nNot working yet: {exc}")
        return 1


def cmd_about(args, cfg: PiConfig) -> int:
    """Version, release date, developer and this Pi's hardware and OS (same as the About screen)."""
    from .app import edition_name
    from .sysinfo import about_rows

    print("AIRA - Autonomous Intelligent Responsive Assistant")
    for label, value in about_rows(edition_name(cfg.edition), cfg.touch):
        print(f"  {label:<10} {value}")
    return 0


def cmd_controller(args, cfg: PiConfig) -> int:
    """What the ESP32 joystick needs: the Pi's controller port and its pairing token."""
    from .controller import BEACON_PORT, new_token
    from .app import local_ips

    if args.new_token or not cfg.controller_token:
        cfg.controller_token = new_token()
        cfg.save()
        print("New controller token made (restart AIRA:  sudo systemctl restart aira-pi).")
    ips = local_ips()
    state = "on" if cfg.controller else 'off: set "controller": true in the config'
    print(f"Controller server: ws://{ips[0] if ips else '<pi-ip>'}:{cfg.controller_port}/   ({state})")
    print(f"The Pi announces itself on UDP {BEACON_PORT}, so the ESP32 finds it without an IP address.")
    print("Put this line in esp32_joystick/aira_joystick/secrets.h (with your Wi-Fi name and password):")
    print(f'    #define AIRA_TOKEN "{cfg.controller_token}"')
    return 0


def cmd_joystick(args, cfg: PiConfig) -> int:
    """A keyboard stand-in for the ESP32: test the Pi side before the joystick is built."""
    import json

    from .controller import BEACON_PORT

    async def go() -> int:
        try:
            from websockets.asyncio.client import connect
        except ImportError:
            from websockets import connect  # type: ignore[no-redef]
        host = args.host
        if not host:                                       # find the Pi the way the ESP32 does
            print(f"Listening for the Pi's announcement on UDP {BEACON_PORT} ...")
            loop = asyncio.get_running_loop()
            got: asyncio.Future = loop.create_future()

            class Listen(asyncio.DatagramProtocol):
                def datagram_received(self, data, addr):
                    if not got.done() and b'"aira-pi"' in data:
                        got.set_result((addr[0], json.loads(data)["port"]))

            transport, _ = await loop.create_datagram_endpoint(Listen, local_addr=("0.0.0.0", BEACON_PORT),
                                                               allow_broadcast=True)
            try:
                host, port = await asyncio.wait_for(got, 10)
            except asyncio.TimeoutError:
                print("Nothing heard. Is AIRA running (sudo systemctl status aira-pi)?")
                return 1
            finally:
                transport.close()
        else:
            port = cfg.controller_port
        token = args.token or cfg.controller_token or ""
        from .app import no_proxy

        async with connect(f"ws://{host}:{port}/", **no_proxy(connect)) as ws:
            await ws.send(json.dumps({"event": "hello", "device": "keyboard-joystick", "token": token}))
            reply = json.loads(await ws.recv())
            if not reply.get("ok"):
                print("Refused:", reply.get("error"))
                return 1
            print(f"Connected to {reply.get('name')} at {host}:{port}.")
            print("Type w a s d (up left down right), e = select, b = back (long press), q = quit; then Enter.")
            keys = {"w": ("navigate", "up"), "s": ("navigate", "down"), "a": ("navigate", "left"),
                    "d": ("navigate", "right"), "e": ("select", None), "b": ("back", None)}
            while True:
                line = (await asyncio.to_thread(input, "> ")).strip().lower()
                if line == "q":
                    return 0
                for ch in line:
                    if ch in keys:
                        event, direction = keys[ch]
                        await ws.send(json.dumps({"event": event, **({"direction": direction} if direction else {})}))

    return asyncio.run(go())


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
    from .app import edition_name

    print(f"  device: {cfg.name} ({cfg.device_id}), edition: {edition_name(cfg.edition)}")
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
    o = sub.add_parser("online", help="online AI on the Pi (works without the laptop)")
    o.add_argument("action", choices=["setup", "test", "status", "off"], nargs="?", default="status")
    t = sub.add_parser("touchtest", help="show what the touch controller sends")
    t.add_argument("--seconds", type=float, default=20)
    d = sub.add_parser("discover")
    d.add_argument("--timeout", type=float, default=10)
    m = sub.add_parser("demo")
    m.add_argument("--out", default="aira-face-demo")
    m.add_argument("--theme", choices=["auto", "brutal", "mascot", "simple"], help="default: from the config")
    sub.add_parser("info")
    sub.add_parser("about", help="version, developer, Pi hardware and OS")
    k = sub.add_parser("controller", help="token and port for the ESP32 joystick")
    k.add_argument("--new-token", action="store_true", help="make a new token (the old one stops working)")
    j = sub.add_parser("joystick", help="keyboard stand-in for the ESP32 joystick (testing)")
    j.add_argument("--host", help="Pi address (default: find it by its announcement)")
    j.add_argument("--token", help="controller token (default: this Pi's own)")
    args = ap.parse_args(argv)
    _setup_logging(args.verbose)
    cfg = PiConfig.load()
    handler = {"run": cmd_run, "pair": cmd_pair, "calibrate": cmd_calibrate, "touchtest": cmd_touchtest, "online": cmd_online, "about": cmd_about,
               "controller": cmd_controller, "joystick": cmd_joystick,
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
