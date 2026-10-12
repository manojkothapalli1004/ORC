"""Local-network access controls for the orchestrator approval console."""

from __future__ import annotations

import base64
import binascii
import ipaddress
import secrets
from collections.abc import Callable

from fastapi import Request
from fastapi.responses import JSONResponse, Response
from starlette.middleware.base import BaseHTTPMiddleware

from backend.config import settings


_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "::1", "localhost", "::ffff:127.0.0.1"})


def _is_private_or_loopback(host: str | None) -> bool:
    if not host:
        return False
    normalized = host.strip()
    if normalized.startswith("::ffff:"):
        normalized = normalized.split(":")[-1]
    if normalized == "localhost":
        return True
    try:
        address = ipaddress.ip_address(normalized)
    except ValueError:
        return False
    return address.is_loopback or address.is_private


def _unauthorized_response() -> Response:
    return Response(
        status_code=401,
        headers={"WWW-Authenticate": 'Basic realm="orchestrator-local"'},
    )


def _credentials_valid(auth_header: str) -> bool:
    expected_password = str(settings.local_access_password or "")
    if not expected_password:
        return False
    scheme, _, token = auth_header.partition(" ")
    if scheme.lower() != "basic":
        return False
    token = token.strip()
    if not token:
        return False
    try:
        decoded = base64.b64decode(token, validate=True).decode("utf-8")
    except (binascii.Error, ValueError, UnicodeDecodeError):
        return False
    username, _, password = decoded.partition(":")
    expected_username = str(settings.local_access_username or "")
    username_ok = secrets.compare_digest(username.encode("utf-8"), expected_username.encode("utf-8"))
    password_ok = secrets.compare_digest(password.encode("utf-8"), expected_password.encode("utf-8"))
    return username_ok and password_ok


class LocalConsoleSecurityMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: Callable):
        client_host = request.client.host if request.client else None
        if settings.local_network_only and not _is_private_or_loopback(client_host):
            return JSONResponse({"detail": "Local network access only."}, status_code=403)

        if client_host in _LOOPBACK_HOSTS:
            return await call_next(request)

        if not settings.local_access_password:
            return JSONResponse(
                {"detail": "Set LOCAL_ACCESS_PASSWORD before using the console from another device on your LAN."},
                status_code=503,
            )

        auth_header = request.headers.get("authorization", "")
        if not _credentials_valid(auth_header):
            return _unauthorized_response()

        return await call_next(request)
