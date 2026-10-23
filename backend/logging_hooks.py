"""Structured logging hooks for provider requests and responses."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from backend.config import settings

_SECRET_KEYS = {"api_key", "openai_api_key", "anthropic_api_key", "authorization", "token"}

# Keys that look secret-ish by suffix/prefix but are plain counters and must stay visible.
_SAFE_KEYS = frozenset(
    {
        "token_usage",
        "input_tokens",
        "output_tokens",
        "total_tokens",
        "max_tokens",
        "token_count",
        "prompt_tokens",
        "completion_tokens",
    }
)

# Normalised (lowercase, separators stripped) names that are always secrets.
_SECRET_COMPACT = frozenset(
    {
        "apikey",
        "xapikey",
        "openaiapikey",
        "anthropicapikey",
        "authorization",
        "xauthorization",
        "proxyauthorization",
        "token",
        "accesstoken",
        "refreshtoken",
        "bearertoken",
        "idtoken",
        "secret",
        "clientsecret",
        "password",
        "passwd",
        "credentials",
    }
)

# Suffixes (after separator normalisation) that mark a key as a secret.
# Plural counters like ``input_tokens`` end in ``_tokens``, not ``_token``, so they never match.
_SECRET_SUFFIXES = ("_api_key", "_apikey", "_secret", "_password", "_token", "_authorization")

_REDACTED = "***REDACTED***"


def _is_secret_key(key: Any) -> bool:
    """Return True if ``key`` names a credential that must never be logged.

    Works on any key type (non-str keys are stringified) and tolerates the
    common spelling variants: case, surrounding whitespace, ``-`` vs ``_``,
    and a vendor/role prefix such as ``x-api-key`` or ``github_token``.
    """
    name = str(key).strip().lower().replace("-", "_").replace(" ", "_")
    compact = name.replace("_", "")
    if name in _SAFE_KEYS:
        return False
    if name in _SECRET_KEYS or compact in _SECRET_COMPACT:
        return True
    return name.endswith(_SECRET_SUFFIXES)


def _sanitize(value: Any) -> Any:
    """Return a copy of ``value`` with secret-keyed entries replaced by a marker.

    Never mutates the input. Dict keys are preserved as the original objects.
    A secret key whose value is itself a container is replaced whole.
    """
    if isinstance(value, dict):
        return {
            key: (_REDACTED if _is_secret_key(key) else _sanitize(val))
            for key, val in value.items()
        }
    if isinstance(value, list):
        return [_sanitize(item) for item in value]
    if isinstance(value, tuple):
        return tuple(_sanitize(item) for item in value)
    return value


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "logger": record.name,
            "level": record.levelname,
            "message": record.getMessage(),
        }
        event = getattr(record, "event", None)
        if isinstance(event, dict):
            payload["event"] = _sanitize(event)
        return json.dumps(payload, default=str)


def get_orchestrator_logger(name: str = "orchestrator.providers") -> logging.Logger:
    logger = logging.getLogger(name)
    if logger.handlers:
        return logger

    settings.log_dir.mkdir(parents=True, exist_ok=True)
    formatter = JsonFormatter()

    stream_handler = logging.StreamHandler()
    stream_handler.setFormatter(formatter)

    file_handler = logging.FileHandler(Path(settings.log_dir) / "providers.log")
    file_handler.setFormatter(formatter)

    logger.setLevel(logging.INFO)
    logger.addHandler(stream_handler)
    logger.addHandler(file_handler)
    logger.propagate = False
    return logger


def log_provider_request(logger: logging.Logger, *, role: str, provider: str, model: str, request_id: str | None, metadata: dict[str, Any]) -> None:
    logger.info(
        "provider_request",
        extra={
            "event": {
                "role": role,
                "provider": provider,
                "model": model,
                "request_id": request_id,
                "metadata": metadata,
            }
        },
    )


def log_provider_response(logger: logging.Logger, *, role: str, provider: str, model: str, request_id: str | None, latency_ms: int, token_usage: dict[str, Any], is_mock: bool) -> None:
    logger.info(
        "provider_response",
        extra={
            "event": {
                "role": role,
                "provider": provider,
                "model": model,
                "request_id": request_id,
                "latency_ms": latency_ms,
                "token_usage": token_usage,
                "is_mock": is_mock,
            }
        },
    )
