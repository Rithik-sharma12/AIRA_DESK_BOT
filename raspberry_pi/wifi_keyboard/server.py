"""AIRA Wi-Fi keyboard: phone browser -> WebSocket -> Linux uinput keyboard on the Pi.

Standard library only (plus python3-evdev for uinput), to stay small on the Zero 2 W.

Security model
- Only clients from private / link-local addresses are served (hotspot or home LAN).
- A phone must pair once using a 6-digit code printed on AIRA's own console (the TFT):
  physical presence proves ownership. Pairing locks after 5 wrong codes until restart.
- Paired phones get a random token; only its SHA-256 is stored on the Pi. Revoke with
  `python3 -m wifi_keyboard --revoke-all`.
- Only allow-listed keys are injected (no SysRq). Messages are size- and rate-limited.
  All keys are released when a phone disconnects.
- Traffic is plain HTTP/WebSocket on the local network: use it on your own hotspot/LAN.

Run:  python3 -m wifi_keyboard --layout gb          (from the raspberry_pi/ folder)
      python3 -m wifi_keyboard --dry-run             (prints events, no uinput needed)
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import concurrent.futures
import hashlib
import hmac
import ipaddress
import json
import logging
import os
import secrets
import socket
import struct
import time
from dataclasses import dataclass, field
from pathlib import Path

from .injector import KeyInjector, RecordingInjector
from .keymap import ALLOWED, LAYOUTS, MODIFIERS, text_to_strokes

log = logging.getLogger("aira-keyboard")

WS_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
MAX_FRAME = 16 * 1024
MAX_TEXT_CHARS = 2000
MAX_PAIR_FAILURES = 5
AUTH_TIMEOUT_S = 60
STATIC = Path(__file__).with_name("static")


# --------------------------------------------------------------------------- state
@dataclass
class DeviceStore:
    path: Path
    devices: list[dict] = field(default_factory=list)

    @classmethod
    def load(cls, path: Path) -> "DeviceStore":
        store = cls(path)
        if path.exists():
            store.devices = json.loads(path.read_text(encoding="utf-8")).get("devices", [])
        return store

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps({"devices": self.devices}, indent=2), encoding="utf-8")
        os.chmod(tmp, 0o600)
        tmp.replace(self.path)

    @staticmethod
    def _hash(token: str) -> str:
        return hashlib.sha256(token.encode()).hexdigest()

    def add(self, name: str) -> str:
        token = secrets.token_urlsafe(32)
        self.devices.append({"name": name[:40] or "phone", "sha256": self._hash(token),
                             "created": time.strftime("%Y-%m-%d %H:%M:%S")})
        self.save()
        return token

    def check(self, token: str) -> str | None:
        h = self._hash(token)
        for d in self.devices:
            if hmac.compare_digest(d["sha256"], h):
                return d["name"]
        return None

    def revoke_all(self) -> int:
        n = len(self.devices)
        self.devices = []
        self.save()
        return n


class Pairing:
    def __init__(self):
        self.code = f"{secrets.randbelow(1_000_000):06d}"
        self.failures = 0

    @property
    def locked(self) -> bool:
        return self.failures >= MAX_PAIR_FAILURES

    def attempt(self, code: str) -> bool:
        if self.locked:
            return False
        ok = hmac.compare_digest(str(code).replace(" ", ""), self.code)
        if not ok:
            self.failures += 1
        return ok


class RateLimiter:
    def __init__(self, rate: float = 60.0, burst: float = 120.0):
        self.rate, self.capacity, self.tokens, self.t = rate, burst, burst, time.monotonic()

    def allow(self, cost: float = 1.0) -> bool:
        now = time.monotonic()
        self.tokens = min(self.capacity, self.tokens + (now - self.t) * self.rate)
        self.t = now
        if self.tokens >= cost:
            self.tokens -= cost
            return True
        return False


# ----------------------------------------------------------------------- websocket
class WebSocket:
    """Minimal RFC 6455 server side: text frames, ping/pong, close."""

    def __init__(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        self.r, self.w = reader, writer
        self.closed = False

    async def recv(self) -> str | None:
        parts: list[bytes] = []
        while True:
            try:
                b1, b2 = await self.r.readexactly(2)
            except (asyncio.IncompleteReadError, ConnectionError):
                return None
            fin, opcode, masked, n = b1 & 0x80, b1 & 0x0F, b2 & 0x80, b2 & 0x7F
            if not masked:  # clients must mask (RFC 6455 5.1)
                await self.close(1002)
                return None
            if n == 126:
                (n,) = struct.unpack("!H", await self.r.readexactly(2))
            elif n == 127:
                (n,) = struct.unpack("!Q", await self.r.readexactly(8))
            if n > MAX_FRAME or sum(map(len, parts)) + n > MAX_FRAME:
                await self.close(1009)
                return None
            mask = await self.r.readexactly(4)
            data = bytes(b ^ mask[i % 4] for i, b in enumerate(await self.r.readexactly(n)))
            if opcode == 0x8:
                await self.close(1000)
                return None
            if opcode == 0x9:
                await self._send(0xA, data)
                continue
            if opcode == 0xA:
                continue
            if opcode in (0x1, 0x0):
                parts.append(data)
                if fin:
                    try:
                        return b"".join(parts).decode("utf-8")
                    except UnicodeDecodeError:
                        await self.close(1007)
                        return None
                continue
            await self.close(1003)  # binary or unknown
            return None

    async def _send(self, opcode: int, payload: bytes) -> None:
        if self.closed:
            return
        n = len(payload)
        head = bytes([0x80 | opcode])
        if n < 126:
            head += bytes([n])
        elif n < 65536:
            head += bytes([126]) + struct.pack("!H", n)
        else:
            head += bytes([127]) + struct.pack("!Q", n)
        self.w.write(head + payload)
        await self.w.drain()

    async def send_json(self, obj: dict) -> None:
        try:
            await self._send(0x1, json.dumps(obj).encode())
        except (ConnectionError, RuntimeError):
            self.closed = True

    async def close(self, code: int = 1000) -> None:
        if self.closed:
            return
        try:
            await self._send(0x8, struct.pack("!H", code))
        except (ConnectionError, RuntimeError):
            pass
        self.closed = True


def accept_key(client_key: str) -> str:
    return base64.b64encode(hashlib.sha1((client_key + WS_GUID).encode()).digest()).decode()


# -------------------------------------------------------------------------- server
def is_local(addr: str) -> bool:
    try:
        ip = ipaddress.ip_address(addr.split("%")[0])
    except ValueError:
        return False
    if ip.version == 6 and ip.ipv4_mapped:
        ip = ip.ipv4_mapped
    return ip.is_private or ip.is_loopback or ip.is_link_local


def local_ips() -> list[str]:
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


class KeyboardServer:
    def __init__(self, injector: KeyInjector, store: DeviceStore, layout: str = "gb",
                 port: int = 8780, announce=print):
        if layout not in LAYOUTS:
            raise ValueError(f"layout must be one of {sorted(LAYOUTS)}")
        self.injector, self.store, self.layout, self.port = injector, store, layout, port
        self.pairing = Pairing()
        self.announce = announce
        # One worker thread keeps keystrokes in order and off the event loop.
        self.pool = concurrent.futures.ThreadPoolExecutor(max_workers=1, thread_name_prefix="inject")
        self.page = (STATIC / "index.html").read_bytes()

    def banner(self) -> None:
        urls = "  ".join(f"http://{ip}:{self.port}" for ip in local_ips()) or f"http://<pi-ip>:{self.port}"
        state = "LOCKED (restart service to retry)" if self.pairing.locked else \
            f"{self.pairing.code[:3]} {self.pairing.code[3:]}"
        self.announce(f"\n[AIRA keyboard] open on your phone: {urls}\n"
                      f"[AIRA keyboard] pairing code: {state}   layout: {self.layout}\n")

    async def run_inject(self, fn, *args) -> None:
        await asyncio.get_running_loop().run_in_executor(self.pool, fn, *args)

    # ---- HTTP
    async def handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        peer = (writer.get_extra_info("peername") or ("?", 0))[0]
        try:
            if not is_local(peer):
                log.warning("refused non-local client %s", peer)
                return
            try:
                raw = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), 10)
            except (asyncio.TimeoutError, asyncio.IncompleteReadError, asyncio.LimitOverrunError):
                return
            lines = raw.decode("latin-1").split("\r\n")
            parts = lines[0].split(" ")
            if len(parts) < 2:
                return
            method, target = parts[0], parts[1].split("?")[0]
            headers = {k.strip().lower(): v.strip() for k, v in
                       (l.split(":", 1) for l in lines[1:] if ":" in l)}
            if method == "GET" and target == "/ws" and headers.get("upgrade", "").lower() == "websocket":
                key = headers.get("sec-websocket-key")
                if not key:
                    return await self._http(writer, 400, b"missing key")
                writer.write(("HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\n"
                              "Connection: Upgrade\r\n"
                              f"Sec-WebSocket-Accept: {accept_key(key)}\r\n\r\n").encode())
                await writer.drain()
                await self.session(WebSocket(reader, writer), peer)
            elif method == "GET" and target in ("/", "/index.html"):
                await self._http(writer, 200, self.page, "text/html; charset=utf-8")
            elif method == "GET" and target == "/health":
                await self._http(writer, 200, b"ok")
            else:
                await self._http(writer, 404, b"not found")
        finally:
            try:
                writer.close()
                await writer.wait_closed()
            except (ConnectionError, OSError):
                pass

    async def _http(self, writer, status: int, body: bytes, ctype: str = "text/plain") -> None:
        reason = {200: "OK", 400: "Bad Request", 404: "Not Found"}.get(status, "OK")
        writer.write((f"HTTP/1.1 {status} {reason}\r\nContent-Type: {ctype}\r\n"
                      f"Content-Length: {len(body)}\r\nCache-Control: no-store\r\n"
                      "X-Content-Type-Options: nosniff\r\nConnection: close\r\n\r\n").encode() + body)
        await writer.drain()

    # ---- WebSocket session
    async def session(self, ws: WebSocket, peer: str) -> None:
        device = await self._authenticate(ws, peer)
        if not device:
            await ws.close(1008)
            return
        log.info("keyboard connected: %s (%s)", device, peer)
        limiter = RateLimiter()
        try:
            while True:
                raw = await ws.recv()
                if raw is None:
                    break
                if not limiter.allow():
                    await ws.send_json({"t": "err", "msg": "slow down"})
                    continue
                await self._command(ws, raw, limiter)
        finally:
            await self.run_inject(self.injector.release_all)
            log.info("keyboard disconnected: %s", device)

    async def _authenticate(self, ws: WebSocket, peer: str) -> str | None:
        while True:
            try:
                raw = await asyncio.wait_for(ws.recv(), AUTH_TIMEOUT_S)
            except asyncio.TimeoutError:
                return None
            if raw is None:
                return None
            try:
                msg = json.loads(raw)
            except ValueError:
                return None
            if msg.get("t") == "auth":
                name = self.store.check(str(msg.get("token", "")))
                if name:
                    await ws.send_json({"t": "ready", "layout": self.layout, "device": name})
                    return name
                await ws.send_json({"t": "unpaired", "msg": "This phone is not paired (or was revoked)."})
            elif msg.get("t") == "hello":
                self.banner()  # show the code on AIRA's screen for whoever is standing there
                await ws.send_json({"t": "need_code", "locked": self.pairing.locked})
            elif msg.get("t") == "pair":
                if self.pairing.attempt(str(msg.get("code", ""))):
                    token = self.store.add(str(msg.get("name", "phone")))
                    self.pairing = Pairing()  # a code works once
                    log.warning("paired new device '%s' from %s", msg.get("name", "phone"), peer)
                    await ws.send_json({"t": "paired", "token": token, "layout": self.layout})
                    return str(msg.get("name", "phone"))
                left = max(0, MAX_PAIR_FAILURES - self.pairing.failures)
                log.warning("wrong pairing code from %s (%d tries left)", peer, left)
                await ws.send_json({"t": "err", "msg": "wrong code" if left else "pairing locked",
                                    "left": left})
                if not left:
                    self.banner()
            else:
                return None

    async def _command(self, ws: WebSocket, raw: str, limiter: RateLimiter) -> None:
        try:
            msg = json.loads(raw)
            t = msg.get("t")
            mods = [m for m in msg.get("mods", []) if m in MODIFIERS]
            if t == "key":
                key = msg["key"]
                if key not in ALLOWED:
                    raise ValueError(f"key not allowed: {key}")
                repeat = max(1, min(int(msg.get("repeat", 1)), 200))
                if repeat > 1 and not limiter.allow(repeat / 10):
                    await ws.send_json({"t": "err", "msg": "slow down"})
                    return
                for _ in range(repeat):
                    await self.run_inject(self.injector.tap, key, tuple(mods))
            elif t in ("down", "up"):
                key = msg["key"]
                if key not in ALLOWED:
                    raise ValueError(f"key not allowed: {key}")
                await self.run_inject(self.injector.down if t == "down" else self.injector.up, key)
            elif t == "text":
                s = str(msg.get("s", ""))[:MAX_TEXT_CHARS]
                if not limiter.allow(len(s) / 10):
                    await ws.send_json({"t": "err", "msg": "slow down"})
                    return
                strokes, skipped = text_to_strokes(s, self.layout)
                await self.run_inject(self.injector.type_strokes, strokes, tuple(mods))
                if skipped:
                    await ws.send_json({"t": "skipped", "chars": skipped[:50]})
            elif t == "release_all":
                await self.run_inject(self.injector.release_all)
            elif t == "ping":
                await ws.send_json({"t": "pong"})
            else:
                raise ValueError(f"unknown message type: {t}")
        except (ValueError, KeyError, TypeError, AttributeError) as exc:
            await ws.send_json({"t": "err", "msg": str(exc)[:120]})

    async def serve(self, host: str = "0.0.0.0") -> None:
        server = await asyncio.start_server(self.handle, host, self.port, limit=8192)
        self.banner()
        async with server:
            await server.serve_forever()


# ---------------------------------------------------------------------------- main
class PrintingInjector(RecordingInjector):
    def _emit(self, code: int, value: int) -> None:
        super()._emit(code, value)
        print(f"  key {code:>3} {'down' if value else 'up'}")


def detect_layout(default: str = "gb") -> str:
    p = Path("/etc/default/keyboard")
    if p.exists():
        for line in p.read_text(errors="ignore").splitlines():
            if line.startswith("XKBLAYOUT="):
                first = line.split("=", 1)[1].strip().strip('"').split(",")[0]
                if first in LAYOUTS:
                    return first
    return default


def main() -> int:
    ap = argparse.ArgumentParser(description="AIRA Wi-Fi keyboard (phone -> uinput)")
    ap.add_argument("--port", type=int, default=8780)
    ap.add_argument("--host", default="0.0.0.0")
    ap.add_argument("--layout", choices=sorted(LAYOUTS), help="console layout (default: from /etc/default/keyboard)")
    ap.add_argument("--state-dir", type=Path, default=None,
                    help="where paired phones are stored (service: /var/lib/aira-keyboard; --dry-run: ~/.aira-keyboard)")
    ap.add_argument("--dry-run", action="store_true", help="print key events instead of using uinput")
    ap.add_argument("--revoke-all", action="store_true", help="forget every paired phone and exit")
    ap.add_argument("--list", action="store_true", help="list paired phones and exit")
    a = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="[AIRA keyboard] %(message)s")
    state_dir = a.state_dir or (Path.home() / ".aira-keyboard" if a.dry_run
                                else Path(os.environ.get("STATE_DIRECTORY", "/var/lib/aira-keyboard")))
    store = DeviceStore.load(state_dir / "devices.json")
    if a.list:
        for d in store.devices:
            print(f"{d['created']}  {d['name']}")
        return 0
    if a.revoke_all:
        print(f"revoked {store.revoke_all()} device(s)")
        return 0
    if a.dry_run:
        injector: KeyInjector = PrintingInjector()
    else:
        from .injector import UInputInjector

        injector = UInputInjector()
    server = KeyboardServer(injector, store, a.layout or detect_layout(), a.port,
                            announce=lambda s: print(s, flush=True))
    try:
        asyncio.run(server.serve(a.host))
    except KeyboardInterrupt:
        pass
    finally:
        injector.close()
    return 0
