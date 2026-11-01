"""Finding and talking to the laptop safely.

- ``discover()`` listens for the laptop's UDP beacon (port 8766) on the hotspot.
- With HTTPS, the laptop's self-signed certificate is pinned at pairing time and used as
  the *only* trusted certificate afterwards, so the token is never sent to anyone else
  (``pinned_context``). Hostname checks are off because the hotspot IP changes daily;
  the pinned certificate is what proves identity.
- ``http_json`` / ``http_bytes`` are tiny stdlib HTTP helpers (no extra packages).
"""

from __future__ import annotations

import asyncio
import hashlib
import http.client
import json
import socket
import ssl
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit


@dataclass
class Beacon:
    url: str
    fingerprint: str | None
    host: str


def parse_beacon(data: bytes, sender_ip: str) -> Beacon | None:
    try:
        b = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return None
    if not isinstance(b, dict) or b.get("service") != "aira":
        return None
    try:
        port = int(b.get("port", 8765))
    except (TypeError, ValueError):
        return None
    if not 0 < port < 65536:
        return None
    scheme = "https" if b.get("tls") else "http"
    fp = b.get("fp") if isinstance(b.get("fp"), str) else None
    return Beacon(f"{scheme}://{sender_ip}:{port}", fp, str(b.get("host", "?"))[:40])


class _BeaconProtocol(asyncio.DatagramProtocol):
    def __init__(self, queue: asyncio.Queue):
        self.queue = queue

    def datagram_received(self, data: bytes, addr) -> None:
        beacon = parse_beacon(data, addr[0])
        if beacon:
            self.queue.put_nowait(beacon)


async def discover(port: int = 8766, timeout: float = 10.0, fingerprint: str | None = None) -> Beacon | None:
    """Wait for a beacon; with ``fingerprint`` only accept the laptop we paired with."""
    loop = asyncio.get_running_loop()
    queue: asyncio.Queue = asyncio.Queue()
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(("0.0.0.0", port))
    transport, _ = await loop.create_datagram_endpoint(lambda: _BeaconProtocol(queue), sock=sock)
    try:
        deadline = loop.time() + timeout
        while (left := deadline - loop.time()) > 0:
            try:
                beacon = await asyncio.wait_for(queue.get(), left)
            except asyncio.TimeoutError:
                return None
            if fingerprint is None or beacon.fingerprint is None or beacon.fingerprint == fingerprint:
                return beacon
        return None
    finally:
        transport.close()


# --------------------------------------------------------------------------- TLS
def fetch_certificate(url: str, timeout: float = 5.0) -> tuple[str, str]:
    """Trust-on-first-use: get the laptop's certificate (PEM) and its SHA-256 fingerprint."""
    parts = urlsplit(url)
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    with socket.create_connection((parts.hostname, parts.port or 443), timeout=timeout) as raw:
        with ctx.wrap_socket(raw, server_hostname=parts.hostname) as tls:
            der = tls.getpeercert(binary_form=True)
    return ssl.DER_cert_to_PEM_cert(der), hashlib.sha256(der).hexdigest()


def pinned_context(cert_pem: str) -> ssl.SSLContext:
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    ctx.check_hostname = False            # IP changes on the hotspot; the pinned cert proves identity
    ctx.verify_mode = ssl.CERT_REQUIRED
    ctx.load_verify_locations(cadata=cert_pem)
    return ctx


def pretty_fingerprint(fp: str | None) -> str:
    return ":".join(fp[i:i + 4] for i in range(0, min(len(fp), 16), 4)).upper() if fp else "(plain HTTP)"


# -------------------------------------------------------------------------- HTTP
class HttpError(Exception):
    def __init__(self, status: int, detail: str):
        super().__init__(f"HTTP {status}: {detail}")
        self.status = status


def _request(url: str, method: str, path: str, *, body: bytes | None, headers: dict[str, str],
             ctx: ssl.SSLContext | None, timeout: float) -> tuple[int, bytes, str]:
    parts = urlsplit(url)
    if parts.scheme == "https":
        conn: http.client.HTTPConnection = http.client.HTTPSConnection(
            parts.hostname, parts.port or 443, timeout=timeout, context=ctx)
    else:
        conn = http.client.HTTPConnection(parts.hostname, parts.port or 80, timeout=timeout)
    try:
        conn.request(method, path, body=body, headers=headers)
        r = conn.getresponse()
        return r.status, r.read(), r.getheader("Content-Type", "")
    finally:
        conn.close()


def http_json(url: str, method: str, path: str, data: Any = None, *, token: str | None = None,
              ctx: ssl.SSLContext | None = None, timeout: float = 15.0) -> Any:
    headers = {"Content-Type": "application/json", "Accept": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    body = json.dumps(data).encode() if data is not None else None
    status, raw, _ = _request(url, method, path, body=body, headers=headers, ctx=ctx, timeout=timeout)
    try:
        parsed = json.loads(raw or b"null")
    except ValueError:
        parsed = None
    if status >= 400:
        detail = parsed.get("detail") if isinstance(parsed, dict) else raw[:200].decode("utf-8", "replace")
        raise HttpError(status, str(detail))
    return parsed


def http_bytes(url: str, method: str, path: str, body: bytes, content_type: str, *, token: str | None,
               ctx: ssl.SSLContext | None = None, timeout: float = 30.0) -> tuple[bytes, str]:
    headers = {"Content-Type": content_type}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    status, raw, ctype = _request(url, method, path, body=body, headers=headers, ctx=ctx, timeout=timeout)
    if status >= 400:
        raise HttpError(status, raw[:200].decode("utf-8", "replace"))
    return raw, ctype


def multipart(fields: dict[str, str], file_field: str, filename: str, data: bytes,
              file_type: str = "audio/wav") -> tuple[bytes, str]:
    boundary = "aira" + hashlib.sha1(data[:64] + filename.encode()).hexdigest()[:16]
    out = bytearray()
    for k, v in fields.items():
        out += f'--{boundary}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode()
    out += (f'--{boundary}\r\nContent-Disposition: form-data; name="{file_field}"; filename="{filename}"\r\n'
            f"Content-Type: {file_type}\r\n\r\n").encode()
    out += data + f"\r\n--{boundary}--\r\n".encode()
    return bytes(out), f"multipart/form-data; boundary={boundary}"


def ws_url(laptop_url: str) -> str:
    parts = urlsplit(laptop_url)
    scheme = "wss" if parts.scheme == "https" else "ws"
    return f"{scheme}://{parts.netloc}/api/v1/device/ws"
