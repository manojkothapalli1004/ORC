"""Tests for backend/security.py local-network access controls."""

from __future__ import annotations

import asyncio
import base64
import json
from types import SimpleNamespace

import pytest
from fastapi import Request
from fastapi.responses import PlainTextResponse

import backend.security as security
from backend.security import (
    LocalConsoleSecurityMiddleware,
    _is_private_or_loopback,
    _unauthorized_response,
)


USERNAME = "admin"
PASSWORD = "s3cret-pass"
LAN_HOST = "192.168.1.50"
PUBLIC_HOST = "8.8.8.8"


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


async def _dummy_app(scope, receive, send):  # pragma: no cover - never invoked
    raise AssertionError("inner ASGI app should not be called directly")


def _basic(username: str, password: str) -> str:
    raw = f"{username}:{password}".encode("utf-8")
    return "Basic " + base64.b64encode(raw).decode("ascii")


def _dispatch(host, path="/api/status", headers=None):
    """Run the middleware dispatch for a synthetic request.

    Returns (response, downstream_called).
    """
    header_list = [
        (key.lower().encode("latin-1"), value.encode("latin-1"))
        for key, value in (headers or {}).items()
    ]
    scope = {
        "type": "http",
        "method": "GET",
        "path": path,
        "raw_path": path.encode("latin-1"),
        "query_string": b"",
        "headers": header_list,
        "client": (host, 54321) if host is not None else None,
        "server": ("testserver", 80),
        "scheme": "http",
        "http_version": "1.1",
        "root_path": "",
    }
    request = Request(scope)
    called: list[Request] = []

    async def call_next(req):
        called.append(req)
        return PlainTextResponse("ok")

    middleware = LocalConsoleSecurityMiddleware(_dummy_app)
    response = asyncio.run(middleware.dispatch(request, call_next))
    return response, bool(called)


def _json(response):
    return json.loads(response.body)


@pytest.fixture
def fake_settings(monkeypatch):
    fake = SimpleNamespace(
        local_network_only=True,
        local_access_username=USERNAME,
        local_access_password=PASSWORD,
    )
    monkeypatch.setattr(security, "settings", fake)
    return fake


# ---------------------------------------------------------------------------
# _is_private_or_loopback
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "host",
    [
        "127.0.0.1",
        "127.0.0.2",
        "::1",
        "localhost",
        "10.0.0.1",
        "172.16.0.1",
        "172.31.255.254",
        "192.168.1.50",
        "fd00::1",
        "::ffff:127.0.0.1",
        "::ffff:192.168.0.10",
        "  127.0.0.1  ",
    ],
)
def test_is_private_or_loopback_accepts_local_addresses(host):
    assert _is_private_or_loopback(host) is True


@pytest.mark.parametrize(
    "host",
    [
        None,
        "",
        "   ",
        "8.8.8.8",
        "1.1.1.1",
        "172.32.0.1",
        "2001:4860:4860::8888",
        "::ffff:8.8.8.8",
        "testclient",
        "example.com",
        "localhost.evil.com",
        "127.0.0.1/8",
        "127.0.0.1:8000",
        "../../etc/passwd",
        "127.0.0.1\x00",
        "1" * 10_000,
    ],
)
def test_is_private_or_loopback_rejects_other_values(host):
    assert _is_private_or_loopback(host) is False


# ---------------------------------------------------------------------------
# _unauthorized_response
# ---------------------------------------------------------------------------


def test_unauthorized_response_has_basic_challenge():
    response = _unauthorized_response()
    assert response.status_code == 401
    assert response.headers["www-authenticate"] == 'Basic realm="orchestrator-local"'


# ---------------------------------------------------------------------------
# LocalConsoleSecurityMiddleware: network restriction
# ---------------------------------------------------------------------------


def test_public_client_blocked_when_local_network_only(fake_settings):
    response, called = _dispatch(PUBLIC_HOST)
    assert response.status_code == 403
    assert _json(response) == {"detail": "Local network access only."}
    assert called is False


def test_missing_client_blocked_when_local_network_only(fake_settings):
    response, called = _dispatch(None)
    assert response.status_code == 403
    assert called is False


def test_public_client_without_auth_challenged_when_local_network_only_disabled(fake_settings):
    fake_settings.local_network_only = False
    response, called = _dispatch(PUBLIC_HOST)
    assert response.status_code == 401
    assert response.headers["www-authenticate"].startswith("Basic ")
    assert called is False


def test_public_client_with_valid_auth_passes_when_local_network_only_disabled(fake_settings):
    fake_settings.local_network_only = False
    response, called = _dispatch(PUBLIC_HOST, headers={"Authorization": _basic(USERNAME, PASSWORD)})
    assert response.status_code == 200
    assert called is True


