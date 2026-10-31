"""FastAPI application factory for the AIRA laptop server."""

from __future__ import annotations

import logging
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from . import __version__
from .agent.conversation import ConversationManager
from .api.routes import build_router
from .api.web_routes import build_web_router
from .config import Settings
from .llm.base import LLMError, LLMProvider
from .llm.factory import create_provider
from .monitoring.metrics import MetricsRecorder
from .security.auth import make_api_key_dependency
from .security.web_pairing import WebPairing
from .voice.service import VoiceService

log = logging.getLogger("aira.app")


def create_app(settings: Settings, provider: LLMProvider | None = None,
               voice: VoiceService | None = None) -> FastAPI:
    settings.validate_for_startup()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        st = app.state
        h = await st.provider.health()
        if not h.reachable:
            log.error("LLM provider unreachable: %s", h.detail)
        elif not h.model_available:
            log.error("%s  Installed: %s", h.detail, ", ".join(h.installed_models) or "none")
        else:
            log.info("Ollama %s ready, model %s installed", h.server_version, settings.llm_model)
            if settings.llm_warmup_on_start:
                try:
                    secs = await st.provider.warmup()
                    log.info("Model warm-up finished in %.1f s", secs)
                except LLMError as exc:
                    log.error("Warm-up failed: %s", exc)
        if settings.web_ui_enabled:
            caps = st.voice.capabilities()
            log.info("Web UI: speech recognition on %s, speech output on %s",
                     caps["stt"] or "the browser", caps["tts"] or "the browser")
            log.warning("Web UI pairing code: %s", st.web_pairing.pretty_code)
        yield
        await st.voice.aclose()
        await st.provider.aclose()

    app = FastAPI(
        title="AIRA Laptop Server",
        version=__version__,
        description="AI brain for AIRA, the desktop companion: local LLM conversation and a web voice interface.",
        lifespan=lifespan,
    )
    app.state.settings = settings
    app.state.provider = provider or create_provider(settings)
    app.state.conversations = ConversationManager(
        max_turns=settings.history_max_turns,
        token_budget=settings.effective_history_budget,
        ttl_s=settings.session_ttl_minutes * 60,
        max_sessions=settings.max_sessions,
    )
    app.state.metrics = MetricsRecorder(settings.metrics_window)
    app.state.started_at = time.monotonic()
    app.state.web_pairing = WebPairing(settings.data_dir / "web_devices.json")
    app.state.voice = voice or VoiceService(settings)

    @app.exception_handler(LLMError)
    async def _llm_error(_: Request, exc: LLMError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.http_status,
            content={"code": exc.code, "message": str(exc), "hint": exc.hint},
        )

    require = make_api_key_dependency(settings.api_key, extra_check=app.state.web_pairing.check)
    app.include_router(build_router(require))
    app.include_router(build_web_router(require))
    return app
