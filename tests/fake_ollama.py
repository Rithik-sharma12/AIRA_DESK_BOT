"""A small emulator of Ollama's REST API for tests and GPU-free development.

It implements the endpoints AIRA uses (/api/version, /api/tags, /api/ps, /api/chat,
/api/generate) with Ollama's response shapes, including NDJSON streaming and
nanosecond durations. Replies are canned, so it says nothing about model quality.

Run standalone:  python -m tests.fake_ollama --port 11500
"""

from __future__ import annotations

import argparse
import asyncio
import json
import time
from datetime import datetime, timezone

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse

INSTALLED = ["gemma4:e4b-it-qat", "gemma4:12b-it-qat"]


def create_fake_ollama(token_delay_s: float = 0.005, installed: list[str] | None = None) -> FastAPI:
    app = FastAPI()
    models = list(installed or INSTALLED)
    loaded: dict[str, float] = {}

    def now() -> str:
        return datetime.now(timezone.utc).isoformat()

    @app.get("/api/version")
    async def version():
        return {"version": "0.0.0-fake"}

    @app.get("/api/tags")
    async def tags():
        return {"models": [{"name": m, "model": m, "size": 6_100_000_000} for m in models]}

    @app.get("/api/ps")
    async def ps():
        return {
            "models": [
                {"name": m, "model": m, "size": 7_000_000_000, "size_vram": 7_000_000_000} for m in loaded
            ]
        }

    @app.post("/api/generate")
    async def generate(req: Request):
        body = await req.json()
        model = body.get("model")
        if model not in models:
            return JSONResponse({"error": f'model "{model}" not found, try pulling it first'}, status_code=404)
        if body.get("keep_alive") == 0:
            loaded.pop(model, None)
            return {"model": model, "created_at": now(), "response": "", "done": True, "done_reason": "unload"}
        load_ns = 0 if model in loaded else 1_500_000_000
        loaded[model] = time.time()
        return {"model": model, "created_at": now(), "response": "", "done": True, "load_duration": load_ns}

    @app.post("/api/chat")
    async def chat(req: Request):
        body = await req.json()
        model = body.get("model")
        if model not in models:
            return JSONResponse({"error": f'model "{model}" not found, try pulling it first'}, status_code=404)
        user = next((m["content"] for m in reversed(body["messages"]) if m["role"] == "user"), "")
        if "தமிழ்" in user or "vanakkam" in user.lower():
            reply = "வணக்கம்! நான் AIRA. உங்களுக்கு எப்படி help பண்ணட்டும்?"
        else:
            reply = f"Hi! I heard you say: {user[:60]}"
        tokens = reply.split(" ")
        load_ns = 0 if model in loaded else 1_500_000_000
        loaded[model] = time.time()

        async def stream():
            for i, tok in enumerate(tokens):
                await asyncio.sleep(token_delay_s)
                piece = tok if i == 0 else " " + tok
                yield json.dumps({"model": model, "created_at": now(),
                                  "message": {"role": "assistant", "content": piece}, "done": False}) + "\n"
            yield json.dumps({
                "model": model, "created_at": now(), "message": {"role": "assistant", "content": ""},
                "done": True, "done_reason": "stop",
                "total_duration": 900_000_000 + load_ns, "load_duration": load_ns,
                "prompt_eval_count": sum(len(m["content"]) // 4 for m in body["messages"]),
                "prompt_eval_duration": 120_000_000,
                "eval_count": len(tokens), "eval_duration": int(len(tokens) / 25 * 1e9),
            }) + "\n"

        if body.get("stream", True):
            return StreamingResponse(stream(), media_type="application/x-ndjson")
        return {"model": model, "message": {"role": "assistant", "content": reply}, "done": True}

    return app


if __name__ == "__main__":
    import uvicorn

    p = argparse.ArgumentParser()
    p.add_argument("--port", type=int, default=11500)
    a = p.parse_args()
    uvicorn.run(create_fake_ollama(token_delay_s=0.03), host="127.0.0.1", port=a.port)