def test_public_client_gets_503_without_configured_password(fake_settings):
    fake_settings.local_network_only = False
    fake_settings.local_access_password = ""
    response, called = _dispatch(PUBLIC_HOST)
    assert response.status_code == 503
    assert "LOCAL_ACCESS_PASSWORD" in _json(response)["detail"]
    assert called is False


@pytest.mark.parametrize("host", ["127.0.0.1", "::1", "::ffff:127.0.0.1"])
@pytest.mark.parametrize("path", ["/", "/api/status", "/static/app.js", "/docs"])
def test_loopback_client_needs_no_password(fake_settings, host, path):
    response, called = _dispatch(host, path=path)
    assert response.status_code == 200
    assert called is True


def test_ipv4_mapped_loopback_is_exempt(fake_settings):
    fake_settings.local_access_password = ""
    response, called = _dispatch("::ffff:127.0.0.1", path="/openapi.json")
    assert response.status_code == 200
    assert called is True


# ---------------------------------------------------------------------------
# LocalConsoleSecurityMiddleware: LAN basic auth
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("path", ["/", "/api/status", "/api/../api/status", "/static/app.js", "/healthz"])
def test_lan_client_without_auth_is_challenged(fake_settings, path):
    response, called = _dispatch(LAN_HOST, path=path)
    assert response.status_code == 401
    assert response.headers["www-authenticate"].startswith("Basic ")
    assert called is False


def test_lan_client_with_valid_credentials_passes(fake_settings):
    response, called = _dispatch(LAN_HOST, headers={"Authorization": _basic(USERNAME, PASSWORD)})
    assert response.status_code == 200
    assert called is True


def test_lan_client_requires_configured_password(fake_settings):
    fake_settings.local_access_password = ""
    response, called = _dispatch(LAN_HOST, headers={"Authorization": _basic(USERNAME, "")})
    assert response.status_code == 503
    assert "LOCAL_ACCESS_PASSWORD" in _json(response)["detail"]
    assert called is False


def test_lan_client_with_none_password_configured_gets_503(fake_settings):
    fake_settings.local_access_password = None
    response, called = _dispatch(LAN_HOST)
    assert response.status_code == 503
    assert called is False


@pytest.mark.parametrize(
    "auth_header",
    [
        "",
        "Bearer abc",
        "Basic",
        "Basic ",
        "Basic !!!not-base64!!!",
        "Basic " + base64.b64encode(b"\xff\xfe\xfd").decode("ascii"),
        "Basic " + base64.b64encode(b"no-colon-here").decode("ascii"),
        _basic(USERNAME, "wrong"),
        _basic(USERNAME, PASSWORD + "x"),
        _basic(USERNAME, ""),
        _basic("root", PASSWORD),
        _basic("", PASSWORD),
        _basic("../" + USERNAME, PASSWORD),
        _basic(USERNAME, "A" * 100_000),
        _basic("\u00e4dmin", PASSWORD),
    ],
)
def test_lan_client_with_bad_credentials_is_rejected(fake_settings, auth_header):
    headers = {"Authorization": auth_header} if auth_header else {}
    response, called = _dispatch(LAN_HOST, headers=headers)
    assert response.status_code == 401
    assert called is False


def test_password_containing_colon_is_supported(fake_settings):
    fake_settings.local_access_password = "pa:ss:word"
    response, called = _dispatch(LAN_HOST, headers={"Authorization": _basic(USERNAME, "pa:ss:word")})
    assert response.status_code == 200
    assert called is True


def test_non_ascii_configured_password_is_supported(fake_settings):
    fake_settings.local_access_password = "p\u00e4ssword"
    response, called = _dispatch(LAN_HOST, headers={"Authorization": _basic(USERNAME, "p\u00e4ssword")})
    assert response.status_code == 200
    assert called is True


def test_ipv4_mapped_lan_client_requires_auth(fake_settings):
    response, called = _dispatch("::ffff:192.168.1.50")
    assert response.status_code == 401
    assert called is False


# ---------------------------------------------------------------------------
# Previously known gaps, now fixed
# ---------------------------------------------------------------------------


def test_non_ascii_password_attempt_returns_401(fake_settings):
    response, called = _dispatch(LAN_HOST, headers={"Authorization": _basic(USERNAME, "p\u00e4ssword")})
    assert response.status_code == 401
    assert called is False


@pytest.mark.parametrize("path", ["/docs", "/openapi.json", "/redoc", "/api/docs"])
def test_lan_client_docs_paths_require_auth(fake_settings, path):
    response, called = _dispatch(LAN_HOST, path=path)
    assert response.status_code == 401
    assert called is False


@pytest.mark.parametrize("scheme", ["basic", "BASIC", "bAsIc"])
def test_lowercase_basic_scheme_is_accepted(fake_settings, scheme):
    header = _basic(USERNAME, PASSWORD).replace("Basic ", f"{scheme} ", 1)
    response, called = _dispatch(LAN_HOST, headers={"Authorization": header})
    assert response.status_code == 200
    assert called is True
