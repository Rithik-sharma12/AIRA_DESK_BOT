"""API-key authentication for the laptop server.

Clients send ``Authorization: Bearer <key>`` (preferred) or ``X-API-Key: <key>``.
Comparison is constant-time. Per-device pairing tokens replace the single shared key
in Phase 6, behind the same dependency.
"""

from __future__ import annotations

import hmac
import logging

from fastapi import Depends, Header, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import SecretStr

audit = logging.getLogger("aira.audit")

_bearer = HTTPBearer(auto_error=False)


def make_api_key_dependency(expected: SecretStr):
    expected_bytes = expected.get_secret_value().encode("utf-8")

    async def require_api_key(
        request: Request,
        creds: HTTPAuthorizationCredentials | None = Depends(_bearer),
        x_api_key: str | None = Header(default=None, alias="X-API-Key"),
    ) -> None:
        token = creds.credentials if creds else x_api_key
        if not token or not expected_bytes or not hmac.compare_digest(token.encode("utf-8"), expected_bytes):
            client = request.client.host if request.client else "unknown"
            audit.warning("auth_failed client=%s path=%s", client, request.url.path)
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Missing or invalid API key",
                headers={"WWW-Authenticate": "Bearer"},
            )

    return require_api_key
