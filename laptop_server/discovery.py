"""LAN discovery beacon: lets the Raspberry Pi find the laptop without typing an IP.

On a phone hotspot the laptop's address changes from day to day, so when the server is
reachable from the LAN (``AIRA_ALLOW_LAN=true``) it broadcasts a tiny UDP announcement
every few seconds on port 8766::

    {"service": "aira", "v": 1, "port": 8765, "tls": true, "fp": "<sha256 of the cert>", "host": "OMEN"}

It contains no secrets. The Pi uses the sender's address plus ``port``, and (with HTTPS)
only talks to a laptop whose certificate matches the one it pinned when it was paired,
so a fake beacon cannot steal the Pi's token.
"""

from __future__ import annotations

import asyncio
import hashlib
import ipaddress
import json
import logging
import socket
import ssl
from pathlib import Path

log = logging.getLogger("aira.discovery")
INTERVAL_S = 3.0


def cert_fingerprint(certfile: Path | None) -> str | None:
    """SHA-256 of the certificate (DER), hex. Same value the Pi shows when pairing."""
    if not certfile or not certfile.exists():
        return None
    pem = certfile.read_text(encoding="ascii", errors="ignore")
    start = pem.find("-----BEGIN CERTIFICATE-----")
    end = pem.find("-----END CERTIFICATE-----")
    if start < 0 or end < 0:
        return None
    der = ssl.PEM_cert_to_DER_cert(pem[start:end + len("-----END CERTIFICATE-----")] + "\n")
    return hashlib.sha256(der).hexdigest()


def broadcast_addresses() -> list[str]:
    """Directed broadcast address of every IPv4 LAN interface, plus the limited broadcast."""
    targets = {"255.255.255.255"}
    try:
        import psutil

        for addrs in psutil.net_if_addrs().values():
            for a in addrs:
                if a.family != socket.AF_INET or not a.netmask or a.address.startswith(("127.", "169.254.")):
                    continue
                net = ipaddress.IPv4Network(f"{a.address}/{a.netmask}", strict=False)
                if net.prefixlen < 31:
                    targets.add(str(net.broadcast_address))
    except Exception:  # psutil missing or odd interface: the limited broadcast still works
        pass
    return sorted(targets)


def beacon_payload(port: int, tls: bool, fingerprint: str | None) -> bytes:
    return json.dumps({"service": "aira", "v": 1, "port": port, "tls": tls, "fp": fingerprint,
                       "host": socket.gethostname()[:40]}, separators=(",", ":")).encode()


class Beacon:
    def __init__(self, *, port: int, tls: bool, fingerprint: str | None, discovery_port: int = 8766,
                 interval_s: float = INTERVAL_S):
        self.payload = beacon_payload(port, tls, fingerprint)
        self.discovery_port = discovery_port
        self.interval_s = interval_s
        self._task: asyncio.Task | None = None

    def start(self) -> None:
        self._task = asyncio.get_running_loop().create_task(self._run())
        log.info("Announcing AIRA on the LAN (UDP %d) so the Raspberry Pi can find it", self.discovery_port)

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

    async def _run(self) -> None:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        sock.setblocking(False)
        warned = False
        try:
            while True:
                for addr in broadcast_addresses():
                    try:
                        sock.sendto(self.payload, (addr, self.discovery_port))
                    except OSError as exc:  # no network right now (Wi-Fi off, hotspot gone)
                        if not warned:
                            log.debug("beacon to %s failed: %s", addr, exc)
                            warned = True
                await asyncio.sleep(self.interval_s)
        finally:
            sock.close()
