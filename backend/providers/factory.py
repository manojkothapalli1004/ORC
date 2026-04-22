"""Provider factory for direct and router-backed execution."""

from __future__ import annotations

import os

from backend.config import Settings, settings
from backend.models.core import ProviderRole
from .anthropic_provider import AnthropicProvider
from .claude_code_local_provider import ClaudeCodeLocalProvider
from .gemini_provider import GeminiProvider
from .mock_provider import MockProvider
from .openai_provider import OpenAIProvider
from .router_provider import RouterProvider


class ProviderFactory:
    def __init__(self, app_settings: Settings | None = None) -> None:
        self._settings = app_settings or settings
        self.last_unavailability_reason: str = ""

    def build(self, config: ProviderRole):
        self.last_unavailability_reason = ""
        mode = (config.mode or "direct").lower()
        provider = config.provider.lower()

        if mode == "router":
            base_url = config.base_url or self._settings.router_base_url
            return self._router_or_mock(config=config, base_url=base_url)

        if provider == "openai":
            instance = OpenAIProvider(api_key=self._resolve_api_key(config, self._settings.openai_api_key), default_model=config.model)
        elif provider == "anthropic":
            instance = AnthropicProvider(api_key=self._resolve_api_key(config, self._settings.anthropic_api_key), default_model=config.model)
        elif provider == "gemini":
            instance = GeminiProvider(api_key=self._resolve_api_key(config, self._settings.gemini_api_key), default_model=config.model)
        elif provider == "claude_code_local":
            instance = ClaudeCodeLocalProvider(default_model=config.model)
        elif provider == "router":
            instance = self._router_or_mock(config=config, base_url=config.base_url or self._settings.router_base_url)
            return instance
        else:
            self.last_unavailability_reason = f"unknown provider: {config.provider}"
            return MockProvider(provider_name=f"mock:{config.provider}", model=config.model)

        if instance.is_available:
            return instance
        self.last_unavailability_reason = getattr(instance, "unavailability_reason", f"{config.provider} unavailable")
        return MockProvider(provider_name=f"mock:{config.provider}", model=config.model)

    def _router_or_mock(self, *, config: ProviderRole, base_url: str):
        instance = RouterProvider(
            base_url=base_url,
            api_key=self._resolve_api_key(config, self._settings.router_api_key),
            default_model=config.model,
            provider_name=self._settings.router_provider_name,
        )
        if instance.is_available:
            return instance
        return MockProvider(provider_name=f"mock:{self._settings.router_provider_name}", model=config.model)

    def _resolve_api_key(self, config: ProviderRole, fallback: str) -> str:
        if config.api_key_env:
            return os.getenv(config.api_key_env, fallback)
        return fallback
