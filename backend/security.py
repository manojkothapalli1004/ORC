"""Local-network access controls for the orchestrator approval console."""

from __future__ import annotations

import base64
import ipaddress
import secrets
from collections.abc import Callable

from fastapi import Request
from fastapi.responses import JSONResponse, Response
from starlette.middleware.base import BaseHTTPMiddleware

from backend.config import settings


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


class LocalConsoleSecurityMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: Callable):
        client_host = request.client.host if request.client else None
        if settings.local_network_only and not _is_private_or_loopback(client_host):
            return JSONResponse({"detail": "Local network access only."}, status_code=403)

        if _is_private_or_loopback(client_host) and (request.url.path.startswith("/api") or request.url.path == "/" or request.url.path.startswith("/static/")):
            if _is_private_or_loopback(client_host) and client_host not in {"127.0.0.1", "::1", "localhost"}:
                if not settings.local_access_password:
                    return JSONResponse(
                        {"detail": "Set LOCAL_ACCESS_PASSWORD before using the console from another device on your LAN."},
                        status_code=503,
                    )
                auth_header = request.headers.get("authorization", "")
                if not auth_header.startswith("Basic "):
                    return _unauthorized_response()
                try:
                    decoded = base64.b64decode(auth_header.split(" ", 1)[1]).decode()
                except Exception:
                    return _unauthorized_response()
                username, _, password = decoded.partition(":")
                if username != settings.local_access_username:
                    return _unauthorized_response()
                if not secrets.compare_digest(password, settings.local_access_password):
                    return _unauthorized_response()

        return await call_next(request)
