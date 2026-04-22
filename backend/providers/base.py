"""Structured provider interfaces and request/response models."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Protocol

from pydantic import BaseModel, Field


def _now() -> datetime:
    return datetime.now(timezone.utc)


class TokenUsage(BaseModel):
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0


class ProviderRequest(BaseModel):
    role: str = Field(description="Workflow role: reviewer, planner, or builder")
    prompt: str = Field(description="Primary user/task prompt")
    system_prompt: str = Field(default="", description="Optional system instruction")
    model: str | None = Field(default=None, description="Optional model override")
    max_tokens: int = Field(default=4096, ge=1)
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=_now)


class ProviderResponse(BaseModel):
    role: str
    provider_name: str
    model_used: str
    content: str
    token_usage: TokenUsage = Field(default_factory=TokenUsage)
    latency_ms: int = 0
    request_id: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    is_mock: bool = False
    created_at: datetime = Field(default_factory=_now)


class OrchestratorProvider(Protocol):
    @property
    def provider_name(self) -> str:
        ...

    @property
    def is_available(self) -> bool:
        ...

    async def complete(self, request: ProviderRequest) -> ProviderResponse:
        ...


class LLMProvider(Protocol):
    @property
    def provider_name(self) -> str:
        ...

    @property
    def is_available(self) -> bool:
        ...

    async def complete(self, request: ProviderRequest) -> ProviderResponse:
        ...
