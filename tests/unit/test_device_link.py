"""Raspberry Pi side: protocol copy, discovery, display conversion, audio detection."""

from __future__ import annotations

import asyncio
import json
import socket
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "raspberry_pi"))
pytest.importorskip("PIL", reason="Pi tests need Pillow (pip install -r requirements-dev.txt)")
pytest.importorskip("numpy", reason="Pi tests need numpy (pip install -r requirements-dev.txt)")

from aira_pi import protocol as pi_protocol  # noqa: E402
from aira_pi.audio import find_card  # noqa: E402
from aira_pi.display import Framebuffer, to_rgb565  # noqa: E402
from aira_pi.face import FaceRenderer  # noqa: E402
from aira_pi.net import discover, parse_beacon, ws_url  # noqa: E402
from laptop_server.api.device_routes import event_to_envelope  # noqa: E402
from laptop_server.discovery import beacon_payload, cert_fingerprint  # noqa: E402
from shared.protocol import PROTOCOL_VERSION, Envelope, Expression, MessageType  # noqa: E402


def test_pi_protocol_copy_matches_shared_protocol():
    assert pi_protocol.PROTOCOL_VERSION == PROTOCOL_VERSION
    assert set(pi_protocol.MESSAGE_TYPES) == {m.value for m in MessageType}
    assert set(pi_protocol.EXPRESSIONS) == {e.value for e in Expression}
    frame = pi_protocol.envelope("USER_TEXT", "aira-pi-test", {"text": "hi"}, "dev-x")
    env = Envelope.from_json(frame)  # the laptop's strict validator accepts the Pi's frames
    assert env.type == MessageType.USER_TEXT and env.payload == {"text": "hi"}
    assert pi_protocol.parse('{"type": "NOPE"}') is None and pi_protocol.parse("garbage") is None


def test_hub_events_become_protocol_frames():
    f = json.loads(event_to_envelope({"type": "state", "state": "speaking", "estimate_s": 3.2}))
    assert f["type"] == "DISPLAY_STATE_CHANGED" and f["payload"] == {"expression": "speaking", "hold_s": 3.2}
    f = json.loads(event_to_envelope({"type": "reminder", "text": "tea", "say": "Reminder: tea"}))
    assert f["type"] == "REMINDER" and f["payload"]["say"] == "Reminder: tea"
    assert event_to_envelope({"type": "wake"}) is None  # for web pages only
    f = json.loads(event_to_envelope({"type": "screen", "screen": "music"}))
    assert f["type"] == "SHOW_SCREEN" and f["payload"] == {"screen": "music"}
    assert event_to_envelope({"type": "screen", "screen": "../etc"}) is None
    for frame in (event_to_envelope({"type": "caption", "role": "aira", "text": "x"}),
                  event_to_envelope({"type": "confirm", "prompt": "Shut down?"})):
        assert pi_protocol.parse(frame) is not None


def test_beacon_roundtrip_and_fingerprint(tmp_path):
    payload = beacon_payload(8765, True, "ab" * 32)
    b = parse_beacon(payload, "192.168.43.20")
    assert b.url == "https://192.168.43.20:8765" and b.fingerprint == "ab" * 32
    assert parse_beacon(b'{"service":"other"}', "1.2.3.4") is None
    assert parse_beacon(b"\xff\xfe", "1.2.3.4") is None
    assert ws_url("https://10.0.0.5:8765") == "wss://10.0.0.5:8765/api/v1/device/ws"
    assert cert_fingerprint(tmp_path / "missing.crt") is None


def test_discover_hears_beacon_and_respects_pinned_fingerprint():
    port = 20000 + (id(object()) % 20000)

    async def go():
        async def send(fp):
            await asyncio.sleep(0.2)
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as tx:
                tx.sendto(beacon_payload(8765, True, fp), ("127.0.0.1", port))

        asyncio.get_running_loop().create_task(send("11" * 32))
        wrong = await discover(port, timeout=0.8, fingerprint="22" * 32)
        asyncio.get_running_loop().create_task(send("22" * 32))
        right = await discover(port, timeout=2, fingerprint="22" * 32)
        return wrong, right

    wrong, right = asyncio.run(go())
    assert wrong is None and right.url == "https://127.0.0.1:8765"


def test_rgb565_and_framebuffer_writes_only_changed_rows(tmp_path):
    from PIL import Image, ImageDraw

    red = to_rgb565(Image.new("RGB", (1, 1), (255, 0, 0)))
    assert red == b"\x00\xf8"
    assert to_rgb565(Image.new("RGB", (1, 1), (0, 0, 255))) == b"\x1f\x00"

    path = tmp_path / "fb"
    path.write_bytes(bytes(480 * 320 * 2))
    fb = Framebuffer.__new__(Framebuffer)
    fb.path, fb.rotate, fb.width, fb.height, fb.bpp, fb.stride = str(path), 0, 480, 320, 16, 960
    import os

    fb._fd, fb._last = os.open(path, os.O_RDWR), None
    img = Image.new("RGB", (480, 320), (11, 15, 23))
    assert fb.show(img) == 320
    assert fb.show(img) == 0
    ImageDraw.Draw(img).rectangle((100, 50, 200, 59), fill=(255, 255, 255))
    assert fb.show(img) == 10
    data = path.read_bytes()
    assert data[50 * 960 + 200:50 * 960 + 202] == b"\xff\xff"
    fb.close()


