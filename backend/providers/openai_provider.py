"""OpenAI provider implementation."""

from __future__ import annotations

import time
from typing import Any

_OPENAI_IMPORT_ERROR: str | None = None
try:
    from openai import AsyncOpenAI
except ImportError as _exc:
    AsyncOpenAI = None
    _OPENAI_IMPORT_ERROR = str(_exc)

from backend.config import settings
from backend.logging_hooks import get_orchestrator_logger, log_provider_request, log_provider_response
from .base import ProviderRequest, ProviderResponse, TokenUsage

logger = get_orchestrator_logger(__name__)

if _OPENAI_IMPORT_ERROR:
    logger.warning("openai SDK not installed: %s", _OPENAI_IMPORT_ERROR)


class OpenAIProvider:
    def __init__(self, api_key: str | None = None, default_model: str = "gpt-4o") -> None:
        self._api_key = api_key or settings.openai_api_key
        self._default_model = default_model
        self._client = AsyncOpenAI(api_key=self._api_key) if (self._api_key and AsyncOpenAI is not None) else None

    @property
    def provider_name(self) -> str:
        return "openai"

    @property
    def is_available(self) -> bool:
        return bool(self._api_key and self._client)

    @property
    def unavailability_reason(self) -> str:
        if self.is_available:
            return ""
        if AsyncOpenAI is None:
            return f"openai SDK not installed ({_OPENAI_IMPORT_ERROR})"
        if not self._api_key:
            return "OPENAI_API_KEY not set"
        return "unknown"

    async def complete(self, request: ProviderRequest) -> ProviderResponse:
        if not self._client:
            raise RuntimeError("OpenAI provider is unavailable: missing API key")

        model = request.model or self._default_model
        request_id = request.metadata.get("request_id") if isinstance(request.metadata, dict) else None
        log_provider_request(
            logger,
            role=request.role,
            provider=self.provider_name,
            model=model,
            request_id=request_id,
            metadata=request.metadata,
        )

        messages: list[dict[str, Any]] = []
        if request.system_prompt:
            messages.append({"role": "system", "content": request.system_prompt})
        messages.append({"role": "user", "content": request.prompt})

        started = time.perf_counter()
        resp = await self._client.chat.completions.create(
            model=model,
            messages=messages,
            max_tokens=request.max_tokens,
        )
        latency_ms = int((time.perf_counter() - started) * 1000)

        usage = TokenUsage(
            input_tokens=getattr(resp.usage, "prompt_tokens", 0) or 0,
            output_tokens=getattr(resp.usage, "completion_tokens", 0) or 0,
            total_tokens=getattr(resp.usage, "total_tokens", 0) or 0,
        )
        provider_request_id = getattr(resp, "id", None)
        response = ProviderResponse(
            role=request.role,
            provider_name=self.provider_name,
            model_used=model,
            content=resp.choices[0].message.content or "",
            token_usage=usage,
            latency_ms=latency_ms,
            request_id=provider_request_id or request_id,
            metadata={"finish_reason": getattr(resp.choices[0], "finish_reason", None)},
        )

        log_provider_response(
            logger,
            role=request.role,
            provider=self.provider_name,
            model=model,
            request_id=response.request_id,
            latency_ms=response.latency_ms,
            token_usage=response.token_usage.model_dump(),
            is_mock=False,
        )
        return response
