"""FastAPI application factory for the AIRA laptop server."""

from __future__ import annotations

import asyncio
import logging
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from . import __version__
from .agent.conversation import ConversationManager
from .agent.engine import AgentEngine
from .agent.router import FastRouter
from .agent.tools import Backend, create_toolkit
from .api.device_routes import build_device_router
from .api.routes import build_router
from .api.web_routes import build_web_router
from .config import LOOPBACK_HOSTS, Settings
from .discovery import Beacon, cert_fingerprint
from .events import EventHub
from .llm.base import LLMError, LLMProvider
from .llm.factory import create_provider
from .monitoring.metrics import MetricsRecorder
from .security.auth import make_api_key_dependency, make_token_checker
from .security.web_pairing import WebPairing
from .voice.service import VoiceService

log = logging.getLogger("aira.app")


def quiet_client_disconnects(loop: asyncio.AbstractEventLoop) -> None:
    """Hide one harmless Windows traceback: when a browser drops a connection, Python's Proactor
    event loop logs "ConnectionResetError: [WinError 10054]" from ``_call_connection_lost``.
    Nothing is wrong (the client simply left), so only that case is silenced; every other
    error still goes to the normal handler."""
    previous = loop.get_exception_handler()

    def handler(lp: asyncio.AbstractEventLoop, context: dict) -> None:
        exc = context.get("exception")
        where = f"{context.get('message', '')} {context.get('handle', '')}"
        if isinstance(exc, (ConnectionResetError, ConnectionAbortedError)) and "_call_connection_lost" in where:
            log.debug("client connection dropped: %s", exc)
            return
        if previous is not None:
            previous(lp, context)
        else:
            lp.default_exception_handler(context)

    loop.set_exception_handler(handler)


def create_app(settings: Settings, provider: LLMProvider | None = None,
               voice: VoiceService | None = None, tool_backend: Backend | None = None) -> FastAPI:
    settings.validate_for_startup()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        st = app.state
        quiet_client_disconnects(asyncio.get_running_loop())
        h = await st.provider.health()
        if not h.reachable:
            log.error("LLM provider unreachable: %s", h.detail)
        elif not h.model_available:
            log.error("%s  Installed: %s", h.detail, ", ".join(h.installed_models) or "none")
        else:
            if (h.server_version or "").startswith("http"):        # the online provider reports its URL
                log.info("Online AI ready: %s at %s%s", h.model, h.server_version, f" ({h.detail})" if h.detail else "")
            else:
                log.info("Ollama %s ready, model %s installed", h.server_version, settings.llm_model)
            if settings.llm_provider == "auto":
                log.info("AI mode auto: local Ollama first, online AI (%s) when Ollama is unreachable",
                         settings.online_model)
            if settings.llm_warmup_on_start:
                try:
                    secs = await st.provider.warmup()
                    log.info("Model warm-up finished in %.1f s", secs)
                except LLMError as exc:
                    log.error("Warm-up failed: %s", exc)
        if st.toolkit is not None:
            await st.toolkit.scheduler.start()
            mode = "DRY-RUN (actions are only logged)" if st.toolkit.backend.dry_run else "on"
            log.info("Task automation %s: %d skills, instant commands %s", mode, len(st.toolkit.registry.names()),
                     "on" if settings.agent_fast_commands else "off")
        voice_warmup = asyncio.create_task(st.voice.warmup())
        if settings.web_ui_enabled:
            caps = st.voice.capabilities()
            log.info("Web UI: speech recognition on %s, speech output on %s",
                     caps["stt"] or "the browser", caps["tts"] or "the browser")
            log.warning("Web UI pairing code: %s", st.web_pairing.pretty_code)
        beacon = None
        if settings.allow_lan and settings.host in LOOPBACK_HOSTS:
            log.warning("AIRA_ALLOW_LAN=true but AIRA_HOST=%s: only this laptop can connect. "
                        "Set AIRA_HOST=0.0.0.0 for the phone / Raspberry Pi.", settings.host)
        elif settings.allow_lan and settings.device_discovery:
            fp = cert_fingerprint(settings.ssl_certfile)
            beacon = Beacon(port=settings.port, tls=bool(settings.ssl_certfile), fingerprint=fp,
                            discovery_port=settings.discovery_port)
            beacon.start()
            if fp:
                log.info("Certificate fingerprint (the Pi shows this when pairing): %s", fp[:16])
        yield
        voice_warmup.cancel()
        if beacon:
            await beacon.stop()
        if st.toolkit is not None:
            await st.toolkit.scheduler.stop()
        await st.voice.aclose()
        if settings.llm_keep_alive.strip() in ("-1", "-1m", "-1h") and hasattr(st.provider, "unload"):
            try:                                   # kept loaded while AIRA ran; give the GPU back now
                await asyncio.wait_for(st.provider.unload(), timeout=5)
            except Exception as exc:  # Ollama already gone, etc.
                log.debug("model unload skipped: %s", exc)
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
    app.state.hub = EventHub()
    app.state.devices = {}
    toolkit = None
    if settings.agent_enabled:
        apps_file = settings.agent_apps_file
        toolkit = create_toolkit(hub=app.state.hub, data_dir=settings.data_dir,
                                 apps_file=apps_file if apps_file and apps_file.exists() else None,
                                 dry_run=settings.agent_dry_run, backend=tool_backend)
    app.state.toolkit = toolkit
    app.state.agent = AgentEngine(
        app.state.provider, app.state.conversations,
        tools=toolkit.registry if toolkit else None,
        router=FastRouter(toolkit.catalog) if toolkit else None,
        hub=app.state.hub, max_rounds=settings.agent_max_tool_rounds,
        fast_commands=settings.agent_fast_commands,
    )

    @app.exception_handler(LLMError)
    async def _llm_error(_: Request, exc: LLMError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.http_status,
            content={"code": exc.code, "message": str(exc), "hint": exc.hint},
        )

    require = make_api_key_dependency(settings.api_key, extra_check=app.state.web_pairing.check)
    app.state.check_token = make_token_checker(settings.api_key, extra_check=app.state.web_pairing.check)
    app.include_router(build_router(require))
    app.include_router(build_web_router(require))
    app.include_router(build_device_router(require))
    return app
