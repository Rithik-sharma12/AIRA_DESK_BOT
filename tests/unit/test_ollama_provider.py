import asyncio
import json

import httpx
import pytest

from laptop_server.llm import LLMError, LLMModelNotFoundError, LLMUnavailableError, OllamaProvider


def ndjson(*objs) -> bytes:
    return ("\n".join(json.dumps(o) for o in objs) + "\n").encode()


def make(handler, **kw) -> OllamaProvider:
    return OllamaProvider(base_url="http://ollama.test", model="gemma4:e4b-it-qat",
                          transport=httpx.MockTransport(handler), **kw)


def run(coro):
    return asyncio.run(coro)


async def collect(p, msgs):
    return [c async for c in p.stream_chat(msgs)]


def test_payload_shape_and_think_flag():
    p = make(lambda r: httpx.Response(200), num_ctx=4096, max_tokens=300)
    body = p.build_chat_payload([{"role": "user", "content": "hi"}])
    assert body["options"] == {"num_ctx": 4096, "temperature": 0.7, "top_p": 0.95, "num_predict": 300}
    assert body["stream"] is True and "think" not in body
    p.think = False
    assert p.build_chat_payload([])["think"] is False


def test_stream_parses_chunks_and_stats():
    seen = {}

    def handler(req: httpx.Request):
        seen["body"] = json.loads(req.content)
        return httpx.Response(200, content=ndjson(
            {"message": {"role": "assistant", "content": "", "thinking": "hmm"}, "done": False},
            {"message": {"role": "assistant", "content": "Vanakkam"}, "done": False},
            {"message": {"role": "assistant", "content": "!"}, "done": False},
            {"message": {"role": "assistant", "content": ""}, "done": True, "done_reason": "stop",
             "total_duration": 2_000_000_000, "load_duration": 500_000_000,
             "prompt_eval_count": 40, "prompt_eval_duration": 100_000_000,
             "eval_count": 50, "eval_duration": 2_000_000_000},
        ))

    p = make(handler)
    chunks = run(collect(p, [{"role": "user", "content": "hi"}]))
    assert "".join(c.text for c in chunks) == "Vanakkam!"
    final = chunks[-1]
    assert final.done
    s = final.stats
    assert s.completion_tokens == 50 and s.tokens_per_s == 25.0
    assert s.load_s == 0.5 and s.prompt_tokens == 40
    assert s.ttft_s is not None and s.done_reason == "stop"
    assert seen["body"]["model"] == "gemma4:e4b-it-qat"


def test_chat_collects_full_text():
    p = make(lambda r: httpx.Response(200, content=ndjson(
        {"message": {"content": "a"}, "done": False},
        {"message": {"content": "b"}, "done": True, "eval_count": 2, "eval_duration": 1}),
    ))
    res = run(p.chat([{"role": "user", "content": "x"}]))
    assert res.text == "ab"


def test_model_not_found():
    p = make(lambda r: httpx.Response(404, json={"error": 'model "gemma4:e4b-it-qat" not found, try pulling it first'}))
    with pytest.raises(LLMModelNotFoundError) as ei:
        run(collect(p, []))
    assert "ollama pull gemma4:e4b-it-qat" in ei.value.hint


def test_connection_refused():
    def handler(req):
        raise httpx.ConnectError("refused", request=req)

    with pytest.raises(LLMUnavailableError):
        run(collect(make(handler), []))


def test_error_inside_stream_and_truncated_stream():
    p = make(lambda r: httpx.Response(200, content=ndjson({"error": "out of memory"})))
    with pytest.raises(LLMError, match="out of memory"):
        run(collect(p, []))
    p2 = make(lambda r: httpx.Response(200, content=ndjson({"message": {"content": "partial"}, "done": False})))
    with pytest.raises(LLMError, match="before finishing"):
        run(collect(p2, []))


def test_health_reports_offload():
    def handler(req: httpx.Request):
        path = req.url.path
        if path == "/api/version":
            return httpx.Response(200, json={"version": "0.12.0"})
        if path == "/api/tags":
            return httpx.Response(200, json={"models": [{"name": "gemma4:e4b-it-qat"}, {"name": "gemma4:12b-it-qat"}]})
        if path == "/api/ps":
            return httpx.Response(200, json={"models": [
                {"name": "gemma4:12b-it-qat", "size": 10_000_000_000, "size_vram": 7_500_000_000}]})
        return httpx.Response(404)

    h = run(make(handler).health())
    assert h.reachable and h.model_available and h.server_version == "0.12.0"
    assert h.to_dict()["loaded_models"][0]["gpu_percent"] == 75.0


