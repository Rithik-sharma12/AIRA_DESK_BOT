"""The web voice interface in a real Chromium, with Chrome's fake microphone.

Covers pairing, microphone capture + upload, streamed reply, sentence-by-sentence server
speech and playback. Needs `pip install playwright && playwright install chromium`;
skipped otherwise. Speech engines are fakes: this checks the plumbing, not voice quality.
"""

from __future__ import annotations

import socket
import threading
import time

import pytest

playwright = pytest.importorskip("playwright.sync_api")

import uvicorn  # noqa: E402

from laptop_server.app import create_app  # noqa: E402
from laptop_server.voice.service import VoiceService  # noqa: E402
from tests.conftest import FakeProvider, make_settings  # noqa: E402
from tests.unit.test_web_ui import FakeSTT, FakeTTS  # noqa: E402

REPLY = "Vanakkam! I am AIRA. Nice to meet you."


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="module")
def server():
    s = make_settings(llm_warmup_on_start=False)
    stt, tts = FakeSTT(), FakeTTS()
    app = create_app(s, provider=FakeProvider(reply=REPLY), voice=VoiceService(s, stt=stt, tts=tts))

    @app.post("/_test/publish")                     # test only: publish a hub event on the server's loop
    async def _publish(ev: dict) -> dict:
        app.state.hub.publish(ev)
        return {}
    port = free_port()
    srv = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_config=None, log_level="warning"))
    t = threading.Thread(target=srv.run, daemon=True)
    t.start()
    for _ in range(100):
        if srv.started:
            break
        time.sleep(0.05)
    yield {"url": f"http://localhost:{port}/", "app": app, "stt": stt, "tts": tts}
    srv.should_exit = True
    t.join(timeout=5)


@pytest.fixture(scope="module")
def page(server):
    with playwright.sync_playwright() as p:
        try:
            browser = p.chromium.launch(args=["--use-fake-ui-for-media-stream", "--use-fake-device-for-media-stream",
                                              "--autoplay-policy=no-user-gesture-required"])
        except Exception as exc:  # browser binaries not installed
            pytest.skip(f"Chromium not available: {exc}")
        ctx = browser.new_context(viewport={"width": 412, "height": 860}, permissions=["microphone"])
        pg = ctx.new_page()
        pg.goto(server["url"])
        yield pg
        browser.close()


def test_pair_talk_and_hear_reply(server, page):
    page.wait_for_selector("#pairSheet.open", timeout=5000)
    page.fill("#code", server["app"].state.web_pairing.code)
    page.fill("#devname", "test-phone")
    page.click("#pairBtn")
    page.wait_for_function("document.getElementById('status').textContent.startsWith('ready')", timeout=5000)
    assert "Whisper on the laptop" in page.text_content("#hint")

    page.click("#mic")                      # start recording from the fake microphone
    page.wait_for_selector("#mic.rec", timeout=3000)
    time.sleep(1.5)
    page.click("#mic")                      # stop -> upload -> transcribe -> chat -> speak
    page.wait_for_selector(".msg.user", timeout=5000)
    assert "vanakkam AIRA" in page.text_content(".msg.user")
    page.wait_for_function("document.querySelector('.msg.aira') && document.querySelector('.msg.aira').textContent.includes('Nice to meet you')", timeout=8000)

    suffix, size, lang = server["stt"].calls[-1]
    assert size > 1000 and suffix in (".webm", ".ogg", ".m4a")   # real recorded audio was uploaded
    for _ in range(60):                                          # each sentence spoken separately, in order
        if len(server["tts"].texts) >= 3:
            break
        time.sleep(0.1)
    # requests are sent in parallel (playback order is kept in the page), so arrival order may vary
    assert sorted(server["tts"].texts) == sorted(["Vanakkam!", "I am Aira.", "Nice to meet you."])   # the voice gets "Aira" (spoken as a name, not spelled)
    page.wait_for_function("document.getElementById('face').className === 'idle'", timeout=8000)


