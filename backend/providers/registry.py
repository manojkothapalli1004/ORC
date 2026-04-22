"""Provider registry and role-to-provider resolution."""

from __future__ import annotations

from collections.abc import Iterable

from backend.config import Settings, settings
from backend.logging_hooks import get_orchestrator_logger
from backend.models.core import ProviderRole, WorkflowState
from backend.workflows.modes import WorkflowModeResolver
from .factory import ProviderFactory
from .mock_provider import MockProvider

logger = get_orchestrator_logger(__name__)


class ProviderRegistry:
    def __init__(self, app_settings: Settings | None = None) -> None:
        self._settings = app_settings or settings
        self._factory = ProviderFactory(self._settings)
        self._mode_resolver = WorkflowModeResolver(self._settings)

    def role_map(self, provider_roles: Iterable[ProviderRole] | None = None) -> dict[str, ProviderRole]:
        return self._role_map(provider_roles)

    def get_provider(self, role: str, provider_roles: Iterable[ProviderRole] | None = None):
        mapping = self._role_map(provider_roles)
        config = mapping.get(role)
        if config is None:
            raise KeyError(f"No provider configured for role: {role}")
        return self._instantiate(config)

    def get_all_roles(self, provider_roles: Iterable[ProviderRole] | None = None) -> dict[str, object]:
        mapping = self._role_map(provider_roles)
        return {role: self._instantiate(config) for role, config in mapping.items()}

    def from_workflow(self, state: WorkflowState) -> dict[str, object]:
        provider_roles = state.providers or self._mode_resolver.provider_roles_for(
            state.resolved_policy,
            overrides=state.mode_overrides,
        )
        return self.get_all_roles(provider_roles)

    def summary(self, provider_roles: Iterable[ProviderRole] | None = None) -> list[dict[str, object]]:
        mapping = self._role_map(provider_roles)
        items: list[dict[str, object]] = []
        for role, config in mapping.items():
            instance = self._instantiate(config)
            items.append(
                {
                    "role": role,
                    "configured_provider": config.provider,
                    "configured_mode": config.mode,
                    "configured_model": config.model,
                    "configured_base_url": config.base_url,
                    "resolved_provider": instance.provider_name,
                    "is_live": not instance.provider_name.startswith("mock:"),
                    "is_available": instance.is_available,
                }
            )
        return items

    def _role_map(self, provider_roles: Iterable[ProviderRole] | None) -> dict[str, ProviderRole]:
        if provider_roles is not None:
            return {item.role: item for item in provider_roles}
        return {
            "reviewer": ProviderRole(
                role="reviewer",
                provider=self._settings.reviewer_provider,
                mode=self._settings.reviewer_mode,
                model=self._settings.reviewer_model,
                base_url=self._settings.router_base_url if self._settings.reviewer_mode == "router" else "",
            ),
            "planner": ProviderRole(
                role="planner",
                provider=self._settings.planner_provider,
                mode=self._settings.planner_mode,
                model=self._settings.planner_model,
                base_url=self._settings.router_base_url if self._settings.planner_mode == "router" else "",
            ),
            "builder": ProviderRole(
                role="builder",
                provider=self._settings.builder_provider,
                mode=self._settings.builder_mode,
                model=self._settings.builder_model,
                base_url=self._settings.router_base_url if self._settings.builder_mode == "router" else "",
            ),
        }

    def _instantiate(self, config: ProviderRole):
        instance = self._factory.build(config)
        if not isinstance(instance, MockProvider) or instance.is_available:
            return instance

        logger.info(
            "missing_provider_fallback",
            extra={
                "event": {
                    "role": config.role,
                    "provider": config.provider,
                    "mode": config.mode,
                    "model": config.model,
                    "base_url": config.base_url,
                }
            },
        )
        return instance
