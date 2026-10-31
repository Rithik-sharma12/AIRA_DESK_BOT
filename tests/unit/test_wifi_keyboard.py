"""Wi-Fi keyboard: keymap, injector, and the real server over a real WebSocket client.

uinput itself needs the Pi; here a RecordingInjector captures the key events instead.
"""

import asyncio
import json
import socket

import pytest

websockets = pytest.importorskip("websockets")

from raspberry_pi.wifi_keyboard.injector import RecordingInjector  # noqa: E402
from raspberry_pi.wifi_keyboard.keymap import KEY, text_to_strokes  # noqa: E402
from raspberry_pi.wifi_keyboard.server import (  # noqa: E402
    MAX_FRAME, MAX_PAIR_FAILURES, DeviceStore, KeyboardServer, accept_key, detect_layout, is_local)

SHIFT, CTRL = KEY["KEY_LEFTSHIFT"], KEY["KEY_LEFTCTRL"]


# ------------------------------------------------------------------ keymap
def test_gb_and_us_symbols_differ_where_they_should():
    gb, _ = text_to_strokes('@"#\\|~', "gb")
    us, _ = text_to_strokes('@"#\\|~', "us")
    assert [(s.key, s.shift) for s in gb] == [
        ("KEY_APOSTROPHE", True), ("KEY_2", True), ("KEY_BACKSLASH", False),
        ("KEY_102ND", False), ("KEY_102ND", True), ("KEY_BACKSLASH", True)]
    assert [(s.key, s.shift) for s in us] == [
        ("KEY_2", True), ("KEY_APOSTROPHE", True), ("KEY_3", True),
        ("KEY_BACKSLASH", False), ("KEY_BACKSLASH", True), ("KEY_GRAVE", True)]


def test_untypeable_characters_are_reported():
    strokes, skipped = text_to_strokes("hi வணக்கம் 😀", "gb")
    assert len(strokes) == 4 and "வ" in skipped and "😀" in skipped


def test_every_printable_ascii_char_is_typeable_on_both_layouts():
    import string
    for layout in ("gb", "us"):
        _, skipped = text_to_strokes(string.printable.replace("\x0b", "").replace("\x0c", ""), layout)
        assert skipped == "", layout


# ---------------------------------------------------------------- injector
def test_tap_with_modifiers_orders_events_and_releases():
    inj = RecordingInjector()
    inj.tap("KEY_C", ("KEY_LEFTCTRL",))
    c = KEY["KEY_C"]
    assert inj.events == [(CTRL, 1), (c, 1), (c, 0), (CTRL, 0)]
    assert inj.pressed == set()


def test_type_strokes_adds_shift_and_rejects_bad_keys():
    inj = RecordingInjector()
    strokes, _ = text_to_strokes("A", "gb")
    inj.type_strokes(strokes)
    assert inj.events[0] == (SHIFT, 1) and inj.events[-1] == (SHIFT, 0)
    with pytest.raises(ValueError):
        inj.tap("KEY_SYSRQ")
    with pytest.raises(ValueError):
        inj.tap("KEY_A", ("KEY_A",))


def test_release_all():
    inj = RecordingInjector()
    inj.down("KEY_LEFTALT")
    inj.down("KEY_TAB")
    inj.release_all()
    assert inj.pressed == set() and inj.events[-2:] == [(KEY["KEY_LEFTALT"], 0), (KEY["KEY_TAB"], 0)]


# ---------------------------------------------------------------- helpers
def test_is_local_and_accept_key_and_layout_detection(tmp_path, monkeypatch):
    for ok in ("192.168.43.10", "10.140.240.10", "127.0.0.1", "::1", "fe80::1", "::ffff:192.168.1.5"):
        assert is_local(ok), ok
    for bad in ("8.8.8.8", "2001:4860::8888", "nonsense"):
        assert not is_local(bad), bad
    # RFC 6455 section 1.3 example
    assert accept_key("dGhlIHNhbXBsZSBub25jZQ==") == "s3pPLMBiTxaQ9kYGzzhZRbK+xOo="
    assert detect_layout() in ("gb", "us")


# ------------------------------------------------------------------ server
def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class Harness:
    def __init__(self, tmp_path):
        self.inj = RecordingInjector()
        self.store = DeviceStore.load(tmp_path / "devices.json")
        self.port = free_port()
        self.lines = []
        self.server = KeyboardServer(self.inj, self.store, "gb", self.port, announce=self.lines.append)
        self.url = f"ws://127.0.0.1:{self.port}/ws"

    async def start(self):
        self.srv = await asyncio.start_server(self.server.handle, "127.0.0.1", self.port)

    async def stop(self):
        self.srv.close()
        await self.srv.wait_closed()


async def recv(ws):
    return json.loads(await asyncio.wait_for(ws.recv(), 5))


async def settle(ws):
    """Commands run in order per connection; a pong means everything before it was injected."""
    await ws.send(json.dumps({"t": "ping"}))
    while (await recv(ws))["t"] != "pong":
        pass


def run(coro):
    return asyncio.run(coro)


def test_page_served_and_unknown_paths(tmp_path):
    h = Harness(tmp_path)

    async def go():
        await h.start()
        try:
            r, w = await asyncio.open_connection("127.0.0.1", h.port)
            w.write(b"GET / HTTP/1.1\r\nHost: x\r\n\r\n")
            body = await r.read()
            w.close()
            r2, w2 = await asyncio.open_connection("127.0.0.1", h.port)
            w2.write(b"GET /etc/passwd HTTP/1.1\r\nHost: x\r\n\r\n")
            body2 = await r2.read()
            w2.close()
            return body, body2
        finally:
            await h.stop()

    page, missing = run(go())
    assert b"200 OK" in page and b"AIRA Keyboard" in page
    assert b"404" in missing


