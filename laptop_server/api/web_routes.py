"""Web voice interface: the page itself, browser pairing, and speech endpoints.

GET  /                         the web UI (public; it asks for pairing before doing anything)
GET  /mascot/manifest.json     AIRA's mascot sprites (public, shared with the Raspberry Pi)
GET  /mascot/{emotion}/{frame}
POST /api/v1/web/hello         shows the pairing code in the server console
POST /api/v1/web/pair          {code, name} -> {token}
GET  /api/v1/voice/capabilities  which speech engines run on the server        (auth)
POST /api/v1/voice/transcribe    multipart audio -> {text, language, ...}       (auth)
POST /api/v1/voice/speak         {text} -> audio/wav                            (auth)
"""

from __future__ import annotations

import json
import logging
import re
import tempfile
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, Response
from pydantic import BaseModel, ConfigDict, Field

from ..voice.service import VoiceUnavailable

log = logging.getLogger("aira.web")
WEB_DIR = Path(__file__).resolve().parents[1] / "web"
# One set of sprites for the web page and the Pi screen (raspberry_pi/aira_pi/assets/mascot).
MASCOT_DIR = Path(__file__).resolve().parents[2] / "raspberry_pi" / "aira_pi" / "assets" / "mascot"
_NAME = re.compile(r"^[a-z][a-z0-9_]{0,30}$")
_FRAME = re.compile(r"^[a-z0-9_]{1,40}\.png$")
SUFFIX = {"audio/webm": ".webm", "audio/ogg": ".ogg", "audio/mp4": ".m4a", "audio/mpeg": ".mp3",
          "audio/wav": ".wav", "audio/x-wav": ".wav", "audio/wave": ".wav"}


class PairRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    code: str = Field(min_length=6, max_length=7)
    name: str = Field(default="browser", max_length=40)


class SpeakRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text: str = Field(min_length=1, max_length=1000)


def build_web_router(require_api_key) -> APIRouter:
    public = APIRouter()
    private = APIRouter(prefix="/api/v1/voice", dependencies=[Depends(require_api_key)])

    @public.get("/", response_class=HTMLResponse, include_in_schema=False)
    async def index(request: Request) -> HTMLResponse:
        if not request.app.state.settings.web_ui_enabled:
            raise HTTPException(404, "web UI disabled (AIRA_WEB_UI_ENABLED=false)")
        return HTMLResponse((WEB_DIR / "index.html").read_text(encoding="utf-8"),
                            headers={"Cache-Control": "no-store"})

    @public.get("/mascot/manifest.json", include_in_schema=False)
    async def mascot_manifest() -> Response:
        path = MASCOT_DIR / "manifest.json"
        if not path.exists():
            raise HTTPException(404, "no mascot sprites")
        return Response(json.dumps(json.loads(path.read_text(encoding="utf-8"))), media_type="application/json",
                        headers={"Cache-Control": "no-cache"})

    @public.get("/mascot/{emotion}/{frame}", include_in_schema=False)
    async def mascot_frame(emotion: str, frame: str) -> FileResponse:
        # Strict names: only files inside the mascot folder can ever be served.
        if not _NAME.match(emotion) or not _FRAME.match(frame):
            raise HTTPException(404, "not found")
        path = MASCOT_DIR / emotion / frame
        if not path.is_file():
            raise HTTPException(404, "not found")
        return FileResponse(path, media_type="image/png", headers={"Cache-Control": "max-age=3600"})

    @public.post("/api/v1/web/hello")
    async def hello(request: Request) -> dict:
        p = request.app.state.web_pairing
        client = request.client.host if request.client else "?"
        if p.locked:
            log.warning("Web pairing is LOCKED after too many wrong codes. Restart the server to pair.")
        else:
            log.warning("Pairing requested by %s — code: %s", client, p.pretty_code)
        return {"locked": p.locked}

    @public.post("/api/v1/web/pair")
    async def pair(request: Request, body: PairRequest) -> dict:
        p = request.app.state.web_pairing
        client = request.client.host if request.client else "?"
        token = p.pair(body.code, body.name, client)
        if token is None:
            raise HTTPException(403, "pairing locked: restart the AIRA server" if p.locked else "wrong code")
        return {"token": token}

    @private.get("/capabilities")
    async def capabilities(request: Request) -> dict:
        return request.app.state.voice.capabilities()

    @private.post("/transcribe")
    async def transcribe(request: Request, audio: UploadFile = File(...), language: str = Form("auto")) -> dict:
        s = request.app.state.settings
        limit = int(s.max_audio_upload_mb * 1024 * 1024)
        ctype = (audio.content_type or "").split(";")[0].strip().lower()
        suffix = SUFFIX.get(ctype, ".webm")
        lang = None if language in ("", "auto") else language[:5]
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / f"utterance{suffix}"
            size = 0
            with path.open("wb") as f:
                while chunk := await audio.read(64 * 1024):
                    size += len(chunk)
                    if size > limit:
                        raise HTTPException(413, f"audio larger than {s.max_audio_upload_mb} MB")
                    f.write(chunk)
            if size == 0:
                raise HTTPException(422, "empty audio")
            try:
                tr = await request.app.state.voice.transcribe(path, lang)
            except VoiceUnavailable as exc:
                raise HTTPException(501, str(exc)) from exc
        log.info("transcribed %.1f s of audio in %.2f s (lang=%s)", tr.audio_s, tr.latency_s, tr.language)
        return {"text": tr.text, "language": tr.language, "language_probability": tr.language_probability,
                "audio_s": round(tr.audio_s, 2), "latency_s": round(tr.latency_s, 3)}

    @private.post("/speak")
    async def speak(request: Request, body: SpeakRequest) -> Response:
        try:
            clip = await request.app.state.voice.speak(body.text)
        except VoiceUnavailable as exc:
            raise HTTPException(501, str(exc)) from exc
        return Response(clip.to_wav_bytes(), media_type="audio/wav", headers={"Cache-Control": "no-store"})

    root = APIRouter()
    root.include_router(public)
    root.include_router(private)
    return root
