"""Entry point:  python -m laptop_server   (run from the repository root)."""

from __future__ import annotations

import logging
import socket
import sys

import uvicorn
from pydantic import ValidationError

from .app import create_app
from .config import LOOPBACK_HOSTS, Settings
from .monitoring.logging_setup import setup_logging


def lan_ips() -> list[str]:
    ips = set()
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("10.255.255.255", 1))  # no packet is sent; picks the outgoing interface
            ips.add(s.getsockname()[0])
    except OSError:
        pass
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            if not info[4][0].startswith("127."):
                ips.add(info[4][0])
    except OSError:
        pass
    return sorted(ips)


def web_urls(settings: Settings, tls: bool) -> list[str]:
    scheme = "https" if tls else "http"
    urls = [f"{scheme}://localhost:{settings.port}/"]
    if settings.host not in LOOPBACK_HOSTS:
        urls += [f"{scheme}://{ip}:{settings.port}/" for ip in lan_ips()]
    return urls


def main() -> int:
    try:
        settings = Settings()
        settings.validate_for_startup()
        tls = bool(settings.ssl_certfile or settings.ssl_keyfile)
        if tls and not (settings.ssl_certfile and settings.ssl_keyfile
                        and settings.ssl_certfile.exists() and settings.ssl_keyfile.exists()):
            raise ValueError("AIRA_SSL_CERTFILE and AIRA_SSL_KEYFILE must both point to existing files "
                             "(create them with: python scripts/make_dev_cert.py)")
    except (ValidationError, ValueError) as exc:
        print(f"\nAIRA configuration error:\n{exc}\n", file=sys.stderr)
        return 2

    setup_logging(settings.log_level, settings.log_dir)
    log = logging.getLogger("aira")
    think = {False: "off", True: "on", None: "model default"}[settings.llm_think]
    log.info("Starting AIRA (model %s, thinking %s)", settings.llm_model, think)
    if settings.web_ui_enabled:
        for url in web_urls(settings, tls):
            log.info("Web voice interface: %s", url)
        if settings.host not in LOOPBACK_HOSTS and not tls:
            log.warning("Other devices need HTTPS for the microphone: see HOW-TO-RUN.md, section 4")

    app = create_app(settings)
    uvicorn.run(
        app, host=settings.host, port=settings.port, log_config=None, access_log=False,
        ssl_certfile=str(settings.ssl_certfile) if tls else None,
        ssl_keyfile=str(settings.ssl_keyfile) if tls else None,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
