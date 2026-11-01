"""Online AI provider (OpenAI-compatible) and the local -> online fallback."""

from __future__ import annotations

import asyncio
import json

import httpx
import pytest
from pydantic import SecretStr

from laptop_server.llm.base import LLMModelNotFoundError, LLMUnavailableError, StreamChunk
from laptop_server.llm.factory import create_provider
from laptop_server.llm.online_provider import (
    SERVICES,
    FallbackProvider,
    LLMAuthError,
    OnlineProvider,
    resolve_base_url,
    to_openai_messages,
)
from tests.conftest import make_settings


def sse(*objs) -> bytes:
    return b"".join(b"data: " + json.dumps(o).encode() + b"\n\n" for o in objs) + b"data: [DONE]\n\n"


def provider(handler, **kw) -> OnlineProvider:
    return OnlineProvider(base_url="https://ai.example/v1", api_key="sk-test", model="m-1",
                          transport=httpx.MockTransport(handler), **kw)


async def collect(p, messages, tools=None) -> list[StreamChunk]:
    return [c async for c in p.stream_chat(messages, tools)]


def test_streams_text_and_sends_the_key_and_tools():
    seen = {}

    def handler(req: httpx.Request) -> httpx.Response:
        seen["auth"] = req.headers["authorization"]
        seen["body"] = json.loads(req.content)
        return httpx.Response(200, content=sse(
            {"choices": [{"delta": {"content": "Hi! "}}]},
            {"choices": [{"delta": {"content": "I'm AIRA."}, "finish_reason": "stop"}],
             "usage": {"prompt_tokens": 12, "completion_tokens": 5}}))

    tools = [{"type": "function", "function": {"name": "volume", "parameters": {"type": "object"}}}]
    chunks = asyncio.run(collect(provider(handler), [{"role": "user", "content": "hello"}], tools))
    assert "".join(c.text for c in chunks) == "Hi! I'm AIRA."
    assert chunks[-1].done and chunks[-1].stats.completion_tokens == 5 and chunks[-1].stats.ttft_s is not None
    assert seen["auth"] == "Bearer sk-test"
    assert seen["body"]["model"] == "m-1" and seen["body"]["stream"] and seen["body"]["tools"] == tools


def test_streamed_tool_call_is_assembled():
    def handler(req):
        return httpx.Response(200, content=sse(
            {"choices": [{"delta": {"tool_calls": [{"index": 0, "id": "x", "function": {"name": "vol", "arguments": ""}}]}}]},
            {"choices": [{"delta": {"tool_calls": [{"index": 0, "function": {"name": "ume", "arguments": '{"dire'}}]}}]},
            {"choices": [{"delta": {"tool_calls": [{"index": 0, "function": {"arguments": 'ction": "up"}'}}]},
                          "finish_reason": "tool_calls"}]}))

    chunks = asyncio.run(collect(provider(handler), [{"role": "user", "content": "louder"}]))
    calls = chunks[-1].tool_calls
    assert [(c.name, c.arguments) for c in calls] == [("volume", {"direction": "up"})]


def test_tool_history_gets_ids_for_the_online_format():
    msgs = [{"role": "user", "content": "louder"},
            {"role": "assistant", "content": "", "tool_calls": [{"function": {"name": "volume", "arguments": {"d": 1}}}]},
            {"role": "tool", "tool_name": "volume", "content": "ok"}]
    out = to_openai_messages(msgs)
    call = out[1]["tool_calls"][0]
    assert call["id"] == out[2]["tool_call_id"] and json.loads(call["function"]["arguments"]) == {"d": 1}
    assert out[2]["role"] == "tool" and "tool_name" not in out[2]


@pytest.mark.parametrize("status,error", [(401, LLMAuthError), (404, LLMModelNotFoundError),
                                          (429, LLMUnavailableError)])
def test_errors_say_what_to_fix(status, error):
    p = provider(lambda req: httpx.Response(status, json={"error": {"message": "nope"}}))
    with pytest.raises(error):
        asyncio.run(collect(p, [{"role": "user", "content": "hi"}]))


def test_health_does_not_need_every_model_listed():
    p = provider(lambda req: httpx.Response(200, json={"data": [{"id": "other"}]}))
    h = asyncio.run(p.health())
    assert h.reachable and h.model_available and "not in the service's model list" in h.detail
    bad = provider(lambda req: httpx.Response(401, json={}))
    assert not asyncio.run(bad.health()).reachable


class _Down:
    name, model, supports_tools = "ollama", "local", True

    def __init__(self):
        self.calls = 0

    async def stream_chat(self, messages, tools=None):
        self.calls += 1
        raise LLMUnavailableError("Ollama is not running")
        yield  # pragma: no cover

    async def aclose(self):
        pass


def test_auto_mode_answers_online_when_ollama_is_down_and_retries_later():
    online = provider(lambda req: httpx.Response(200, content=sse({"choices": [{"delta": {"content": "Online!"}}]})))
    local = _Down()
    t = [0.0]
    fb = FallbackProvider(local, online, cooldown_s=60, clock=lambda: t[0])
    msgs = [{"role": "user", "content": "hi"}]
    assert "".join(c.text for c in asyncio.run(collect(fb, msgs))) == "Online!"
    assert fb.last_used == "online" and fb.model == "m-1"
    asyncio.run(collect(fb, msgs))
    assert local.calls == 1                                  # cooling down: straight to online
    t[0] = 61
    asyncio.run(collect(fb, msgs))
    assert local.calls == 2                                  # local is tried again later


def test_settings_build_the_right_provider():
    assert resolve_base_url("gemini", None) == SERVICES["gemini"]
    assert resolve_base_url(None, "http://localhost:1234/v1/") == "http://localhost:1234/v1"
    with pytest.raises(ValueError):
        resolve_base_url(None, None)
    s = make_settings(llm_provider="online", online_service="groq", online_model="m-1", online_api_key=SecretStr("k"))
    p = create_provider(s)
    assert isinstance(p, OnlineProvider) and p.base_url == SERVICES["groq"]
    s = make_settings(llm_provider="auto", online_service="openai", online_model="m-1", online_api_key=SecretStr("k"))
    assert isinstance(create_provider(s), FallbackProvider)
    with pytest.raises(ValueError, match="API key"):
        create_provider(make_settings(llm_provider="online", online_service="openai", online_model="m-1"))
    assert "k" not in repr(s.online_api_key)                 # the key is never printed
