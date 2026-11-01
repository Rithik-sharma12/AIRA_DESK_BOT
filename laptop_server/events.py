"""In-process event hub: one place where AIRA announces what is happening.

Publishers: the agent engine (state, captions, confirmations), the reminder scheduler,
the web UI (its listening/speaking state) and devices (button presses).
Subscribers: every open web page (SSE) and every connected Raspberry Pi (WebSocket).

Events are plain dicts with a ``type`` key, e.g.::

    {"type": "state", "state": "thinking"}
    {"type": "caption", "role": "aira", "text": "Opening Chrome."}
    {"type": "reminder", "text": "Drink water"}
    {"type": "wake", "source": "pi"}
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import AsyncIterator
from typing import Any

log = logging.getLogger("aira.events")

STATES = ("idle", "listening", "thinking", "speaking", "error", "sleep")


class EventHub:
    def __init__(self, queue_size: int = 200):
        self._subs: set[asyncio.Queue] = set()
        self._queue_size = queue_size
        self.state: dict[str, Any] = {"type": "state", "state": "idle", "ts": time.time()}

    @property
    def subscriber_count(self) -> int:
        return len(self._subs)

    def publish(self, event: dict[str, Any]) -> None:
        event = {**event, "ts": event.get("ts", time.time())}
        if event.get("type") == "state":
            self.state = event
        for q in list(self._subs):
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:  # a stuck subscriber must not block AIRA
                log.debug("dropping event for a slow subscriber")

    def set_state(self, state: str, **extra: Any) -> None:
        if state in STATES:
            self.publish({"type": "state", "state": state, **extra})

    async def subscribe(self, *, send_current_state: bool = True) -> AsyncIterator[dict[str, Any]]:
        q: asyncio.Queue = asyncio.Queue(maxsize=self._queue_size)
        self._subs.add(q)
        try:
            if send_current_state:
                yield self.state
            while True:
                yield await q.get()
        finally:
            self._subs.discard(q)
