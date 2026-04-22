"""Anthropic provider implementation."""

from __future__ import annotations

import time

_ANTHROPIC_IMPORT_ERROR: str | None = None
try:
    from anthropic import AsyncAnthropic
except ImportError as _exc:
    AsyncAnthropic = None
    _ANTHROPIC_IMPORT_ERROR = str(_exc)

from backend.config import settings
from backend.logging_hooks import get_orchestrator_logger, log_provider_request, log_provider_response
from .base import ProviderRequest, ProviderResponse, TokenUsage

logger = get_orchestrator_logger(__name__)

if _ANTHROPIC_IMPORT_ERROR:
    logger.warning("anthropic SDK not installed: %s", _ANTHROPIC_IMPORT_ERROR)


class AnthropicProvider:
    def __init__(self, api_key: str | None = None, default_model: str = "claude-sonnet-4-20250514") -> None:
        self._api_key = api_key or settings.anthropic_api_key
        self._default_model = default_model
        self._client = AsyncAnthropic(api_key=self._api_key) if (self._api_key and AsyncAnthropic is not None) else None

    @property
    def provider_name(self) -> str:
        return "anthropic"

    @property
    def is_available(self) -> bool:
        return bool(self._api_key and self._client)

    @property
    def unavailability_reason(self) -> str:
        if self.is_available:
            return ""
        if AsyncAnthropic is None:
            return f"anthropic SDK not installed ({_ANTHROPIC_IMPORT_ERROR})"
        if not self._api_key:
            return "ANTHROPIC_API_KEY not set"
        return "unknown"

    async def complete(self, request: ProviderRequest) -> ProviderResponse:
        if not self._client:
            raise RuntimeError("Anthropic provider is unavailable: missing API key")

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

        kwargs: dict = {
            "model": model,
            "max_tokens": request.max_tokens,
            "messages": [{"role": "user", "content": request.prompt}],
        }
        if request.system_prompt:
            kwargs["system"] = request.system_prompt

        started = time.perf_counter()
        resp = await self._client.messages.create(**kwargs)
        latency_ms = int((time.perf_counter() - started) * 1000)

        input_tokens = getattr(resp.usage, "input_tokens", 0) or 0
        output_tokens = getattr(resp.usage, "output_tokens", 0) or 0
        usage = TokenUsage(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            total_tokens=input_tokens + output_tokens,
        )
        text_parts = [block.text for block in resp.content if getattr(block, "type", None) == "text"]
        response = ProviderResponse(
            role=request.role,
            provider_name=self.provider_name,
            model_used=model,
            content="".join(text_parts),
            token_usage=usage,
            latency_ms=latency_ms,
            request_id=getattr(resp, "id", None) or request_id,
            metadata={"stop_reason": getattr(resp, "stop_reason", None)},
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
