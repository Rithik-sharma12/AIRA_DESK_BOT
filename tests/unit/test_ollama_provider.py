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
