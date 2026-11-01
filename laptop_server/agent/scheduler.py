"""Timers and reminders.

Each reminder is an asyncio task that sleeps until it is due, then publishes a
``reminder`` event (the web page speaks it, the Pi shows it) and plays a Windows sound.
Reminders are saved to ``data/reminders.json`` so they survive a server restart;
ones that came due while AIRA was off are announced at start-up as missed.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from collections.abc import Callable
from dataclasses import asdict, dataclass
from pathlib import Path

from ..events import EventHub

log = logging.getLogger("aira.scheduler")
MAX_REMINDERS = 50
MAX_DELAY_S = 7 * 24 * 3600


@dataclass
class Reminder:
    id: str
    due: float            # epoch seconds
    text: str
    kind: str = "reminder"  # "timer" | "reminder"

    def remaining_s(self, now: float | None = None) -> float:
        return max(0.0, self.due - (now or time.time()))


class Scheduler:
    def __init__(self, hub: EventHub, store: Path | None, on_fire: Callable[[Reminder], None] | None = None,
                 clock: Callable[[], float] = time.time):
        self.hub = hub
        self.store = store
        self.on_fire = on_fire
        self.clock = clock
        self._items: dict[str, Reminder] = {}
        self._tasks: dict[str, asyncio.Task] = {}

    # ------------------------------------------------------------ persistence
    def _save(self) -> None:
        if not self.store:
            return
        try:
            self.store.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.store.with_suffix(".tmp")
            tmp.write_text(json.dumps([asdict(r) for r in self._items.values()], ensure_ascii=False, indent=1),
                           encoding="utf-8")
            tmp.replace(self.store)
        except OSError as exc:
            log.error("could not save reminders: %s", exc)

    async def start(self) -> None:
        if not self.store or not self.store.exists():
            return
        try:
            saved = [Reminder(**r) for r in json.loads(self.store.read_text(encoding="utf-8"))]
        except (OSError, ValueError, TypeError) as exc:
            log.error("ignoring unreadable %s: %s", self.store, exc)
            return
        now = self.clock()
        for r in saved:
            if r.due <= now:
                self._fire(r, missed=True)
            else:
                self._schedule(r)
        self._save()

    async def stop(self) -> None:
        for task in self._tasks.values():
            task.cancel()
        self._tasks.clear()

    # ----------------------------------------------------------------- API
    def add(self, delay_s: float, text: str, kind: str = "reminder") -> Reminder:
        if len(self._items) >= MAX_REMINDERS:
            raise ValueError("too many reminders")
        delay_s = max(1.0, min(float(delay_s), MAX_DELAY_S))
        r = Reminder(id=uuid.uuid4().hex[:6], due=self.clock() + delay_s, text=text[:200], kind=kind)
        self._schedule(r)
        self._save()
        return r

    def list(self) -> list[Reminder]:
        return sorted(self._items.values(), key=lambda r: r.due)

    def cancel(self, which: str | None = None) -> list[Reminder]:
        """Cancel by id, by words in the text, or everything when ``which`` is empty/'all'."""
        if not which or which.lower() == "all":
            victims = list(self._items.values())
        else:
            w = which.lower()
            victims = [r for r in self._items.values() if r.id == w or w in r.text.lower() or w == r.kind]
        for r in victims:
            self._items.pop(r.id, None)
            task = self._tasks.pop(r.id, None)
            if task:
                task.cancel()
        if victims:
            self._save()
        return victims

    # ------------------------------------------------------------- internals
    def _schedule(self, r: Reminder) -> None:
        self._items[r.id] = r
        self._tasks[r.id] = asyncio.get_running_loop().create_task(self._wait(r))

    async def _wait(self, r: Reminder) -> None:
        try:
            await asyncio.sleep(r.remaining_s(self.clock()))
        except asyncio.CancelledError:
            return
        self._items.pop(r.id, None)
        self._tasks.pop(r.id, None)
        self._save()
        self._fire(r)

    def _fire(self, r: Reminder, missed: bool = False) -> None:
        if r.kind == "timer":
            say = f"Time's up! Your {r.text} timer is done." if r.text and r.text != "timer" else "Time's up!"
        else:
            say = f"Reminder: {r.text}"
        if missed:
            say = "While I was off, you missed a reminder. " + say
        log.warning("%s fired: %s", r.kind, r.text, extra={"data": {"event": "reminder", "kind": r.kind}})
        self.hub.publish({"type": "reminder", "kind": r.kind, "text": r.text, "say": say, "missed": missed})
        if self.on_fire:
            try:
                self.on_fire(r)
            except Exception:  # a sound failing must not break reminders
                log.exception("reminder sound failed")


def describe_delay(seconds: float) -> str:
    seconds = int(round(seconds))
    if seconds < 60:
        return f"{seconds} second{'s' if seconds != 1 else ''}"
    minutes, s = divmod(seconds, 60)
    if minutes < 60:
        return f"{minutes} minute{'s' if minutes != 1 else ''}" + (f" {s} seconds" if s and minutes < 5 else "")
    hours, m = divmod(minutes, 60)
    return f"{hours} hour{'s' if hours != 1 else ''}" + (f" {m} minutes" if m else "")
