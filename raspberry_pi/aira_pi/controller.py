"""Wireless controllers for AIRA's screen: the ESP32 joystick (and anything speaking the same JSON).

    ESP32 + KY-023 --Wi-Fi--> ws://<pi>:8771/  -->  focus moves on the Pi screen

The Pi announces itself on the network (UDP broadcast, port 8767) every 2 seconds, so the
ESP32 finds it even when the hotspot gives the Pi a new address:

    {"service": "aira-pi", "v": 1, "port": 8771, "name": "AIRA Pi"}

Protocol (JSON text frames, max 256 bytes):
    controller -> Pi   {"event": "hello", "device": "aira-joystick", "token": "<pairing token>"}   first, within 5 s
    Pi -> controller   {"ok": true, "name": "AIRA Pi"}   or   {"ok": false, "error": "..."} and close
    controller -> Pi   {"event": "navigate", "direction": "up"|"down"|"left"|"right"}
                       {"event": "select"}          short press
                       {"event": "back"}            long press: home
                       {"event": "ping"}            keep-alive (optional; WebSocket pings also work)

The token is made on the Pi's first start (``python3 -m aira_pi controller`` shows it) and goes
into the ESP32's secrets.h. Without it a controller cannot drive the screen (Restart and Shut
down are on the System screen). Ten wrong tokens lock the server until AIRA restarts.
"""

from __future__ import annotations

import asyncio
import json
import logging
import secrets
import socket
import time
from collections import deque
from collections.abc import Awaitable, Callable

log = logging.getLogger("aira_pi.controller")

DEFAULT_PORT = 8771
BEACON_PORT = 8767
MAX_FRAME = 256
DIRECTIONS = ("up", "down", "left", "right")
EVENTS = ("navigate", "select", "back", "ping")
TOKEN_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"        # no 0/O or 1/I to mistype


def new_token(length: int = 10) -> str:
    return "".join(secrets.choice(TOKEN_ALPHABET) for _ in range(length))


def parse_event(raw: str | bytes) -> tuple[str, str | None] | None:
    """A controller frame -> (event, direction) or None if it is not a valid event."""
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8", "replace")
    if len(raw) > MAX_FRAME:
        return None
    try:
        msg = json.loads(raw)
    except ValueError:
        return None
    if not isinstance(msg, dict) or msg.get("event") not in EVENTS:
        return None
    direction = msg.get("direction")
    if msg["event"] == "navigate" and direction not in DIRECTIONS:
        return None
    return msg["event"], direction if msg["event"] == "navigate" else None


class RateLimit:
    def __init__(self, per_second: float = 25, clock=time.monotonic):
        self.per_second, self.clock = per_second, clock
        self.times: deque[float] = deque()

    def allow(self) -> bool:
        now = self.clock()
        while self.times and now - self.times[0] > 1.0:
            self.times.popleft()
        if len(self.times) >= self.per_second:
            return False
        self.times.append(now)
        return True


class ControllerServer:
    def __init__(self, on_event: Callable[[str, str | None], Awaitable[None]], token: Callable[[], str],
                 port: int = DEFAULT_PORT, name: str = "AIRA Pi",
                 on_change: Callable[[str, bool], None] | None = None):
        self.on_event, self.token, self.port, self.name = on_event, token, port, name
        self.on_change = on_change or (lambda device, connected: None)
        self.connected: dict[int, str] = {}
        self.failures = 0
        self._server = None

    async def start(self, host: str = "0.0.0.0") -> None:
        try:
            from websockets.asyncio.server import serve           # websockets >= 13
        except ImportError:                                         # older Debian: legacy API
            from websockets import serve  # type: ignore[no-redef]
        self._server = await serve(self.handle, host, self.port, max_size=MAX_FRAME * 4,
                                   ping_interval=15, ping_timeout=10)
        log.info("Controller server on port %d", self.port)

    async def close(self) -> None:
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()

    async def handle(self, ws, *_):
        peer = getattr(ws, "remote_address", None)
        device = await self._hello(ws)
        if device is None:
            return
        self.connected[id(ws)] = device
        self.on_change(device, True)
        log.info("Controller '%s' connected from %s", device, peer[0] if peer else "?")
        limit = RateLimit()
        try:
            async for raw in ws:
                ev = parse_event(raw)
                if ev is None or not limit.allow():
                    continue
                if ev[0] != "ping":
                    await self.on_event(*ev)
        except Exception as exc:  # connection dropped (ConnectionClosed and friends)
            log.debug("controller %s: %s", device, exc)
        finally:
            self.connected.pop(id(ws), None)
            self.on_change(device, False)
            log.info("Controller '%s' disconnected", device)

    async def _hello(self, ws) -> str | None:
        if self.failures >= 10:
            await self._refuse(ws, "too many wrong tokens; restart AIRA")
            return None
        try:
            raw = await asyncio.wait_for(ws.recv(), timeout=5)
            msg = json.loads(raw) if len(raw) <= MAX_FRAME else {}
        except Exception:  # timeout, bad JSON, connection closed
            await self._refuse(ws, "send hello first")
            return None
        if not isinstance(msg, dict) or msg.get("event") != "hello":
            await self._refuse(ws, "send hello first")
            return None
        if not secrets.compare_digest(str(msg.get("token", "")), self.token()):
            self.failures += 1
            await self._refuse(ws, "wrong token")
            return None
        self.failures = 0
        await ws.send(json.dumps({"ok": True, "name": self.name}))
        return str(msg.get("device") or "controller")[:32]

    @staticmethod
    async def _refuse(ws, why: str) -> None:
        try:
            await ws.send(json.dumps({"ok": False, "error": why}))
            await ws.close(code=4401, reason=why[:60])
        except Exception:
            pass


def beacon_payload(port: int, name: str) -> bytes:
    return json.dumps({"service": "aira-pi", "v": 1, "port": port, "name": name[:40]},
                      separators=(",", ":")).encode()


def broadcast_targets(ips: list[str]) -> list[str]:
    """255.255.255.255 plus each local network's /24 broadcast (hotspots are /24)."""
    targets = {"255.255.255.255"}
    for ip in ips:
        parts = ip.split(".")
        if len(parts) == 4 and not ip.startswith(("127.", "169.254.")):
            targets.add(".".join(parts[:3] + ["255"]))
    return sorted(targets)


async def run_beacon(port: int, name: str, ips: Callable[[], list[str]], beacon_port: int = BEACON_PORT,
                     interval_s: float = 2.0) -> None:
    payload = beacon_payload(port, name)
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    sock.setblocking(False)
    try:
        while True:
            for target in broadcast_targets(ips()):
                try:
                    sock.sendto(payload, (target, beacon_port))
                except OSError:                     # no network yet: try again next round
                    pass
            await asyncio.sleep(interval_s)
    finally:
        sock.close()