def test_health_model_missing_and_latest_alias():
    def handler(req):
        if req.url.path == "/api/tags":
            return httpx.Response(200, json={"models": [{"name": "gemma4:latest"}]})
        return httpx.Response(200, json={"version": "1", "models": []})

    p = make(handler)
    h = run(p.health())
    assert not h.model_available and "ollama pull" in h.detail
    p.model = "gemma4"
    assert run(p.health()).model_available


def test_health_unreachable():
    def handler(req):
        raise httpx.ConnectError("refused", request=req)

    h = run(make(handler).health())
    assert h.reachable is False and "not reachable" in h.detail


def test_loopback_urls_bypass_system_proxy():
    from laptop_server.llm.ollama_provider import OllamaProvider, is_loopback_url

    assert is_loopback_url("http://127.0.0.1:11434")
    assert is_loopback_url("http://localhost:11434/")
    assert is_loopback_url("http://[::1]:11434")
    assert not is_loopback_url("http://192.168.1.20:11434")
    p = OllamaProvider(base_url="http://127.0.0.1:11434", model="m")
    assert p._client._trust_env is False


def test_think_flag_rejected_is_retried_without_it():
    bodies = []

    def handler(req: httpx.Request):
        body = json.loads(req.content)
        bodies.append(body)
        if "think" in body:
            return httpx.Response(400, json={"error": '"llama3.1:8b" does not support thinking'})
        return httpx.Response(200, content=ndjson(
            {"message": {"content": "Hello"}, "done": False},
            {"message": {"content": ""}, "done": True, "done_reason": "stop"},
        ))

    p = make(handler, think=False)
    chunks = run(collect(p, [{"role": "user", "content": "hi"}]))
    assert "".join(c.text for c in chunks) == "Hello"
    assert len(bodies) == 2 and "think" in bodies[0] and "think" not in bodies[1]
    assert p.think is None  # remembered for later turns
    run(collect(p, [{"role": "user", "content": "again"}]))
    assert len(bodies) == 3


def test_reply_that_is_all_thinking_gets_a_spoken_fallback():
    from laptop_server.llm.ollama_provider import EMPTY_REPLY_FALLBACK

    p = make(lambda r: httpx.Response(200, content=ndjson(
        {"message": {"content": "", "thinking": "Let me consider... " * 50}, "done": False},
        {"message": {"content": ""}, "done": True, "done_reason": "length", "eval_count": 400},
    )))
    chunks = run(collect(p, [{"role": "user", "content": "hi"}]))
    assert chunks[-1].done and chunks[-1].text == EMPTY_REPLY_FALLBACK
    assert chunks[-1].stats.done_reason == "length"


def test_tools_are_sent_and_tool_calls_parsed():
    seen = {}

    def handler(req: httpx.Request):
        seen["body"] = json.loads(req.content)
        return httpx.Response(200, content=ndjson(
            {"message": {"role": "assistant", "content": "",
                         "tool_calls": [{"function": {"name": "open_app", "arguments": {"name": "chrome"}}},
                                        {"function": {"name": "volume", "arguments": '{"action": "up"}'}},
                                        {"function": {"arguments": {}}}]}, "done": False},
            {"message": {"content": ""}, "done": True, "done_reason": "stop"},
        ))

    tools = [{"type": "function", "function": {"name": "open_app", "parameters": {"type": "object"}}}]
    p = make(handler, think=False)
    chunks = run(collect_tools(p, [{"role": "user", "content": "open chrome"}], tools))
    assert seen["body"]["tools"] == tools and seen["body"]["think"] is False
    calls = [c for ch in chunks for c in ch.tool_calls]
    assert [(c.name, c.arguments) for c in calls] == [("open_app", {"name": "chrome"}), ("volume", {"action": "up"})]
    # a tool-call-only reply is not "empty": no fallback sentence
    assert "".join(c.text for c in chunks) == ""


async def collect_tools(p, msgs, tools):
    return [c async for c in p.stream_chat(msgs, tools=tools)]
