"""The Raspberry Pi client against a real HTTPS laptop server (in-process uvicorn).

Covers: pairing with certificate pinning, the device WebSocket, AIRA's conversation
mirrored on the Pi face, a Pi-originated request running a laptop tool, the Pi button
waking the web page, and refusing a laptop whose certificate is not the pinned one.
"""

from __future__ import annotations

import asyncio
import socket
import ssl
import subprocess
import sys
import threading
import time
from pathlib import Path

import httpx
import pytest
import uvicorn

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "raspberry_pi"))
pytest.importorskip("PIL", reason="Pi tests need Pillow (pip install -r requirements-dev.txt)")
pytest.importorskip("websockets")

from aira_pi.app import PiApp, pair  # noqa: E402
from aira_pi.audio import PiAudio  # noqa: E402
from aira_pi.config import PiConfig  # noqa: E402
from aira_pi.display import NullDisplay  # noqa: E402
from laptop_server.app import create_app  # noqa: E402
from tests.conftest import TEST_KEY, FakeProvider, make_settings  # noqa: E402


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def make_cert(out: Path) -> tuple[Path, Path]:
    subprocess.run([sys.executable, str(ROOT / "scripts" / "make_dev_cert.py"), "--out", str(out)],
                   check=True, capture_output=True, cwd=ROOT)
    return out / "aira-dev.crt", out / "aira-dev.key"


@pytest.fixture(scope="module")
def laptop(tmp_path_factory):
    certs = tmp_path_factory.mktemp("certs")
    crt, key = make_cert(certs)
    port = free_port()
    settings = make_settings(ssl_certfile=crt, ssl_keyfile=key, port=port)
    app = create_app(settings, provider=FakeProvider(reply="Sure thing."))
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, ssl_certfile=str(crt),
                                           ssl_keyfile=str(key), log_level="warning", ws="auto"))
    t = threading.Thread(target=server.run, daemon=True)
    t.start()
    deadline = time.time() + 15
    while not server.started and time.time() < deadline:
        time.sleep(0.05)
    assert server.started
    yield {"url": f"https://127.0.0.1:{port}", "app": app, "crt": crt, "certs": certs}
    server.should_exit = True
    t.join(5)


def api(laptop, path, body):
    return httpx.post(laptop["url"] + path, json=body, headers={"Authorization": f"Bearer {TEST_KEY}"},
                      verify=str(laptop["crt"]), timeout=10)


async def wait_for(cond, timeout=5.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if cond():
            return True
        await asyncio.sleep(0.05)
    return False


def test_pair_link_mirror_and_control(laptop, tmp_path, monkeypatch):
    monkeypatch.setenv("AIRA_PI_CONFIG", str(tmp_path / "pi.json"))
    app = laptop["app"]
    cfg = PiConfig()
    code = app.state.web_pairing.code
    assert asyncio.run(pair(cfg, laptop["url"], code, "Desk Pi")) == 0
    saved = PiConfig.load()
    assert saved.token and saved.cert_pem and len(saved.fingerprint) == 64
    assert (tmp_path / "pi.json").stat().st_mode & 0o777 == 0o600

    async def scenario():
        pi = PiApp(saved, NullDisplay(), audio=PiAudio("off"))
        task = asyncio.create_task(pi.session(laptop["url"]))
        assert await wait_for(lambda: pi.face.state.linked), "Pi did not link"
        assert await wait_for(lambda: any(d["name"] == "Desk Pi" for d in app.state.devices.values()))
        assert next(iter(app.state.devices.values()))["caps"]["edition"] == "AIRA Half"   # screen-only build

        # a conversation on the laptop (web page / API) shows up on the Pi
        r = await asyncio.to_thread(api, laptop, "/api/v1/chat", {"message": "volume up"})
        assert r.status_code == 200 and r.json()["reply"] == "Volume up."
        assert await wait_for(lambda: pi.face.state.caption == "Volume up.")
        # the state frame follows the caption frame; wait for it rather than racing it
        assert await wait_for(lambda: pi.face.state.expression == "speaking" and pi.face.state.hold_until > 0)

        # something said on the Pi runs a laptop tool; the web page hears it came from the Pi
        seen_caps = []
        caps_agen = app.state.hub.subscribe(send_current_state=False)

        async def collect_caps():
            async for ev in caps_agen:
                if ev["type"] == "caption":
                    seen_caps.append(ev)

        caps_task = asyncio.create_task(collect_caps())
        await asyncio.sleep(0.05)
        await pi.send("USER_TEXT", {"text": "mute"})
        backend = app.state.toolkit.backend
        assert await wait_for(lambda: ("press", ("mute", 1)) in backend.actions)
        assert await wait_for(lambda: any(e["role"] == "aira" and e["source"] == "Desk Pi" for e in seen_caps))
        caps_task.cancel()

        # the web page switches the Pi's screen (no touch needed)
        r = await asyncio.to_thread(api, laptop, "/api/v1/devices/screen", {"screen": "music"})
        assert r.status_code == 200 and r.json()["devices"] == 1
        assert await wait_for(lambda: getattr(pi.face, "screen", None) == "music")
        r = await asyncio.to_thread(api, laptop, "/api/v1/devices/screen", {"screen": "../x"})
        assert r.status_code == 422

        # the Pi button wakes the web page's microphone
        seen = []
        agen = app.state.hub.subscribe(send_current_state=False)

        async def collect():
            async for ev in agen:
                seen.append(ev)

        collector = asyncio.create_task(collect())
        await asyncio.sleep(0.05)
        await pi.on_button("up")
        assert await wait_for(lambda: any(e["type"] == "wake" for e in seen))
        collector.cancel()
        task.cancel()

    asyncio.run(scenario())


def test_pi_refuses_a_laptop_with_another_certificate(laptop, tmp_path):
    other_crt, _ = make_cert(tmp_path / "other")
    cfg = PiConfig(token="whatever", cert_pem=other_crt.read_text())
    pi = PiApp(cfg, NullDisplay(), audio=PiAudio("off"))
    with pytest.raises(ssl.SSLError):
        asyncio.run(pi.session(laptop["url"]))


def test_wrong_token_is_rejected(laptop):
    cfg = PiConfig(token="not-a-real-token", cert_pem=laptop["crt"].read_text())
    pi = PiApp(cfg, NullDisplay(), audio=PiAudio("off"))
    with pytest.raises(Exception) as info:
        asyncio.run(pi.session(laptop["url"]))
    assert "403" in str(info.value) or "4401" in str(info.value)
