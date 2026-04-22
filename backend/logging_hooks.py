"""Structured logging hooks for provider requests and responses."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from backend.config import settings

_SECRET_KEYS = {"api_key", "openai_api_key", "anthropic_api_key", "authorization", "token"}


def _sanitize(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: ("***REDACTED***" if key.lower() in _SECRET_KEYS else _sanitize(val))
            for key, val in value.items()
        }
    if isinstance(value, list):
        return [_sanitize(item) for item in value]
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