def test_typing_works_and_conversation_keeps_context(server, page):
    server["tts"].texts.clear()
    page.fill("#text", "What did I say?")
    page.press("#text", "Enter")
    page.wait_for_function("document.querySelectorAll('.msg.aira').length >= 2", timeout=5000)
    assert len(server["app"].state.conversations) == 1   # same session reused


def test_tasks_confirmations_and_reminders_in_the_page(server, page):
    backend = server["app"].state.toolkit.backend
    # instant command: chip with the lightning mark, no model call
    page.fill("#text", "volume up")
    page.press("#text", "Enter")
    page.wait_for_function("[...document.querySelectorAll('.chip')].some(c => c.textContent.includes('volume'))",
                           timeout=5000)
    assert ("press", ("volume_up", 5)) in backend.actions

    # risky action: the page shows Yes / Cancel; clicking Yes runs it
    page.fill("#text", "shut down the laptop")
    page.press("#text", "Enter")
    page.wait_for_selector(".confirm .yes", timeout=5000)
    assert ("shutdown", None) not in backend.actions
    page.click(".confirm .yes")
    page.wait_for_function("[...document.querySelectorAll('.msg.aira')].some(m => m.textContent.includes('30 seconds'))",
                           timeout=5000)
    assert ("shutdown", None) in backend.actions

    # a timer fires over the live events stream and is shown (and spoken) in the page
    page.fill("#text", "set a timer for 1 second")
    page.press("#text", "Enter")
    page.wait_for_selector(".msg.reminder", timeout=6000)
    assert "Time's up" in page.text_content(".msg.reminder")

    # the page reports its state so a Raspberry Pi face can follow
    page.wait_for_function("true", timeout=100)
    deadline = time.time() + 5
    while time.time() < deadline and server["app"].state.hub.state.get("source") != "web":
        time.sleep(0.1)
    assert server["app"].state.hub.state.get("source") == "web"


def test_pi_screen_status_and_conversations_started_on_the_pi(server, page):
    import httpx

    app = server["app"]
    app.state.devices["test-pi"] = {"name": "Desk Pi", "last_seen": time.time(), "caps": {"edition": "AIRA Half"}}
    try:
        # the header shows the Pi is connected and the screen buttons come alive
        page.wait_for_function("document.getElementById('piTag').classList.contains('on')", timeout=12000)
        assert page.text_content("#piTag").lower() == "half on"     # phone width; "AIRA Half · on" when wide
        assert not page.is_disabled("#piScreens button[data-screen=music]")
        page.eval_on_selector("#piScreens button[data-screen=music]", "b => b.click()")
        page.wait_for_function("document.getElementById('hint').textContent.includes('Music')", timeout=5000)

        # a command tapped on the Pi is shown here and its answer is spoken by this page
        before = len(server["tts"].texts)
        for ev in ({"type": "caption", "role": "user", "text": "mute", "source": "Desk Pi"},
                   {"type": "caption", "role": "aira", "text": "Muted from the Pi.", "source": "Desk Pi"}):
            httpx.post(server["url"] + "_test/publish", json=ev, timeout=5)
        page.wait_for_function("[...document.querySelectorAll('.msg.aira')].some(m => m.textContent === 'Muted from the Pi.')",
                               timeout=5000)
        assert page.evaluate("[...document.querySelectorAll('.msg.user')].pop().textContent").endswith("on Desk Pi")
        deadline = time.time() + 5
        while time.time() < deadline and "Muted from the Pi." not in server["tts"].texts[before:]:
            time.sleep(0.1)
        assert "Muted from the Pi." in server["tts"].texts[before:]

        # the page's own conversations are not shown twice
        n = page.evaluate("document.querySelectorAll('.msg.aira').length")
        page.fill("#text", "hello again")
        page.press("#text", "Enter")
        page.wait_for_function(f"document.querySelectorAll('.msg.aira').length > {n}", timeout=5000)
        time.sleep(0.5)
        assert page.evaluate("document.querySelectorAll('.msg.aira').length") == n + 1
    finally:
        app.state.devices.pop("test-pi", None)
