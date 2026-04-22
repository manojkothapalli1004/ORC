"""Local Claude Code CLI provider — uses the claude CLI on this machine.

Experimental, local-only, session/machine-dependent.
Requires `claude` CLI installed and authenticated (Claude Pro/Max/Code subscription).
Does NOT require ANTHROPIC_API_KEY.
"""

from __future__ import annotations

import asyncio
import json
import shutil
import time
from typing import Literal

from backend.logging_hooks import get_orchestrator_logger
from .base import ProviderRequest, ProviderResponse, TokenUsage

logger = get_orchestrator_logger(__name__)

_DEFAULT_TIMEOUT_S = 90

AuthState = Literal["unknown", "ok", "not_logged_in"]


class ClaudeCodeLocalAuthError(RuntimeError):
    """Raised when the local `claude` CLI reports it is not logged in."""


class ClaudeCodeLocalProvider:
    # Class-level auth-state cache so every operator-visible surface
    # (provider-status, chat) reports a consistent answer without each
    # caller having to re-probe the CLI.
    _cached_auth_state: AuthState = "unknown"
    _cached_auth_message: str = ""

    def __init__(self, default_model: str = "sonnet", timeout_s: int = _DEFAULT_TIMEOUT_S) -> None:
        self._default_model = default_model
        self._timeout_s = timeout_s
        self._cli_path: str | None = shutil.which("claude")

    @property
    def provider_name(self) -> str:
        return "claude_code_local"

    @property
    def is_available(self) -> bool:
        return self._cli_path is not None

    @property
    def unavailability_reason(self) -> str:
        if self._cli_path is None:
            return "claude CLI not found in PATH"
        return ""

    @classmethod
    def auth_state_snapshot(cls) -> dict[str, str]:
        return {"state": cls._cached_auth_state, "message": cls._cached_auth_message}

    @classmethod
    def reset_auth_state(cls) -> None:
        cls._cached_auth_state = "unknown"
        cls._cached_auth_message = ""

    async def complete(self, request: ProviderRequest) -> ProviderResponse:
        if not self._cli_path:
            raise RuntimeError("claude CLI not found in PATH")

        model = request.model or self._default_model
        args = [
            self._cli_path,
            "-p",
            "--bare",
            "--no-session-persistence",
            "--output-format", "json",
            "--model", model,
        ]
        if request.system_prompt:
            args.extend(["--system-prompt", request.system_prompt])
        # Prompt is piped via stdin to avoid CLI flag parsing issues
        # (chat history may contain "---" prefixes that look like flags)
        args.append("-")

        started = time.perf_counter()
        try:
            proc = await asyncio.create_subprocess_exec(
                *args,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(input=request.prompt.encode("utf-8")),
                timeout=self._timeout_s,
            )
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            raise RuntimeError(f"claude CLI timed out after {self._timeout_s}s")

        latency_ms = int((time.perf_counter() - started) * 1000)
        raw = stdout.decode("utf-8", errors="replace").strip()

        # Parse stdout first regardless of exit code: when the CLI runs
        # without a controlling TTY it exits non-zero but still writes the
        # full JSON result (e.g. `is_error: true, result: "Not logged in…"`)
        # to stdout. Treating rc != 0 as fatal here would drop that signal
        # and mask auth errors as generic runtime failures.
        payload: dict | None = None
        parse_error: Exception | None = None
        if raw:
            try:
                payload = self._parse_json_response(raw)
            except Exception as exc:
                parse_error = exc

        if payload is None:
            err = stderr.decode("utf-8", errors="replace").strip()
            logger.warning(
                "claude CLI failed (rc=%s): %s", proc.returncode, err[:500] or (str(parse_error) if parse_error else "")
            )
            raise RuntimeError(f"claude CLI exited {proc.returncode}: {err[:300] or (str(parse_error) if parse_error else '')}")

        if payload.get("is_error"):
            error_msg = str(payload.get("result", "unknown CLI error"))
            if self._is_not_logged_in(error_msg):
                type(self)._cached_auth_state = "not_logged_in"
                type(self)._cached_auth_message = error_msg
                raise ClaudeCodeLocalAuthError(error_msg)
            raise RuntimeError(f"claude CLI error: {error_msg[:300]}")

        content = payload.get("result", "")
        usage_data = payload.get("usage", {})
        usage = TokenUsage(
            input_tokens=usage_data.get("input_tokens", 0),
            output_tokens=usage_data.get("output_tokens", 0),
            total_tokens=usage_data.get("input_tokens", 0) + usage_data.get("output_tokens", 0),
        )

        # Live call succeeded — auth is definitely good right now.
        type(self)._cached_auth_state = "ok"
        type(self)._cached_auth_message = ""

        return ProviderResponse(
            role=request.role,
            provider_name=self.provider_name,
            model_used=model,
            content=content,
            token_usage=usage,
            latency_ms=latency_ms,
            request_id=payload.get("session_id"),
            metadata={"stop_reason": payload.get("stop_reason"), "cost_usd": payload.get("total_cost_usd")},
        )

    @staticmethod
    def _is_not_logged_in(message: str) -> bool:
        return "not logged in" in message.lower()

    @staticmethod
    def _parse_json_response(raw: str) -> dict:
        """Extract the JSON object from CLI output (may have trailing hook output)."""
        # Try the full output first
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            pass
        # CLI sometimes appends hook stderr after the JSON — find the JSON object
        for line in raw.splitlines():
            line = line.strip()
            if line.startswith("{"):
                try:
                    return json.loads(line)
                except json.JSONDecodeError:
                    continue
        raise RuntimeError(f"Could not parse JSON from claude CLI output: {raw[:300]}")
