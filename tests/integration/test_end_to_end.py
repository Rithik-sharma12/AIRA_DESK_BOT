"""End-to-end: real `python -m laptop_server` process + Ollama emulator, over real HTTP.

Proves the startup path (env config, health check, warm-up), auth, JSON chat and SSE
streaming work across process boundaries. Model quality and GPU performance are NOT
tested here - that is what scripts/benchmark_llm.py and test_live_ollama.py are for.
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx
import pytest

ROOT = Path(__file__).resolve().parents[2]
KEY = "e2e-" + "k" * 40


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def wait_http(url: str, timeout: float = 20) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            httpx.get(url, timeout=1)
            return
        except httpx.HTTPError:
            time.sleep(0.2)
    raise TimeoutError(url)


@pytest.fixture(scope="module")
def stack(tmp_path_factory):
    ollama_port, aira_port = free_port(), free_port()
    procs = []
    fake = subprocess.Popen(
        [sys.executable, "-m", "tests.fake_ollama", "--port", str(ollama_port)],
        cwd=ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    procs.append(fake)
    wait_http(f"http://127.0.0.1:{ollama_port}/api/version")

    logs = tmp_path_factory.mktemp("logs")
    env = {
        **os.environ,
        "AIRA_API_KEY": KEY,
        "AIRA_PORT": str(aira_port),
        "AIRA_OLLAMA_BASE_URL": f"http://127.0.0.1:{ollama_port}",
        "AIRA_LLM_MODEL": "gemma4:e4b-it-qat",
        "AIRA_LOG_DIR": str(logs),
        # Independent of the developer's .env (e.g. LAN + HTTPS for the Pi): plain HTTP on localhost.
        "AIRA_HOST": "127.0.0.1",
        "AIRA_ALLOW_LAN": "false",
        "AIRA_SSL_CERTFILE": "",
        "AIRA_SSL_KEYFILE": "",
        "AIRA_DEVICE_DISCOVERY": "false",
    }
    server = subprocess.Popen(
        [sys.executable, "-m", "laptop_server"], cwd=ROOT, env=env,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
    )
    procs.append(server)
    try:
        wait_http(f"http://127.0.0.1:{aira_port}/api/v1/health")
        yield {"base": f"http://127.0.0.1:{aira_port}", "server": server, "logs": logs}
    finally:
        for p in procs:
            p.terminate()
            p.wait(timeout=10)


def test_status_shows_warm_model(stack):
    r = httpx.get(f"{stack['base']}/api/v1/status", headers={"Authorization": f"Bearer {KEY}"})
    body = r.json()
    assert body["status"] == "ok"
    assert body["llm"]["model_available"] is True
    assert body["llm"]["loaded_models"][0]["name"] == "gemma4:e4b-it-qat"  # warm-up loaded it


def test_unauthenticated_rejected_and_audited(stack):
    r = httpx.post(f"{stack['base']}/api/v1/chat", json={"message": "hi"})
    assert r.status_code == 401
    time.sleep(0.2)
    audit = (stack["logs"] / "audit.jsonl").read_text(encoding="utf-8")
    assert "auth_failed" in audit


def test_tamil_streaming_round_trip(stack):
    tokens, done = [], None
    with httpx.stream("POST", f"{stack['base']}/api/v1/chat/stream",
                      json={"message": "vanakkam AIRA"}, headers={"Authorization": f"Bearer {KEY}"},
                      timeout=10) as r:
        event = None
        for line in r.iter_lines():
            if line.startswith("event: "):
                event = line[7:]
            elif line.startswith("data: "):
                data = json.loads(line[6:])
                if event == "token":
                    tokens.append(data["text"])
                elif event == "done":
                    done = data
    assert done is not None
    assert "".join(tokens) == done["reply"]
    assert "வணக்கம்" in done["reply"]
    assert done["stats"]["ttft_s"] > 0


def test_conversation_content_not_logged_by_default(stack):
    httpx.post(f"{stack['base']}/api/v1/chat", json={"message": "my secret plan"},
               headers={"Authorization": f"Bearer {KEY}"})
    time.sleep(0.2)
    log = (stack["logs"] / "aira.jsonl").read_text(encoding="utf-8")
    assert '"event": "chat"' in log
    assert "my secret plan" not in log
