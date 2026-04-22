"""Gemini direct provider implementation."""

from __future__ import annotations

import time

try:
    from google import genai
except ImportError:  # pragma: no cover
    genai = None

from backend.config import settings
from backend.logging_hooks import get_orchestrator_logger, log_provider_request, log_provider_response
from .base import ProviderRequest, ProviderResponse, TokenUsage

logger = get_orchestrator_logger(__name__)


class GeminiProvider:
    def __init__(self, api_key: str | None = None, default_model: str = "gemini-2.5-pro") -> None:
        self._api_key = api_key or settings.gemini_api_key
        self._default_model = default_model
        self._client = genai.Client(api_key=self._api_key) if (self._api_key and genai is not None) else None

    @property
    def provider_name(self) -> str:
        return "gemini"

    @property
    def is_available(self) -> bool:
        return bool(self._api_key and self._client)

    async def complete(self, request: ProviderRequest) -> ProviderResponse:
        if not self._client:
            raise RuntimeError("Gemini provider is unavailable: missing API key")

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

        prompt = request.prompt if not request.system_prompt else f"{request.system_prompt}\n\n{request.prompt}"
        started = time.perf_counter()
        resp = await self._client.aio.models.generate_content(model=model, contents=prompt)
        latency_ms = int((time.perf_counter() - started) * 1000)
        usage_meta = getattr(resp, "usage_metadata", None)
        usage = TokenUsage(
            input_tokens=getattr(usage_meta, "prompt_token_count", 0) or 0,
            output_tokens=getattr(usage_meta, "candidates_token_count", 0) or 0,
            total_tokens=getattr(usage_meta, "total_token_count", 0) or 0,
        )
        response = ProviderResponse(
            role=request.role,
            provider_name=self.provider_name,
            model_used=model,
            content=getattr(resp, "text", "") or "",
            token_usage=usage,
            latency_ms=latency_ms,
            request_id=request_id,
            metadata={},
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