def test_pair_then_type_then_reconnect_with_token(tmp_path):
    h = Harness(tmp_path)

    async def go():
        await h.start()
        try:
            async with websockets.connect(h.url) as ws:
                await ws.send(json.dumps({"t": "hello"}))
                assert (await recv(ws))["t"] == "need_code"
                assert any(h.server.pairing.code[:3] in line for line in h.lines)  # code shown on console
                await ws.send(json.dumps({"t": "pair", "code": "000000" if h.server.pairing.code != "000000" else "111111"}))
                bad = await recv(ws)
                assert bad["t"] == "err" and bad["left"] == MAX_PAIR_FAILURES - 1
                await ws.send(json.dumps({"t": "pair", "code": h.server.pairing.code, "name": "Redmi"}))
                paired = await recv(ws)
                assert paired["t"] == "paired" and len(paired["token"]) > 30
                await ws.send(json.dumps({"t": "text", "s": "Hi!"}))
                await settle(ws)
            assert h.store.devices[0]["name"] == "Redmi"
            assert paired["token"] not in (tmp_path / "devices.json").read_text()  # only the hash is stored
            typed = [c for c, v in h.inj.events if v == 1]
            assert typed == [SHIFT, KEY["KEY_H"], KEY["KEY_I"], SHIFT, KEY["KEY_1"]]

            h.inj.events.clear()
            async with websockets.connect(h.url) as ws:
                await ws.send(json.dumps({"t": "auth", "token": paired["token"]}))
                ready = await recv(ws)
                assert ready["t"] == "ready" and ready["layout"] == "gb"
                await ws.send(json.dumps({"t": "key", "key": "KEY_C", "mods": ["KEY_LEFTCTRL"]}))
                await ws.send(json.dumps({"t": "key", "key": "KEY_BACKSPACE", "repeat": 3}))
                await ws.send(json.dumps({"t": "text", "s": "வ"}))
                assert (await recv(ws)) == {"t": "skipped", "chars": "வ"}
                await settle(ws)
            downs = [c for c, v in h.inj.events if v == 1]
            assert downs == [CTRL, KEY["KEY_C"]] + [KEY["KEY_BACKSPACE"]] * 3
        finally:
            await h.stop()

    run(go())


def test_disconnect_releases_held_keys(tmp_path):
    h = Harness(tmp_path)
    token = h.store.add("phone")

    async def go():
        await h.start()
        try:
            async with websockets.connect(h.url) as ws:
                await ws.send(json.dumps({"t": "auth", "token": token}))
                await recv(ws)
                await ws.send(json.dumps({"t": "down", "key": "KEY_LEFTSHIFT"}))
                await settle(ws)
                assert "KEY_LEFTSHIFT" in h.inj.pressed
            for _ in range(50):
                if not h.inj.pressed:
                    break
                await asyncio.sleep(0.05)
        finally:
            await h.stop()

    run(go())
    assert h.inj.pressed == set() and h.inj.events[-1] == (SHIFT, 0)


def test_rejects_unpaired_commands_bad_keys_and_wrong_tokens(tmp_path):
    h = Harness(tmp_path)
    token = h.store.add("phone")

    async def go():
        await h.start()
        try:
            async with websockets.connect(h.url) as ws:
                await ws.send(json.dumps({"t": "text", "s": "rm -rf /"}))  # not authenticated
                with pytest.raises(websockets.ConnectionClosed):
                    await recv(ws)
            async with websockets.connect(h.url) as ws:
                await ws.send(json.dumps({"t": "auth", "token": "guess"}))
                assert (await recv(ws))["t"] == "unpaired"
            async with websockets.connect(h.url) as ws:
                await ws.send(json.dumps({"t": "auth", "token": token}))
                await recv(ws)
                await ws.send(json.dumps({"t": "key", "key": "KEY_SYSRQ"}))
                assert "not allowed" in (await recv(ws))["msg"]
                await ws.send(json.dumps({"t": "key", "key": "KEY_A", "mods": ["KEY_SYSRQ"]}))  # bogus mod dropped
                await settle(ws)
        finally:
            await h.stop()

    run(go())
    assert all(code != 99 for code, _ in h.inj.events)  # 99 = SysRq never injected
    assert [c for c, v in h.inj.events if v == 1] == [KEY["KEY_A"]]


def test_pairing_locks_after_too_many_wrong_codes(tmp_path):
    h = Harness(tmp_path)

    async def go():
        await h.start()
        try:
            async with websockets.connect(h.url) as ws:
                await ws.send(json.dumps({"t": "hello"}))
                await recv(ws)
                wrong = "000000" if h.server.pairing.code != "000000" else "111111"
                for _ in range(MAX_PAIR_FAILURES):
                    await ws.send(json.dumps({"t": "pair", "code": wrong}))
                    last = await recv(ws)
                assert last == {"t": "err", "msg": "pairing locked", "left": 0}
                await ws.send(json.dumps({"t": "pair", "code": h.server.pairing.code}))
                assert (await recv(ws))["left"] == 0  # even the right code is refused now
        finally:
            await h.stop()

    run(go())
    assert any("LOCKED" in line for line in h.lines)


def test_oversized_frame_closes_connection(tmp_path):
    h = Harness(tmp_path)
    token = h.store.add("phone")

    async def go():
        await h.start()
        try:
            async with websockets.connect(h.url, max_size=None) as ws:
                await ws.send(json.dumps({"t": "auth", "token": token}))
                await recv(ws)
                await ws.send("x" * (MAX_FRAME + 10))
                with pytest.raises(websockets.ConnectionClosed):
                    await recv(ws)
        finally:
            await h.stop()

    run(go())