def test_face_renders_every_expression_and_returns_to_idle():
    face = FaceRenderer(seed=3)
    face.state.linked = True
    for e in pi_protocol.EXPRESSIONS:
        face.set_expression(e, now=0)
        assert face.render(now=0.5).size == (480, 320)
    face.set_expression("speaking", hold_s=2, now=0)
    face.render(now=1)
    assert face.state.expression == "speaking"
    face.render(now=2.5)
    assert face.state.expression == "idle"
    face.state.linked = False  # offline: AIRA sleeps
    face.render(now=3)


@pytest.mark.parametrize("listing,expect", [
    ("card 0: vc4hdmi [vc4-hdmi], device 0: MAI PCM i2s-hifi-0\ncard 1: sndrpigooglevoi [snd_rpi_googlevoicehat_soundcar], device 0",
     "sndrpigooglevoi"),
    ("card 0: Headphones [bcm2835 Headphones], device 0", None),
    ("", None),
])
def test_audio_card_detection(listing, expect):
    assert find_card(listing) == expect


# ------------------------------------------------------------------ mascot theme
def test_mascot_theme_renders_every_screen():
    from aira_pi.theme import EXPRESSION_TO_SPRITE, ThemeRenderer, create_renderer, load_sprites

    sprites = load_sprites()
    assert set(sprites) >= {"idle", "happy", "thinking", "listening", "speaking", "excited", "confused",
                            "sleepy", "error"}
    assert all(f.size == (300, 240) for a in sprites.values() for f in a.frames)
    r = ThemeRenderer(sprites)
    r.state.linked = True
    for e in pi_protocol.EXPRESSIONS:
        r.set_expression(e, now=0)
        r.set_caption("Opening Chrome. " * 12, now=0)       # long text pages
        assert r.render(now=0.5).size == (480, 320)
        assert r._sprite_name(e) in sprites
    assert EXPRESSION_TO_SPRITE["sleep"] == "sleepy"
    r.notify("Connected to OMEN")
    r.render()
    r.show_info(["Pi: AIRA 10.0.0.2", "Link: connected"])
    r.render()
    r.state.info_until = 0
    r.state.linked = False                                 # laptop away: clock screen
    r.render()
    assert isinstance(create_renderer("simple"), FaceRenderer)
    assert isinstance(create_renderer("auto"), ThemeRenderer)


def test_mascot_theme_wraps_mixed_tamil_and_english():
    from aira_pi.theme import ThemeRenderer

    r = ThemeRenderer()
    runs = r._runs("சரி, உங்க Python error-ஐ பார்க்கலாம்")
    assert [s for _, s in runs][0].startswith("சரி") and any("Python" in s for _, s in runs)
    lines = r._wrap("word " * 60, 130)
    assert len(lines) > 5 and all(r._measure(l) <= 130 for l in lines)


def test_missing_sprites_fall_back_to_simple_face(tmp_path):
    from aira_pi.theme import ThemeRenderer, load_sprites

    assert load_sprites(tmp_path) == {}
    with pytest.raises(FileNotFoundError):
        ThemeRenderer(sprites={})


def test_blink_plays_over_the_loop_and_speaking_moves_the_mouth():
    from aira_pi.theme import BLINK_FPS, ThemeRenderer, load_sprites

    sprites = load_sprites()
    assert sprites["idle"].blink and len(sprites["speaking"].frames) >= 3
    r = ThemeRenderer(sprites)
    r._next_blink = 10.0
    idle = sprites["idle"]
    assert r._frame("idle", 9.0) is idle.frames[0]
    assert r._frame("idle", 10.0 + 1.5 / BLINK_FPS) is idle.blink[1]      # mid-blink: eyes closed
    assert r.animating(9.7)                                               # speeds up just before a blink
    r._frame("idle", 10.0 + len(idle.blink) / BLINK_FPS + 0.01)           # blink over: next one scheduled
    assert r._next_blink > 10.4
    shapes = {id(sprites["speaking"].frame(t / 7)) for t in range(6)}
    assert len(shapes) == 3                                               # three mouth shapes cycle


