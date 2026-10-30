"""Logging: readable console output plus JSON-lines files for analysis.

logs/aira.jsonl  - everything at the configured level
logs/audit.jsonl - security-relevant events only (auth failures; tool calls from Phase 3)
"""

from __future__ import annotations

import json
import logging
import logging.handlers
from datetime import datetime, timezone
from pathlib import Path


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        entry = {
            "ts": datetime.fromtimestamp(record.created, timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        extra = getattr(record, "data", None)
        if isinstance(extra, dict):
            entry.update(extra)
        if record.exc_info:
            entry["exc"] = self.formatException(record.exc_info)
        return json.dumps(entry, ensure_ascii=False)


def setup_logging(level: str = "INFO", log_dir: Path | None = Path("logs")) -> None:
    root = logging.getLogger()
    root.setLevel(level)
    for h in list(root.handlers):
        root.removeHandler(h)

    console = logging.StreamHandler()
    console.setFormatter(logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s", "%H:%M:%S"))
    root.addHandler(console)

    if log_dir is not None:
        log_dir = Path(log_dir)
        log_dir.mkdir(parents=True, exist_ok=True)
        main = logging.handlers.RotatingFileHandler(
            log_dir / "aira.jsonl", maxBytes=5_000_000, backupCount=5, encoding="utf-8"
        )
        main.setFormatter(JsonFormatter())
        root.addHandler(main)

        audit = logging.handlers.RotatingFileHandler(
            log_dir / "audit.jsonl", maxBytes=5_000_000, backupCount=10, encoding="utf-8"
        )
        audit.setFormatter(JsonFormatter())
        audit_logger = logging.getLogger("aira.audit")
        audit_logger.addHandler(audit)

    # uvicorn's access log duplicates our request logging
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)
