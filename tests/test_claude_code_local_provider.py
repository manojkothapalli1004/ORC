"""Argv and error-path tests for the claude_code_local provider.

The real `claude` CLI is never spawned: asyncio.create_subprocess_exec is
replaced with a fake that records argv and returns canned JSON on stdout.
"""

from __future__ import annotations

import asyncio
import json

import pytest

from backend.providers.base import ProviderRequest
from backend.providers.claude_code_local_provider import (
    ClaudeCodeLocalAuthError,
    ClaudeCodeLocalProvider,
)

_OK = b'{"is_error": false, "result": "ok", "type": "result"}'
_NOT_LOGGED_IN = b'{"is_error": true, "result": "Not logged in \xc2\xb7 Please run /login", "type": "result"}'


class _FakeProc:
    def __init__(self, stdout: bytes, returncode: int = 0):
        self._stdout, self.returncode = stdout, returncode

    async def communicate(self, input=None):
        return self._stdout, b""

    def kill(self):
        pass

    async def wait(self):
        return self.returncode


def _install(monkeypatch, stdout: bytes, calls: list):
    async def fake_exec(*argv, **kw):
        calls.append(list(argv))
        return _FakeProc(stdout)

    monkeypatch.setattr(
        "backend.providers.claude_code_local_provider.asyncio.create_subprocess_exec", fake_exec
    )


def _provider(monkeypatch):
    p = ClaudeCodeLocalProvider()
    monkeypatch.setattr(p, "_cli_path", "/fake/claude")
    return p


@pytest.fixture(autouse=True)
def _reset_auth_state():
    ClaudeCodeLocalProvider.reset_auth_state()
    yield
    ClaudeCodeLocalProvider.reset_auth_state()


def test_argv_skips_hooks_without_bare(monkeypatch):
    calls: list = []
    _install(monkeypatch, _OK, calls)
    provider = _provider(monkeypatch)

    resp = asyncio.run(provider.complete(ProviderRequest(role="reviewer", prompt="hi", model="sonnet")))

    assert len(calls) == 1
    argv = calls[0]
    assert "--bare" not in argv
    assert argv[0] == "/fake/claude"
    assert argv[1] == "-p"
    i = argv.index("--setting-sources")
    assert argv[i + 1] == ""
    j = argv.index("--settings")
    assert json.loads(argv[j + 1]) == {"disableAllHooks": True}
    assert "--no-session-persistence" in argv
    assert argv[argv.index("--output-format") + 1] == "json"
    assert argv[argv.index("--model") + 1] == "sonnet"
    assert argv[-1] == "-"
    assert "--system-prompt" not in argv
    assert resp.content == "ok"
    assert resp.provider_name == "claude_code_local"
    assert resp.model_used == "sonnet"
    assert ClaudeCodeLocalProvider.auth_state_snapshot()["state"] == "ok"


def test_system_prompt_is_passed_after_the_fixed_flags(monkeypatch):
    calls: list = []
    _install(monkeypatch, _OK, calls)
    provider = _provider(monkeypatch)

    asyncio.run(
        provider.complete(
            ProviderRequest(role="planner", prompt="hi", system_prompt="be terse", model="sonnet")
        )
    )

    argv = calls[0]
    k = argv.index("--system-prompt")
    assert argv[k + 1] == "be terse"
    assert k > argv.index("--model")
    assert k > argv.index("--setting-sources")
    assert k > argv.index("--settings")
    assert k + 1 < len(argv) - 1
    assert argv[-1] == "-"
    assert "--bare" not in argv


def test_not_logged_in_still_raises_auth_error(monkeypatch):
    calls: list = []
    _install(monkeypatch, _NOT_LOGGED_IN, calls)
    provider = _provider(monkeypatch)

    with pytest.raises(ClaudeCodeLocalAuthError, match="Not logged in"):
        asyncio.run(provider.complete(ProviderRequest(role="reviewer", prompt="hi", model="sonnet")))

    assert len(calls) == 1
    assert ClaudeCodeLocalProvider.auth_state_snapshot()["state"] == "not_logged_in"


def test_missing_cli_raises(monkeypatch):
    calls: list = []
    _install(monkeypatch, _OK, calls)
    provider = ClaudeCodeLocalProvider()
    monkeypatch.setattr(provider, "_cli_path", None)

    assert provider.is_available is False
    with pytest.raises(RuntimeError, match="not found in PATH"):
        asyncio.run(provider.complete(ProviderRequest(role="builder", prompt="hi")))
    assert calls == []