def test_transparent_character_frames_are_placed_on_the_background(tmp_path):
    import json

    from PIL import Image

    from aira_pi.theme import load_sprites

    (tmp_path / "idle").mkdir()
    char = Image.new("RGBA", (160, 160), (0, 0, 0, 0))
    char.paste((250, 180, 150, 255), (40, 20, 120, 160))                   # a "character" standing at the bottom
    char.save(tmp_path / "idle" / "frame_01.png")
    Image.new("RGB", (300, 240), (30, 20, 60)).save(tmp_path / "background.png")
    (tmp_path / "manifest.json").write_text(json.dumps({"emotions": {"idle": {"fps": 2, "frames": ["frame_01.png"]}}}))
    frame = load_sprites(tmp_path)["idle"].frames[0]
    assert frame.size == (300, 240) and frame.mode == "RGB"
    assert frame.getpixel((5, 5)) == (30, 20, 60)                         # background shows through
    assert frame.getpixel((150, 235)) == (250, 180, 150)                  # character stands on the bottom edge


# --------------------------------------------------------- touch + brutal Pi interface
def _press_events(kind: str):
    """Raw evdev-like events for one press at about (1000, 3000), in three driver styles."""
    from types import SimpleNamespace as NS

    E = NS(EV_SYN=0, EV_KEY=1, EV_ABS=3, SYN_REPORT=0, ABS_X=0, ABS_Y=1, ABS_PRESSURE=24, BTN_TOUCH=330)
    ev = lambda t, c, v: NS(type=t, code=c, value=v)  # noqa: E731
    frames = []
    for i, (x, y) in enumerate([(990, 2990), (1000, 3000), (1010, 3010)]):
        f = [ev(E.EV_ABS, E.ABS_X, x), ev(E.EV_ABS, E.ABS_Y, y)]
        if kind == "btn" and i == 0:
            f.append(ev(E.EV_KEY, E.BTN_TOUCH, 1))
        if kind == "pressure":
            f.append(ev(E.EV_ABS, E.ABS_PRESSURE, 200))
        frames += f + [ev(E.EV_SYN, E.SYN_REPORT, 0)]
    if kind == "btn":
        frames += [ev(E.EV_KEY, E.BTN_TOUCH, 0), ev(E.EV_SYN, E.SYN_REPORT, 0)]
    if kind == "pressure":
        frames += [ev(E.EV_ABS, E.ABS_PRESSURE, 0), ev(E.EV_SYN, E.SYN_REPORT, 0)]
    return E, frames


@pytest.mark.parametrize("kind", ["btn", "pressure", "neither"])
def test_press_reader_handles_every_driver_style(kind):
    from aira_pi.touch import PressReader

    E, events = _press_events(kind)
    r = PressReader(E, gap_s=0.25)
    got = None
    for ev in events:
        got = got or r.feed(ev, now=1.0)
    if kind == "neither":                        # no "finger up" event: a short pause ends the press
        assert got is None and r.idle(now=1.1) is None
        got = r.idle(now=1.3)
    assert got == (1000.0, 3000.0)
    assert "position" in r.summary() and PressReader(E).summary() == "the touch controller sent nothing at all"


def test_install_script_python_check_runs():
    """The module check inside install.sh must at least run (it once crashed on importlib.util)."""
    import re
    import subprocess

    text = (ROOT / "raspberry_pi" / "install.sh").read_text(encoding="utf-8")
    code = re.search(r"python3 - <<'PY'\n(.*?)\nPY\n", text, re.S).group(1)
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert "Traceback" not in out.stderr
    assert out.returncode == 0 or "ERROR: missing Python modules" in out.stdout


def test_touch_calibration_fit_and_tap_detection():
    from aira_pi.touch import TapDetector, apply_calibration, default_calibration, fit_calibration

    # simulate a panel whose raw axes are swapped and scaled (like tft35a rotate=90)
    def raw_of(x, y):
        return 200 + y * 11.5, 300 + x * 7.6
    targets = [(40, 40), (440, 40), (440, 280), (40, 280)]
    cal = fit_calibration([raw_of(*t) for t in targets], targets)
    for x, y in ((240, 160), (10, 300), (470, 5)):
        assert apply_calibration(cal, *raw_of(x, y)) == (x, y)
    guess = default_calibration((0, 4095), (0, 4095))
    assert apply_calibration(guess, 4095, 4095) == (479, 319)

    taps, clock = [], [0.0]
    det = TapDetector(lambda rx, ry: (int(rx), int(ry)), lambda x, y: taps.append((x, y)), clock=lambda: clock[0])
    det.axis(0, 100); det.axis(1, 50); det.touch(True); clock[0] = 0.2; det.touch(False)
    assert taps == [(100, 50)]
    det.touch(True); det.axis(0, 300); clock[0] = 0.3; det.touch(False)          # a drag, not a tap
    det.touch(True); clock[0] = 5.0; det.touch(False)                            # a long press, not a tap
    assert taps == [(100, 50)]


