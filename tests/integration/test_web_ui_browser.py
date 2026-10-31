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
    assert "Whisper on the laptop" in page.inner_text("#hint")

    page.click("#mic")                      # start recording from the fake microphone
    page.wait_for_selector("#mic.rec", timeout=3000)
    time.sleep(1.5)
    page.click("#mic")                      # stop -> upload -> transcribe -> chat -> speak
    page.wait_for_selector(".msg.user", timeout=5000)
    assert "vanakkam AIRA" in page.inner_text(".msg.user")
    page.wait_for_function(f"document.querySelector('.msg.aira') && document.querySelector('.msg.aira').textContent.includes('Nice to meet you')", timeout=8000)

    suffix, size, lang = server["stt"].calls[-1]
    assert size > 1000 and suffix in (".webm", ".ogg", ".m4a")   # real recorded audio was uploaded
    for _ in range(60):                                          # each sentence spoken separately, in order
        if len(server["tts"].texts) >= 3:
            break
        time.sleep(0.1)
    assert server["tts"].texts == ["Vanakkam!", "I am AIRA.", "Nice to meet you."]
    page.wait_for_function("document.getElementById('face').className === 'idle'", timeout=8000)


def test_typing_works_and_conversation_keeps_context(server, page):
    server["tts"].texts.clear()
    page.fill("#text", "What did I say?")
    page.press("#text", "Enter")
    page.wait_for_function("document.querySelectorAll('.msg.aira').length >= 2", timeout=5000)
    assert len(server["app"].state.conversations) == 1   # same session reused
