"""OpenAI-compatible router provider for local backends such as 9Router."""

from __future__ import annotations

import time
from typing import Any

try:
    from openai import AsyncOpenAI
except ImportError:  # pragma: no cover
    AsyncOpenAI = None

from backend.config import settings
from backend.logging_hooks import get_orchestrator_logger, log_provider_request, log_provider_response
from .base import ProviderRequest, ProviderResponse, TokenUsage

logger = get_orchestrator_logger(__name__)


class RouterProvider:
    def __init__(
        self,
        *,
        base_url: str | None = None,
        api_key: str | None = None,
        default_model: str = "",
        provider_name: str | None = None,
    ) -> None:
        self._base_url = (base_url or settings.router_base_url).rstrip("/")
        self._api_key = api_key or settings.router_api_key or "router-local"
        self._default_model = default_model
        self._provider_name = provider_name or settings.router_provider_name
        self._client = (
            AsyncOpenAI(base_url=self._base_url, api_key=self._api_key)
            if (self._base_url and AsyncOpenAI is not None)
            else None
        )

    @property
    def provider_name(self) -> str:
        return self._provider_name

    @property
    def is_available(self) -> bool:
        return bool(self._base_url and self._client)

    async def complete(self, request: ProviderRequest) -> ProviderResponse:
        if not self._client:
            raise RuntimeError("Router provider is unavailable: missing router base URL")

        model = request.model or self._default_model
        request_id = request.metadata.get("request_id") if isinstance(request.metadata, dict) else None
        log_provider_request(
            logger,
            role=request.role,
            provider=self.provider_name,
            model=model,
            request_id=request_id,
            metadata={**request.metadata, "base_url": self._base_url},
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
        response = ProviderResponse(
            role=request.role,
            provider_name=self.provider_name,
            model_used=model,
            content=resp.choices[0].message.content or "",
            token_usage=usage,
            latency_ms=latency_ms,
            request_id=getattr(resp, "id", None) or request_id,
            metadata={"base_url": self._base_url, "finish_reason": getattr(resp.choices[0], "finish_reason", None)},
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
