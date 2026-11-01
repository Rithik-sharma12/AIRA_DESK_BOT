"""Links to AIRA's devices and live events.

WS   /api/v1/device/ws        the Raspberry Pi (protocol: shared/protocol/messages.py)
GET  /api/v1/events           live events for the web page (SSE): reminders, Pi button, state
POST /api/v1/events/state     the web page reports listening/speaking/idle so the Pi face follows
GET  /api/v1/devices          connected devices

All of them need the API key or a paired-device token (``Authorization: Bearer ...``).
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import re
import socket
import time
from collections.abc import AsyncIterator
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from shared.protocol import Envelope, MessageType

from .. import __version__
from ..agent.persona import build_system_prompt
from ..events import STATES
from ..voice.text import SentenceStream

log = logging.getLogger("aira.devices")
audit = logging.getLogger("aira.audit")
SERVER_ID = "laptop"
MAX_FRAME_BYTES = 64 * 1024


class StateReport(BaseModel):
    model_config = ConfigDict(extra="forbid")
    state: str = Field(pattern="^(" + "|".join(STATES) + ")$")


PI_SCREENS = ("home", "chat", "music", "tools", "emotions", "about", "system")


class ScreenBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    screen: str = Field(max_length=16)


def _env(t: MessageType, payload: dict[str, Any], session_id: str | None = None) -> str:
    return Envelope(type=t, device_id=SERVER_ID, session_id=session_id, payload=payload).to_json()


def event_to_envelope(ev: dict[str, Any]) -> str | None:
    """Translate a hub event into a protocol frame for a device (None = not for devices)."""
    kind = ev.get("type")
    if kind == "state":
        payload: dict[str, Any] = {"expression": ev.get("state", "idle")}
        if ev.get("estimate_s") is not None:
            payload["hold_s"] = min(60.0, float(ev["estimate_s"]))
        return _env(MessageType.DISPLAY_STATE_CHANGED, payload)
    if kind == "caption":
        return _env(MessageType.CAPTION, {"role": ev.get("role", "aira"), "text": str(ev.get("text", ""))[:300]})
    if kind == "reminder":
        return _env(MessageType.REMINDER, {"text": ev.get("text", ""), "say": ev.get("say", ""),
                                           "kind": ev.get("kind", "reminder")})
    if kind == "confirm":
        return _env(MessageType.CONFIRM_REQUEST, {"prompt": ev.get("prompt", "")})
    if kind == "screen" and ev.get("screen") in PI_SCREENS:
        return _env(MessageType.SHOW_SCREEN, {"screen": ev["screen"]})
    return None


KEEPALIVE_S = 15.0


async def sse_stream(hub, is_disconnected, keepalive_s: float | None = None) -> AsyncIterator[str]:
    """Hub events as Server-Sent Events, with a keep-alive comment when it is quiet.

    The wait for the next event is a task that survives the keep-alive timeout: cancelling a
    pending ``__anext__`` (as ``asyncio.wait_for`` does) would end the subscription, and the
    stream would then die with "async generator raised StopAsyncIteration" after 15 quiet seconds.
    """
    yield "retry: 3000\n\n"
    agen = hub.subscribe()
    pending: asyncio.Future | None = None
    try:
        while True:
            if pending is None:
                pending = asyncio.ensure_future(agen.__anext__())
            done, _ = await asyncio.wait({pending}, timeout=keepalive_s or KEEPALIVE_S)
            if not done:
                yield ": keep-alive\n\n"
                if await is_disconnected():
                    return
                continue
            ev, pending = pending.result(), None
            yield f"event: {ev['type']}\ndata: {json.dumps(ev, ensure_ascii=False)}\n\n"
    finally:
        if pending is not None:
            pending.cancel()
            with contextlib.suppress(asyncio.CancelledError, StopAsyncIteration):
                await pending
        await agen.aclose()


def _bearer(value: str | None) -> str | None:
    if value and value.lower().startswith("bearer "):
        return value[7:].strip()
    return None


def build_device_router(require_api_key) -> APIRouter:
    router = APIRouter()
    private = APIRouter(prefix="/api/v1", dependencies=[Depends(require_api_key)])

    # ------------------------------------------------------------------ web events
    @private.get("/events")
    async def events(request: Request) -> StreamingResponse:
        hub = request.app.state.hub

        return StreamingResponse(sse_stream(hub, request.is_disconnected), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    @private.post("/events/state")
    async def report_state(request: Request, body: StateReport) -> dict:
        request.app.state.hub.set_state(body.state, source="web")
        return {"ok": True}

    @private.get("/devices")
    async def devices(request: Request) -> dict:
        return {"devices": list(request.app.state.devices.values())}

    @private.post("/devices/screen")
    async def show_screen(body: ScreenBody, request: Request) -> dict:
        """Switch the Pi's touch screen from the web page (useful while touch is not working)."""
        if body.screen not in PI_SCREENS:
            raise HTTPException(status_code=422, detail=f"screen must be one of {', '.join(PI_SCREENS)}")
        request.app.state.hub.publish({"type": "screen", "screen": body.screen})
        return {"ok": True, "devices": len(request.app.state.devices)}

    # ------------------------------------------------------------------ device link
    @router.websocket("/api/v1/device/ws")
    async def device_ws(ws: WebSocket) -> None:
        st = ws.app.state
        client = ws.client.host if ws.client else "?"
        if not st.check_token(_bearer(ws.headers.get("authorization"))):
            audit.warning("device_auth_failed client=%s", client)
            await ws.close(code=4401)
            return
        await ws.accept()
        try:
            hello = Envelope.from_json(await asyncio.wait_for(ws.receive_text(), timeout=10))
        except (asyncio.TimeoutError, ValidationError, WebSocketDisconnect, ValueError):
            await ws.close(code=4400)
            return
        if hello.type != MessageType.DEVICE_HELLO:
            await ws.close(code=4400)
            return

        device_id = hello.device_id
        caps = hello.payload.get("caps") or {}
        session_id = "dev-" + re.sub(r"[^A-Za-z0-9_-]", "-", device_id)[:56]
        info = {"device_id": device_id, "name": str(hello.payload.get("name", device_id))[:40], "client": client,
                "caps": caps, "version": str(hello.payload.get("version", "?"))[:20],
                "connected_at": time.time(), "last_seen": time.time()}
        st.devices[device_id] = info
        log.info("Device '%s' connected from %s (audio=%s)", info["name"], client, bool(caps.get("audio")))
        audit.warning("device_connected id=%s client=%s", device_id, client)

        send_lock = asyncio.Lock()

        async def send(frame: str) -> None:
            async with send_lock:
                await ws.send_text(frame)

        await send(_env(MessageType.SERVER_WELCOME, {
            "server_version": __version__, "model": st.settings.llm_model, "session_id": session_id,
            "host": socket.gethostname()[:40],
            "agent": st.agent.tools_enabled, "tts": st.voice.tts_name}, session_id))
        await send(event_to_envelope(st.hub.state) or "")

        async def forward_events() -> None:
            async for ev in st.hub.subscribe(send_current_state=False):
                frame = event_to_envelope(ev)
                if frame:
                    await send(frame)

        async def converse(text: str) -> None:
            session = st.conversations.get_or_create(session_id)
            prompt = build_system_prompt(user_name=st.settings.user_name,
                                         tamil_output_script=st.settings.tamil_output_script,
                                         persona_file=st.settings.persona_file,
                                         tools_enabled=st.agent.tools_enabled)
            sentences = SentenceStream()
            async with session.lock:
                async for ev in st.agent.run(session, prompt, text, source=info["name"]):
                    if ev.kind == "token" and caps.get("audio"):
                        for s in sentences.feed(ev.data["text"]):
                            await send(_env(MessageType.SAY, {"text": s}, session_id))
                    elif ev.kind == "done":
                        st.metrics.record(ev.data["stats"])
                        if caps.get("audio"):
                            for s in sentences.flush():
                                await send(_env(MessageType.SAY, {"text": s}, session_id))
                    elif ev.kind == "error":
                        await send(_env(MessageType.ERROR, {"code": ev.data["code"][:64],
                                                            "message": ev.data["message"][:500]}, session_id))

        forwarder = asyncio.create_task(forward_events())
        jobs: set[asyncio.Task] = set()
        try:
            while True:
                raw = await ws.receive_text()
                info["last_seen"] = time.time()
                if len(raw) > MAX_FRAME_BYTES:
                    continue
                try:
                    msg = Envelope.from_json(raw)
                except (ValidationError, ValueError):
                    log.debug("bad frame from %s", device_id)
                    continue
                if msg.type == MessageType.DEVICE_HEARTBEAT:
                    info["heartbeat"] = {k: msg.payload.get(k) for k in ("uptime_s", "cpu_temp_c", "wifi_rssi_dbm")}
                    await send(_env(MessageType.DEVICE_HEARTBEAT, {"uptime_s": time.monotonic() - st.started_at}))
                elif msg.type == MessageType.BUTTON_EVENT:
                    action = str(msg.payload.get("action", "press"))[:20]
                    log.info("Button %s on %s", action, info["name"])
                    st.hub.publish({"type": "wake", "source": device_id, "action": action})
                elif msg.type == MessageType.USER_TEXT:
                    text = str(msg.payload.get("text", "")).strip()[: st.settings.max_user_message_chars]
                    if text:
                        job = asyncio.create_task(converse(text))
                        jobs.add(job)
                        job.add_done_callback(jobs.discard)
                elif msg.type in (MessageType.LISTENING_STARTED, MessageType.SPEECH_STARTED):
                    st.hub.set_state("listening" if msg.type == MessageType.LISTENING_STARTED else "speaking",
                                     source=device_id)
                elif msg.type == MessageType.SPEECH_FINISHED:
                    st.hub.set_state("idle", source=device_id)
        except WebSocketDisconnect:
            pass
        finally:
            forwarder.cancel()
            for job in jobs:
                job.cancel()
            st.devices.pop(device_id, None)
            log.info("Device '%s' disconnected", info["name"])

    router.include_router(private)
    return router
