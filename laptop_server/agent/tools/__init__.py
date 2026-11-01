"""AIRA's task-automation tools (see builtin.py for the list)."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from ...events import EventHub
from ..scheduler import Scheduler
from .apps import AppCatalog, AppEntry  # noqa: F401
from .base import Tool, ToolRegistry, ToolResult  # noqa: F401
from .builtin import build_registry
from .system import Backend, DryRunBackend, create_backend  # noqa: F401


@dataclass
class Toolkit:
    registry: ToolRegistry
    backend: Backend
    catalog: AppCatalog
    scheduler: Scheduler


def create_toolkit(*, hub: EventHub, data_dir: Path, apps_file: Path | None, dry_run: bool | None,
                   backend: Backend | None = None) -> Toolkit:
    backend = backend or create_backend(dry_run)
    catalog = AppCatalog.load(apps_file)
    scheduler = Scheduler(hub, data_dir / "reminders.json", on_fire=lambda r: backend.notify_sound())
    return Toolkit(build_registry(backend, catalog, scheduler), backend, catalog, scheduler)