def test_brutal_screens_navigation_and_confirmations():
    from aira_pi.ui_brutal import SCREENS, BrutalRenderer

    r = BrutalRenderer()
    r.state.linked = True
    t = 100.0
    for s in SCREENS:                                   # every screen renders, every rail button works
        r.render(now=t)
        rect = next(rect for rect, a in r._hits if a == f"nav:{s}")
        assert rect[3] - rect[1] >= 44                    # touch rows stay at least 44 px tall
        assert r.tap(28, (rect[1] + rect[3]) // 2, now=t) is None and r.screen == s
    r.screen = "music"; r.render(now=t)
    assert r.tap(150, 100, now=t) == "say:previous song"
    r.screen = "tools"; r.render(now=t)
    assert r.tap(150, 90, now=t) == "say:How is my laptop doing?"
    r.screen = "system"; r.render(now=t)
    shutdown = next(rect for rect, a in r._hits if a == "sys:poweroff")
    cx, cy = (shutdown[0] + shutdown[2]) // 2, (shutdown[1] + shutdown[3]) // 2
    assert r.tap(cx, cy, now=t) is None                 # first tap only arms it
    r.render(now=t + 0.5)
    assert r.tap(cx, cy, now=t + 1) == "sys:poweroff"   # second tap within 4 s confirms
    assert r.tap(cx, cy, now=t + 20) is None            # much later: arms again
    console = next(rect for rect, a in r._hits if a == "sys:console")
    assert r.tap(console[0] + 5, console[1] + 5, now=t + 21) == "sys:console"
    # idle screens return home; a new answer from AIRA brings you home too
    r.screen = "music"; r.last_touch = t
    r.render(now=t + 31)
    assert r.screen == "home"
    r.screen = "chat"; r.last_touch = 0
    r.set_caption("Hello!", "aira", now=200)
    assert r.screen == "home" and r.history[-1] == ("aira", "Hello!")


def test_emotions_screen_tries_every_emotion_and_about_shows_the_pi():
    from aira_pi import RELEASE_DATE, __version__
    from aira_pi.sysinfo import about_rows, release_text
    from aira_pi.ui_brutal import EMOTION_S, EMOTIONS, BrutalRenderer

    r = BrutalRenderer()
    r.state.linked = True
    r.screen, r.last_touch = "emotions", 100
    r.render(now=100)
    for key, _, _ in EMOTIONS:                            # every emotion has a button that selects it
        rect = next(rect for rect, a in r._hits if a == f"emo:{key}")
        assert r.tap((rect[0] + rect[2]) // 2, (rect[1] + rect[3]) // 2, now=100) is None
        assert r.emotion == key and not r.emotion_auto
        r.render(now=100)
    assert r.animating(now=100)                           # keeps moving while you look at her
    auto = next(rect for rect, a in r._hits if a == "emo:auto")
    r.tap(auto[0] + 5, auto[1] + 5, now=100)
    assert r.emotion_auto
    assert r.show_screen("emotions", now=200) and r.emotion_auto and r.emotion_since == 200   # from the web page
    r.render(now=200 + EMOTION_S * 1.5)                   # auto play moved on to the second emotion

    rows = dict(about_rows("AIRA Half", "XPT2046 over SPI"))
    assert __version__ in rows["Version"] and release_text() in rows["Version"] and "AIRA Half" in rows["Version"]
    assert RELEASE_DATE == "2026-11-01" and release_text() == "1 Nov 2026"
    assert rows["Developer"].startswith("Rajendhira Prasath Saravanan")
    for key in ("Device", "CPU", "Storage", "OS", "Kernel", "Screen", "Uptime"):
        assert rows[key]
    r.screen, r.last_touch = "about", 300
    assert r.render(now=300).size == (480, 320)


def test_mascot_is_20_percent_bigger_and_never_squashed():
    from aira_pi.ui_brutal import MASCOT, STANDBY, BrutalRenderer

    assert MASCOT == (round(226 * 1.2), round(180 * 1.2))
    r = BrutalRenderer()
    r.prewarm()
    frame = r.sprites["sleepy"].frames[0]
    assert frame.size == (300, 240) and r._fit(frame, MASCOT).size == MASCOT   # exactly 0.9x: same shape
    tall = r._fit(frame, STANDBY)
    assert tall.size == STANDBY and r._fit(frame, STANDBY) is tall              # cropped, cached
    full = frame.resize((270, 216), 1)                                           # crop == middle of the scaled frame
    assert tall.getpixel((98, 108)) == full.getpixel((98 + 36, 108))
    for linked in (True, False):
        r.state.linked = linked
        r.set_caption("Opening Spotify! " * 6, "aira", now=5)
        assert r.render(now=5).size == (480, 320)


def test_web_page_can_choose_the_pi_screen():
    import asyncio

    from aira_pi.app import PiApp
    from aira_pi.audio import PiAudio
    from aira_pi.config import PiConfig
    from aira_pi.display import NullDisplay
    from aira_pi.ui_brutal import BrutalRenderer

    r = BrutalRenderer()
    r.state.linked = True
    assert r.show_screen("music", now=100) and r.screen == "music"
    assert not r.show_screen("nope", now=100) and r.screen == "music"
    r.set_caption("Volume up.", "aira", now=110)          # an answer does not pull it away...
    r.render(now=150)
    assert r.screen == "music"
    r.render(now=161)                                     # ...but after a minute it returns home
    assert r.screen == "home"

    app = PiApp(PiConfig(), NullDisplay(), audio=PiAudio("off"), face=r)
    asyncio.run(app.handle({"type": "SHOW_SCREEN", "payload": {"screen": "system"}}))
    assert r.screen == "system"
    assert app.info_lines()[0].startswith("Edition: AIRA Half")       # this build: just the screen
    app.cfg.edition = "full"
    assert app.info_lines()[0].startswith("Edition: AIRA Full")


def test_taps_become_commands_for_the_laptop():
    import asyncio

    from aira_pi.app import PiApp
    from aira_pi.audio import PiAudio
    from aira_pi.config import PiConfig
    from aira_pi.display import NullDisplay

    sent = []

    class FakeWS:
        async def send(self, frame):
            sent.append(json.loads(frame))

    async def go():
        app = PiApp(PiConfig(token="t"), NullDisplay(), audio=PiAudio("off"))
        app.face.state.linked = True
        import time as _t
        app.face.screen = "music"
        app.face.last_touch = _t.monotonic()           # just touched: the screen stays on Music
        app.face.render()
        await app.on_tap(150, 100)                       # not connected yet: just a toast
        assert sent == [] and "not connected" in app.face.state.notice.lower()
        app.ws = FakeWS()
        await app.on_tap(150, 100)
        assert sent[-1]["type"] == "USER_TEXT" and sent[-1]["payload"] == {"text": "previous song"}

    asyncio.run(go())


@pytest.mark.skipif(sys.platform == "win32" or not __import__("shutil").which("bash"), reason="needs bash")
def test_setup_audio_edits_config_safely(tmp_path):
    import os
    import subprocess

    cfg, ovl = tmp_path / "config.txt", tmp_path / "overlays"
    ovl.mkdir()
    for o in ("hifiberry-dac", "googlevoicehat-soundcard"):
        (ovl / f"{o}.dtbo").write_bytes(b"")
    original = "arm_64bit=1\ndtparam=audio=on\ndtoverlay=tft35a:rotate=90\n#dtoverlay=vc4-kms-v3d\n[all]\n"
    cfg.write_text(original)
    env = {**os.environ, "AIRA_CONFIG_TXT": str(cfg), "AIRA_OVERLAYS_DIR": str(ovl)}
    script = str(ROOT / "raspberry_pi" / "setup_audio.sh")

    def run(*args):
        return subprocess.run(["bash", script, *args], env=env, capture_output=True, text=True)

    assert run("--dac").returncode == 0
    lines = cfg.read_text().splitlines()
    i = lines.index("dtoverlay=tft35a:rotate=90")
    assert lines[i + 2:i + 4] == ["dtparam=i2s=on", "dtoverlay=hifiberry-dac"]   # right below the display line
    assert "#dtoverlay=vc4-kms-v3d" in lines                                       # display/KMS lines untouched
    run("--duplex")
    text = cfg.read_text()
    assert "googlevoicehat-soundcard" in text and "hifiberry-dac" not in text     # one audio overlay at a time
    run("--undo")
    run("--undo")
    assert cfg.read_text() == original
    cfg.write_text("dtoverlay=tft35a:rotate=90\ndtoverlay=hifiberry-amp\n")        # someone else's overlay
    assert run("--dac").returncode == 1
    cfg.write_text("arm_64bit=1\n")                                                # unexpected config
    assert run("--dac").returncode == 1 and cfg.read_text() == "arm_64bit=1\n"


def test_event_stream_survives_quiet_periods():
    """A keep-alive must not end the subscription (it did: 'async generator raised StopAsyncIteration')."""
    import asyncio

    from laptop_server.api.device_routes import sse_stream
    from laptop_server.events import EventHub

    async def go():
        hub = EventHub()

        async def connected():
            return False

        gen = sse_stream(hub, connected, keepalive_s=0.05)
        assert (await gen.__anext__()).startswith("retry:")
        assert '"idle"' in await gen.__anext__()                      # current state first
        for _ in range(3):                                              # three quiet periods in a row
            assert (await gen.__anext__()) == ": keep-alive\n\n"
        hub.publish({"type": "caption", "role": "aira", "text": "still here"})
        assert "still here" in await gen.__anext__()
        assert hub.subscriber_count == 1
        await gen.aclose()
        assert hub.subscriber_count == 0                               # nothing left behind

    asyncio.run(go())


def test_windows_disconnect_noise_is_hidden_but_real_errors_are_not():
    import asyncio

    from laptop_server.app import quiet_client_disconnects

    loop = asyncio.new_event_loop()
    seen = []
    loop.set_exception_handler(lambda lp, ctx: seen.append(ctx))
    quiet_client_disconnects(loop)
    handle = "<Handle _ProactorBasePipeTransport._call_connection_lost()>"
    loop.call_exception_handler({"message": "Exception in callback", "handle": handle,
                                 "exception": ConnectionResetError(10054, "forcibly closed")})
    assert seen == []
    loop.call_exception_handler({"message": "Exception in callback", "exception": ValueError("real bug")})
    assert len(seen) == 1 and isinstance(seen[0]["exception"], ValueError)
    loop.close()


class _FakeChip:
    """Simulated XPT2046 on SPI: answers X / Y / Z1 / Z2 commands for a scripted touch."""

    def __init__(self):
        self.touch = None                      # None = not pressed, else (raw_x, raw_y)
        self.spi = self

    def xfer(self, data):
        cmd = data[0]
        if self.touch is None:
            value = {0xD0: 0, 0x90: 4095, 0xB0: 0, 0xC0: 4095}[cmd]
        else:
            value = {0xD0: self.touch[0], 0x90: self.touch[1], 0xB0: 900, 0xC0: 3000}[cmd]
        v = value << 3
        return bytes((0, (v >> 8) & 0xFF, v & 0xFF))

    def close(self):
        pass


def test_spi_touch_reads_the_chip_without_the_pen_signal():
    from aira_pi.touch_spi import XPT2046, SpiPress, SpiTouchScreen, diagnose

    fake = _FakeChip()
    chip = XPT2046(fake)
    assert chip.sample()[2] == 0                                  # untouched: no pressure
    fake.touch = (1200, 2800)
    x, y, p, z1, z2 = chip.sample()
    assert (x, y) == (1200, 2800) and p == 900 + 4095 - 3000

    taps = []
    clock = [0.0]
    ts = SpiTouchScreen(chip, [0, 0.1, 0, 0.1, 0, 0], lambda tx, ty: taps.append((tx, ty)))
    ts.detector.clock = lambda: clock[0]
    for _ in range(5):                                            # a short press...
        ts.poll_once()
        clock[0] += 0.02
    fake.touch = None
    ts.poll_once()                                                # ...and release = one tap
    assert taps == [(280, 120)]
    fake.touch = (1200, 2800)
    clock[0] += 1
    ts.poll_once()
    clock[0] += 2                                                 # held 2 s: not a tap
    fake.touch = None
    ts.poll_once()
    assert len(taps) == 1

    press = SpiPress(threshold=400)
    assert [press.feed(1000, 2000, 1500), press.feed(1010, 2010, 1500), press.feed(0, 4095, 0),
            press.feed(0, 4095, 0)][-1] == (1010.0, 2010.0)

    assert diagnose([(0, 4095, 0, 0, 4095)] * 50 + [(1200, 2800, 1995, 900, 3000)] * 5).startswith("touch works")
    assert "ALL the time" in diagnose([(1200, 2800, 1995, 900, 3000)] * 50)
    assert "does not answer" in diagnose([(4095, 4095, 4095, 4095, 4095)] * 50)
    assert "never saw a press" in diagnose([(0, 4095, 0, 0, 4095)] * 50)


def test_touch_spi_script_is_valid_and_safe():
    import shutil
    import subprocess

    script = ROOT / "raspberry_pi" / "touch_spi.sh"
    text = script.read_text(encoding="utf-8")
    assert "config.txt" not in text.split("set -euo")[1].replace("/boot/firmware/config.txt or", "")  # never edits boot
    if shutil.which("bash") and sys.platform != "win32":
        assert subprocess.run(["bash", "-n", str(script)]).returncode == 0
        out = subprocess.run(["bash", str(script), "enable"], capture_output=True, text=True)
        if subprocess.run(["id", "-u"], capture_output=True, text=True).stdout.strip() != "0":
            assert out.returncode == 1 and "sudo" in out.stdout                # refuses without root


class _FakeResp:
    def __init__(self, lines):
        self.lines = lines

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def __iter__(self):
        return iter(self.lines)


def test_pi_online_brain_streams_and_remembers():
    from aira_pi.online import OnlineBrain, OnlineError

    sent = []

    def opener(req, timeout):
        sent.append((req.full_url, req.headers.get("Authorization"), json.loads(req.data)))
        return _FakeResp([b'data: {"choices":[{"delta":{"content":"Vanakkam! "}}]}\n', b"\n",
                          b'data: {"choices":[{"delta":{"content":"I am AIRA."}}]}\n', b"data: [DONE]\n"])

    b = OnlineBrain("https://ai.example/v1/", "sk-x", "m-1", opener=opener)
    assert b.ask("hi") == "Vanakkam! I am AIRA."
    url, auth, body = sent[0]
    assert url == "https://ai.example/v1/chat/completions" and auth == "Bearer sk-x" and body["stream"]
    assert body["messages"][0]["role"] == "system" and "AIRA" in body["messages"][0]["content"]
    b.ask("again")
    assert [m["content"] for m in sent[1][2]["messages"][1:]] == ["hi", "Vanakkam! I am AIRA.", "again"]

    import urllib.error

    def refused(req, timeout):
        raise urllib.error.HTTPError(req.full_url, 401, "no", {}, None)

    with pytest.raises(OnlineError, match="API key"):
        OnlineBrain("https://ai.example/v1", "bad", "m-1", opener=refused).ask("hi")
    with pytest.raises(OnlineError):
        OnlineBrain("https://ai.example/v1", "", "m-1")


def test_pi_chat_page_checks_the_code_and_answers():
    import asyncio

    from aira_pi.online import ChatServer, OnlineError

    async def ask(text):
        if text == "fail":
            raise OnlineError("no internet")
        return f"You said {text}", "online"

    async def go():
        server = ChatServer(ask, lambda: "123456", port=0)
        await server.start(host="127.0.0.1")
        port = server._server.sockets[0].getsockname()[1]

        async def http(method, path, body=None):
            r, w = await asyncio.open_connection("127.0.0.1", port)
            data = json.dumps(body).encode() if body is not None else b""
            w.write(f"{method} {path} HTTP/1.1\r\nHost: x\r\nContent-Length: {len(data)}\r\n\r\n".encode() + data)
            await w.drain()
            raw = await r.read()
            w.close()
            head, _, payload = raw.partition(b"\r\n\r\n")
            return int(head.split()[1]), payload

        status, page = await http("GET", "/")
        assert status == 200 and b"AIRA" in page and b"/ask" in page
        assert (await http("POST", "/ask", {"code": "000000", "text": "hi"}))[0] == 401
        status, payload = await http("POST", "/ask", {"code": "123456", "text": "hello"})
        assert status == 200 and json.loads(payload) == {"reply": "You said hello", "via": "online"}
        status, payload = await http("POST", "/ask", {"code": "123456", "text": "fail"})
        assert status == 503 and "no internet" in json.loads(payload)["error"]
        for _ in range(10):                                              # guessing codes locks it
            await http("POST", "/ask", {"code": "999999", "text": "x"})
        assert (await http("POST", "/ask", {"code": "123456", "text": "hi"}))[0] == 429
        assert (await http("GET", "/secret"))[0] == 404
        await server.close()

    asyncio.run(go())


def test_pi_page_goes_to_the_laptop_when_linked_else_online():
    import asyncio

    from aira_pi.app import PiApp
    from aira_pi.audio import PiAudio
    from aira_pi.config import PiConfig
    from aira_pi.display import NullDisplay
    from aira_pi.online import OnlineError

    cfg = PiConfig(brain="auto")
    app = PiApp(cfg, NullDisplay(), audio=PiAudio("off"))
    assert app.brain is None and not app.face.state.online_ai           # no key: no online AI

    class Brain:
        def ask(self, text):
            return "Online answer"

    async def go():
        with pytest.raises(OnlineError, match="online AI"):
            await app.ask_from_page("hi")
        app.brain = Brain()
        assert await app.ask_from_page("hi") == ("Online answer", "online")
        assert app.face.state.caption == "Online answer"
        sent = []

        async def send(t, p):
            sent.append((t, p))
            await asyncio.sleep(0.01)
            await app.handle({"type": "CAPTION", "payload": {"role": "user", "text": p["text"]}})
            await app.handle({"type": "CAPTION", "payload": {"role": "aira", "text": "From the laptop"}})

        app.send, app.ws = send, object()
        app.face.state.linked = True
        assert await app.ask_from_page("volume up") == ("From the laptop", "laptop")
        assert sent == [("USER_TEXT", {"text": "volume up"})]

    asyncio.run(go())


def test_laptop_and_pi_agree_on_screen_names():
    import re

    from aira_pi.ui_brutal import SCREENS
    from laptop_server.api.device_routes import PI_SCREENS

    assert tuple(PI_SCREENS) == tuple(SCREENS)
    page = (ROOT / "laptop_server" / "web" / "index.html").read_text(encoding="utf-8")
    assert set(re.findall(r'data-screen="(\w+)"', page)) == set(SCREENS)    # a web button for each


def test_joystick_moves_a_visible_focus_through_menu_and_buttons():
    from aira_pi.ui_brutal import SCREENS, BrutalRenderer

    r = BrutalRenderer()
    r.state.linked = True
    t = 100.0
    r.render(now=t)
    r.navigate("down", now=t)                       # first move only shows the focus on the menu
    assert r.focus_zone == "rail" and r.screen == "home"
    r.navigate("down", now=t)
    assert r.screen == "chat"
    for _ in range(len(SCREENS) - 1):               # wraps around
        r.navigate("down", now=t)
    assert r.screen == "home"
    r.navigate("up", now=t)
    assert r.screen == "system"
    r.navigate("up", now=t); r.navigate("up", now=t); r.navigate("up", now=t); r.navigate("up", now=t)
    assert r.screen == "music"
    r.render(now=t)
    r.navigate("right", now=t)                      # into the buttons: the top-left one is focused
    r.render(now=t)
    assert r.focus_zone == "content" and r.focus_action == "say:previous song"
    r.navigate("right", now=t)
    assert r.focus_action == "say:pause the music"
    r.navigate("down", now=t)
    assert r.focus_action == "say:mute"
    assert r.select(now=t) == "say:mute"            # press = the same as tapping that button
    r.navigate("left", now=t)
    assert r.focus_action == "say:volume down"
    r.navigate("left", now=t)                       # nothing further left: back to the menu
    assert r.focus_zone == "rail"
    # risky buttons still need a second press
    r.screen = "system"; r.render(now=t)
    r.focus_zone, r.focus_action = "content", "sys:poweroff"
    assert r.select(now=t) is None and r.select(now=t + 1) == "sys:poweroff"
    r.back(now=t + 2)                               # long press: home
    assert r.screen == "home" and r.focus_zone == "rail"
    img = r.render(now=t + 2)
    assert img.getpixel((5, 10)) == (255, 0, 255)   # the magenta focus ring is drawn on the menu
    r.render(now=t + 40)                            # unused for 30 s: the ring hides
    assert r.render(now=t + 40).getpixel((5, 10)) != (255, 0, 255)


def test_controller_protocol_and_token():
    import asyncio

    from aira_pi.controller import ControllerServer, beacon_payload, broadcast_targets, new_token, parse_event

    assert parse_event('{"event":"navigate","direction":"up"}') == ("navigate", "up")
    assert parse_event('{"event":"select","direction":"up"}') == ("select", None)
    for bad in ('{"event":"navigate","direction":"sideways"}', '{"event":"reboot"}', "nope", "[]",
                '{"event":"select","x":"' + "a" * 300 + '"}'):
        assert parse_event(bad) is None
    assert json.loads(beacon_payload(8771, "AIRA Pi")) == {"service": "aira-pi", "v": 1, "port": 8771, "name": "AIRA Pi"}
    assert broadcast_targets(["10.140.240.10", "127.0.0.1"]) == ["10.140.240.255", "255.255.255.255"]
    tok = new_token()
    assert len(tok) == 10 and not set(tok) & set("01OI")

    websockets = pytest.importorskip("websockets")

    async def go():
        events, changes = [], []

        async def on_event(e, d):
            events.append((e, d))

        server = ControllerServer(on_event, lambda: "SECRET1234", port=0,
                                  on_change=lambda dev, up: changes.append((dev, up)))
        await server.start(host="127.0.0.1")
        port = list(server._server.sockets)[0].getsockname()[1]
        url = f"ws://127.0.0.1:{port}/"
        async with websockets.connect(url) as ws:                       # wrong token: refused
            await ws.send(json.dumps({"event": "hello", "token": "WRONG"}))
            assert json.loads(await ws.recv()) == {"ok": False, "error": "wrong token"}
        async with websockets.connect(url) as ws:
            await ws.send(json.dumps({"event": "hello", "device": "aira-joystick", "token": "SECRET1234"}))
            assert json.loads(await ws.recv())["ok"] is True
            for frame in ('{"event":"navigate","direction":"down"}', "garbage", '{"event":"ping"}',
                          '{"event":"select"}', '{"event":"back"}'):
                await ws.send(frame)
            for _ in range(50):
                if len(events) == 3:
                    break
                await asyncio.sleep(0.02)
        for _ in range(50):
            if changes and changes[-1] == ("aira-joystick", False):
                break
            await asyncio.sleep(0.02)
        await server.close()
        assert events == [("navigate", "down"), ("select", None), ("back", None)]
        assert changes == [("aira-joystick", True), ("aira-joystick", False)]
        assert server.failures == 0                                     # reset by the good hello

    asyncio.run(go())


def test_joystick_press_on_tools_asks_the_laptop():
    import asyncio

    from aira_pi.app import PiApp
    from aira_pi.audio import PiAudio
    from aira_pi.config import PiConfig
    from aira_pi.display import NullDisplay

    app = PiApp(PiConfig(), NullDisplay(), audio=PiAudio("off"))
    sent = []

    async def send(t, p):
        sent.append((t, p))

    app.send, app.ws = send, object()
    app.face.state.linked = True
    import time

    app.face.screen, app.face.last_touch = "tools", time.monotonic()
    app.face.render()

    async def go():
        await app.on_controller("navigate", "right")       # shows the focus on the menu
        await app.on_controller("navigate", "right")       # into Tools' buttons
        app.face.render()
        await app.on_controller("select", None)

    asyncio.run(go())
    assert sent == [("USER_TEXT", {"text": "How is my laptop doing?"})]
