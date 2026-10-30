"""REST + Server-Sent-Events API (v1).

POST /api/v1/chat          -> full reply as JSON
POST /api/v1/chat/stream   -> reply streamed as SSE events: start, token, done | error
DELETE /api/v1/sessions/{id}
GET  /api/v1/status, /api/v1/metrics   (authenticated)
GET  /api/v1/health                    (unauthenticated, minimal)
"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import AsyncIterator
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Path, Request
from fastapi.responses import StreamingResponse

from .. import __version__
from ..agent.persona import build_system_prompt
from ..llm.base import LLMError
from .schemas import SESSION_ID_PATTERN, ChatRequest, ChatResponse, HealthResponse, StatusResponse

log = logging.getLogger("aira.api")


def _sse(event: str, data: dict[str, Any]) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def build_router(require_api_key) -> APIRouter:
    public = APIRouter(prefix="/api/v1")
    private = APIRouter(prefix="/api/v1", dependencies=[Depends(require_api_key)])

    # ------------------------------------------------------------------ helpers
    def _validate_message(request: Request, body: ChatRequest) -> str:
        text = body.message.strip()
        limit = request.app.state.settings.max_user_message_chars
        if not text:
            raise HTTPException(422, "message is empty")
        if len(text) > limit:
            raise HTTPException(413, f"message longer than {limit} characters")
        return text

    def _system_prompt(request: Request) -> str:
        s = request.app.state.settings
        return build_system_prompt(
            user_name=s.user_name, tamil_output_script=s.tamil_output_script, persona_file=s.persona_file
        )

    def _log_exchange(request: Request, session_id: str, user: str, reply: str, stats: dict) -> None:
        data: dict[str, Any] = {"event": "chat", "session": session_id, **stats}
        if request.app.state.settings.log_conversation_content:
            data.update(user=user, reply=reply)
        log.info("chat session=%s ttft=%s tok/s=%s", session_id, stats.get("ttft_s"), stats.get("tokens_per_s"),
                 extra={"data": data})

    # ------------------------------------------------------------------ public
    @public.get("/health", response_model=HealthResponse)
    async def health() -> HealthResponse:
        return HealthResponse(status="ok", version=__version__)

    # ----------------------------------------------------------------- private
    @private.get("/status", response_model=StatusResponse)
    async def status(request: Request) -> StatusResponse:
        st = request.app.state
        h = await st.provider.health()
        ok = h.reachable and h.model_available
        s = st.settings
        return StatusResponse(
            status="ok" if ok else "degraded",
            version=__version__,
            uptime_s=round(time.monotonic() - st.started_at, 1),
            provider=st.provider.name,
            llm=h.to_dict(),
            active_sessions=len(st.conversations),
            config={
                "model": s.llm_model,
                "num_ctx": s.llm_num_ctx,
                "max_tokens": s.llm_max_tokens,
                "think": s.llm_think,
                "tamil_output_script": s.tamil_output_script,
            },
        )

    @private.get("/metrics")
    async def metrics(request: Request) -> dict[str, Any]:
        return request.app.state.metrics.summary()

    @private.post("/chat", response_model=ChatResponse)
    async def chat(request: Request, body: ChatRequest) -> ChatResponse:
        st = request.app.state
        text = _validate_message(request, body)
        session = st.conversations.get_or_create(body.session_id)
        async with session.lock:
            messages = st.conversations.build_messages(session, _system_prompt(request), text)
            try:
                result = await st.provider.chat(messages)
            except LLMError as exc:
                st.metrics.record_error(exc.code)
                raise
            reply = result.text.strip()
            st.conversations.record_exchange(session, text, reply)
        st.metrics.record(result.stats)
        stats = result.stats.to_dict()
        _log_exchange(request, session.id, text, reply, stats)
        return ChatResponse(session_id=session.id, reply=reply, stats=stats)

    @private.post("/chat/stream")
    async def chat_stream(request: Request, body: ChatRequest) -> StreamingResponse:
        st = request.app.state
        text = _validate_message(request, body)
        session = st.conversations.get_or_create(body.session_id)
        system_prompt = _system_prompt(request)

        async def events() -> AsyncIterator[str]:
            async with session.lock:
                yield _sse("start", {"session_id": session.id})
                messages = st.conversations.build_messages(session, system_prompt, text)
                parts: list[str] = []
                try:
                    async for chunk in st.provider.stream_chat(messages):
                        if chunk.text:
                            parts.append(chunk.text)
                            yield _sse("token", {"text": chunk.text})
                        if chunk.done and chunk.stats:
                            reply = "".join(parts).strip()
                            st.conversations.record_exchange(session, text, reply)
                            st.metrics.record(chunk.stats)
                            stats = chunk.stats.to_dict()
                            _log_exchange(request, session.id, text, reply, stats)
                            yield _sse("done", {"session_id": session.id, "reply": reply, "stats": stats})
                except LLMError as exc:
                    st.metrics.record_error(exc.code)
                    log.warning("stream failed: %s", exc)
                    yield _sse("error", {"code": exc.code, "message": str(exc), "hint": exc.hint})

        return StreamingResponse(
            events(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    @private.delete("/sessions/{session_id}")
    async def reset_session(request: Request, session_id: str = Path(pattern=SESSION_ID_PATTERN)) -> dict[str, Any]:
        return {"session_id": session_id, "deleted": request.app.state.conversations.reset(session_id)}

    root = APIRouter()
    root.include_router(public)
    root.include_router(private)
    return root
