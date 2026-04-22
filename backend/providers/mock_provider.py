"""Mock-safe provider used when API keys are missing or for dry runs."""

from __future__ import annotations

import uuid

from .base import ProviderRequest, ProviderResponse


class MockProvider:
    def __init__(self, provider_name: str = "mock", model: str = "mock-v1") -> None:
        self._provider_name = provider_name
        self._model = model

    @property
    def provider_name(self) -> str:
        return self._provider_name

    @property
    def is_available(self) -> bool:
        return True

    async def complete(self, request: ProviderRequest) -> ProviderResponse:
        return ProviderResponse(
            role=request.role,
            provider_name=self.provider_name,
            model_used=request.model or self._model,
            content=(
                f"Mock response for role={request.role}. "
                "No live provider key is configured, so no external API call was made."
            ),
            request_id=f"mock-{uuid.uuid4().hex[:8]}",
            metadata={"reason": "missing_api_key_or_mock_mode"},
            is_mock=True,
        )
