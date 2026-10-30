import json

from fastapi.testclient import TestClient

from laptop_server.app import create_app
from laptop_server.llm.base import LLMModelNotFoundError, LLMUnavailableError
from tests.conftest import TEST_KEY, FakeProvider, make_settings


def parse_sse(text: str) -> list[tuple[str, dict]]:
    events = []
    for block in text.strip().split("\n\n"):
        lines = dict(line.split(": ", 1) for line in block.splitlines())
        events.append((lines["event"], json.loads(lines["data"])))
    return events


def test_health_is_public(client):
    r = client.get("/api/v1/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


def test_private_endpoints_require_key(client):
    assert client.get("/api/v1/status").status_code == 401
    assert client.post("/api/v1/chat", json={"message": "hi"}).status_code == 401
    r = client.post("/api/v1/chat", json={"message": "hi"}, headers={"Authorization": "Bearer wrong"})
    assert r.status_code == 401
    assert r.headers["www-authenticate"] == "Bearer"


def test_x_api_key_header_accepted(client):
    r = client.post("/api/v1/chat", json={"message": "hi"}, headers={"X-API-Key": TEST_KEY})
    assert r.status_code == 200


def test_warmup_runs_on_startup(fake_provider, client):
    assert fake_provider.warmed is True


def test_chat_round_trip_and_history(client, auth, fake_provider):
    r = client.post("/api/v1/chat", json={"message": "Hey AIRA, good morning!"}, headers=auth)
    assert r.status_code == 200
    body = r.json()
    assert body["reply"] == "Hello from AIRA!"
    sid = body["session_id"]
    assert body["stats"]["completion_tokens"] == 3

    r2 = client.post("/api/v1/chat", json={"message": "What did I just say?", "session_id": sid}, headers=auth)
    assert r2.json()["session_id"] == sid
    second_prompt = fake_provider.calls[1]
    assert second_prompt[0]["role"] == "system"
    assert [m["content"] for m in second_prompt[1:]] == [
        "Hey AIRA, good morning!",
        "Hello from AIRA!",
        "What did I just say?",
    ]


def test_streaming_events(client, auth):
    with client.stream("POST", "/api/v1/chat/stream", json={"message": "Enna AIRA?"}, headers=auth) as r:
        assert r.status_code == 200
        assert r.headers["content-type"].startswith("text/event-stream")
        events = parse_sse(r.read().decode())
    kinds = [e for e, _ in events]
    assert kinds[0] == "start" and kinds[-1] == "done"
    assert "".join(d["text"] for e, d in events if e == "token") == "Hello from AIRA!"
    assert events[-1][1]["reply"] == "Hello from AIRA!"


def test_stream_records_history(client, auth, fake_provider):
    with client.stream("POST", "/api/v1/chat/stream", json={"message": "one", "session_id": "s1"}, headers=auth) as r:
        r.read()
    client.post("/api/v1/chat", json={"message": "two", "session_id": "s1"}, headers=auth)
    assert [m["content"] for m in fake_provider.calls[-1][1:]] == ["one", "Hello from AIRA!", "two"]


def test_reset_session(client, auth, fake_provider):
    client.post("/api/v1/chat", json={"message": "remember me", "session_id": "abc"}, headers=auth)
    r = client.delete("/api/v1/sessions/abc", headers=auth)
    assert r.json() == {"session_id": "abc", "deleted": True}
    client.post("/api/v1/chat", json={"message": "fresh", "session_id": "abc"}, headers=auth)
    assert len(fake_provider.calls[-1]) == 2  # system + user only


def test_validation(client, auth):
    assert client.post("/api/v1/chat", json={"message": ""}, headers=auth).status_code == 422
    assert client.post("/api/v1/chat", json={"message": "   "}, headers=auth).status_code == 422
    assert client.post("/api/v1/chat", json={"message": "x" * 4001}, headers=auth).status_code == 413
    bad_sid = client.post("/api/v1/chat", json={"message": "hi", "session_id": "../etc"}, headers=auth)
    assert bad_sid.status_code == 422
    extra = client.post("/api/v1/chat", json={"message": "hi", "admin": True}, headers=auth)
    assert extra.status_code == 422


def test_model_not_found_maps_to_503_with_hint(auth):
    err = LLMModelNotFoundError("Model 'x' is not installed in Ollama.", hint="Run:  ollama pull x")
    app = create_app(make_settings(), provider=FakeProvider(error=err))
    with TestClient(app) as c:
        r = c.post("/api/v1/chat", json={"message": "hi"}, headers=auth)
        assert r.status_code == 503
        assert r.json()["code"] == "llm_model_not_found"
        assert "ollama pull" in r.json()["hint"]
        m = c.get("/api/v1/metrics", headers=auth).json()
        assert m["errors"] == {"llm_model_not_found": 1}


def test_stream_error_event(auth):
    app = create_app(make_settings(), provider=FakeProvider(error=LLMUnavailableError("down")))
    with TestClient(app) as c:
        with c.stream("POST", "/api/v1/chat/stream", json={"message": "hi"}, headers=auth) as r:
            events = parse_sse(r.read().decode())
    assert events[-1][0] == "error"
    assert events[-1][1]["code"] == "llm_unavailable"


def test_status_and_metrics(client, auth):
    client.post("/api/v1/chat", json={"message": "hi"}, headers=auth)
    s = client.get("/api/v1/status", headers=auth).json()
    assert s["status"] == "ok"
    assert s["active_sessions"] == 1
    assert "api_key" not in json.dumps(s)
    m = client.get("/api/v1/metrics", headers=auth).json()
    assert m["requests"] == 1
    assert m["tokens_per_s"]["mean"] == 30.0


def test_provider_closed_on_shutdown(fake_provider):
    app = create_app(make_settings(), provider=fake_provider)
    with TestClient(app):
        pass
    assert fake_provider.closed
