"""Entry point:  python -m laptop_server   (run from the repository root)."""

from __future__ import annotations

import logging
import sys

import uvicorn
from pydantic import ValidationError

from .app import create_app
from .config import Settings
from .monitoring.logging_setup import setup_logging


def main() -> int:
    try:
        settings = Settings()
        settings.validate_for_startup()
    except (ValidationError, ValueError) as exc:
        print(f"\nAIRA configuration error:\n{exc}\n", file=sys.stderr)
        return 2

    setup_logging(settings.log_level, settings.log_dir)
    log = logging.getLogger("aira")
    log.info("Starting AIRA on http://%s:%d (model %s)", settings.host, settings.port, settings.llm_model)

    app = create_app(settings)
    uvicorn.run(app, host=settings.host, port=settings.port, log_config=None, access_log=False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
