"""Pairing for browsers (phones, other laptops) using the web voice interface.

Typing the 43-character API key on a phone is impractical, so the server prints a
6-digit code in its console at startup. A browser that enters the code gets its own
random session token; only the token's SHA-256 is kept on disk (data/web_devices.json).
Five wrong codes lock pairing until the server restarts. The API key keeps working.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import secrets
import time
from pathlib import Path

log = logging.getLogger("aira.web")
audit = logging.getLogger("aira.audit")

MAX_FAILURES = 5


class WebPairing:
    def __init__(self, store_path: Path):
        self.path = Path(store_path)
        self.code = f"{secrets.randbelow(1_000_000):06d}"
        self.failures = 0
        self.devices: list[dict] = []
        if self.path.exists():
            try:
                self.devices = json.loads(self.path.read_text(encoding="utf-8")).get("devices", [])
            except (ValueError, OSError):
                log.warning("could not read %s; starting with no paired browsers", self.path)

    @property
    def locked(self) -> bool:
        return self.failures >= MAX_FAILURES

    @property
    def pretty_code(self) -> str:
        return f"{self.code[:3]} {self.code[3:]}"

    @staticmethod
    def _hash(token: str) -> str:
        return hashlib.sha256(token.encode()).hexdigest()

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps({"devices": self.devices}, indent=2), encoding="utf-8")
        try:
            os.chmod(tmp, 0o600)
        except OSError:  # Windows: permissions handled by the user profile ACLs
            pass
        tmp.replace(self.path)

    def pair(self, code: str, name: str, client: str) -> str | None:
        """Returns a new session token, or None for a wrong code / locked pairing."""
        if self.locked:
            audit.warning("web_pair_locked client=%s", client)
            return None
        if not hmac.compare_digest(str(code).replace(" ", ""), self.code):
            self.failures += 1
            audit.warning("web_pair_failed client=%s failures=%d", client, self.failures)
            return None
        token = secrets.token_urlsafe(32)
        self.devices.append({"name": (name or "browser")[:40], "sha256": self._hash(token),
                             "created": time.strftime("%Y-%m-%d %H:%M:%S")})
        self._save()
        self.code = f"{secrets.randbelow(1_000_000):06d}"  # each code works once
        audit.warning("web_paired name=%s client=%s", name, client)
        log.info("Browser '%s' paired. Next pairing code: %s", name, self.pretty_code)
        return token

    def check(self, token: str) -> bool:
        if not token:
            return False
        h = self._hash(token)
        return any(hmac.compare_digest(d["sha256"], h) for d in self.devices)

    def revoke_all(self) -> int:
        n = len(self.devices)
        self.devices = []
        self._save()
        return n
