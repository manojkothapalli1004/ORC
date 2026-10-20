"""Hermetic tests for the provider factory, router provider and registry.

These tests never need network access or real API keys. Two things make them
hermetic regardless of what is exported in the developer's shell:

* ``backend/providers/openai_provider.py`` and ``backend/providers/router_provider.py``
  fall back to the *module-level* ``backend.config.settings`` (built at import
  time), so a fresh ``Settings`` passed to the factory is not enough. The
  ``keyless`` fixture therefore also blanks the relevant attributes on
  ``backend.config.settings`` itself.
* ``RouterProvider.complete`` and ``ProviderRegistry._instantiate`` emit JSON
  lines to ``logs/providers.log``; the fixture swaps those hooks for no-ops so
  nothing is written outside the pytest sandbox.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

import backend.config as config_module
from backend.config import Settings
from backend.models.core import ProviderRole
from backend.providers import registry as registry_module
from backend.providers import router_provider as router_module
from backend.providers.base import ProviderRequest, TokenUsage
from backend.providers.factory import ProviderFactory
from backend.providers.mock_provider import MockProvider
from backend.providers.registry import ProviderRegistry
from backend.providers.router_provider import RouterProvider

_KEY_ENV_VARS = (
    "OPENAI_API_KEY",
    "ANTHROPIC_API_KEY",
    "GEMINI_API_KEY",
    "ROUTER_API_KEY",
    "ROUTER_BASE_URL",
)
_BLANKED_SETTINGS_ATTRS = (
    "openai_api_key",
    "anthropic_api_key",
    "gemini_api_key",
    "router_api_key",
    "router_base_url",
)
_DIRECT_PROVIDERS = ("openai", "anthropic", "gemini")


def _noop(*args, **kwargs) -> None:
    return None


class _NullLogger:
    """Stands in for module loggers so nothing reaches logs/providers.log."""

    def info(self, *args, **kwargs) -> None:
        return None

    def warning(self, *args, **kwargs) -> None:
        return None

    def debug(self, *args, **kwargs) -> None:
        return None

    def error(self, *args, **kwargs) -> None:
        return None


def _patch_module_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    """Blank the import-time ``backend.config.settings`` and silence log hooks.

    This is the part the rejected run was missing: providers that receive an
    empty key fall back to the module-level settings object, so it must be
    blanked too. Deliberately does NOT touch ``os.environ`` so the hermeticity
    regression test can exercise it with keys still exported.
    """
    for attr in _BLANKED_SETTINGS_ATTRS:
        monkeypatch.setattr(config_module.settings, attr, "")
    monkeypatch.setattr(router_module, "log_provider_request", _noop)
    monkeypatch.setattr(router_module, "log_provider_response", _noop)
    monkeypatch.setattr(router_module, "logger", _NullLogger())
    monkeypatch.setattr(registry_module, "logger", _NullLogger())


def _fresh_keyless_settings() -> Settings:
    """Build a Settings object with no keys and a fixed direct role mapping.

    Explicit init kwargs take priority over environment variables and the
    ``.env`` file is disabled, so this is independent of the host shell.
    """
    return Settings(
        _env_file=None,
        openai_api_key="",
        anthropic_api_key="",
        gemini_api_key="",
        router_api_key="",
        router_base_url="",
        router_provider_name="router",
        reviewer_provider="openai",
        reviewer_mode="direct",
        reviewer_model="gpt-4o",
        planner_provider="openai",
        planner_mode="direct",
        planner_model="gpt-4o",
        builder_provider="anthropic",
        builder_mode="direct",
        builder_model="claude-sonnet-4-20250514",
    )


@pytest.fixture
def keyless(monkeypatch: pytest.MonkeyPatch) -> Settings:
    for name in _KEY_ENV_VARS:
        monkeypatch.delenv(name, raising=False)
    _patch_module_settings(monkeypatch)
    return _fresh_keyless_settings()


def _role(
    provider: str,
    *,
    mode: str = "direct",
    model: str = "test-model",
    base_url: str = "",
    role: str = "reviewer",
) -> ProviderRole:
    return ProviderRole(role=role, provider=provider, mode=mode, model=model, base_url=base_url)


class _FakeCompletions:
    def __init__(self, result=None, error: BaseException | None = None) -> None:
        self.calls: list[dict] = []
        self._result = result
        self._error = error

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        if self._error is not None:
            raise self._error
        return self._result


class _FakeClient:
    def __init__(self, completions: _FakeCompletions) -> None:
        self.chat = SimpleNamespace(completions=completions)


def _canned_response(*, with_id: bool = True):
    resp = SimpleNamespace(
        usage=SimpleNamespace(prompt_tokens=11, completion_tokens=7, total_tokens=18),
        choices=[SimpleNamespace(message=SimpleNamespace(content="hello from router"), finish_reason="stop")],
    )
    if with_id:
        resp.id = "resp-123"
    return resp


# --------------------------------------------------------------------------- factory


@pytest.mark.parametrize("provider", _DIRECT_PROVIDERS)
def test_direct_provider_without_key_falls_back_to_mock(keyless: Settings, provider: str) -> None:
    factory = ProviderFactory(keyless)

    instance = factory.build(_role(provider, model="m-1"))

    assert isinstance(instance, MockProvider)
    assert instance.provider_name == f"mock:{provider}"
    assert instance.is_available is True
    assert factory.last_unavailability_reason, "factory must explain why it fell back to mock"


def test_router_mode_with_base_url_selects_router_provider(keyless: Settings) -> None:
    factory = ProviderFactory(keyless)

    instance = factory.build(_role("openai", mode="router", model="r-1", base_url="http://127.0.0.1:1/"))

    assert isinstance(instance, RouterProvider)
    assert instance.is_available is True
    assert instance.provider_name == keyless.router_provider_name == "router"
    assert instance._base_url == "http://127.0.0.1:1"  # trailing slash stripped
    assert instance._api_key == "router-local"  # placeholder when no key configured
    assert instance._default_model == "r-1"
    assert factory.last_unavailability_reason == ""


def test_router_mode_without_base_url_falls_back_to_mock(keyless: Settings) -> None:
    factory = ProviderFactory(keyless)

    instance = factory.build(_role("openai", mode="router", model="r-2"))

    assert isinstance(instance, MockProvider)
    assert instance.provider_name == f"mock:{keyless.router_provider_name}"
    assert instance.is_available is True
    # The mock keeps the configured model so callers can still see what was asked for.
    assert instance._model == "r-2"


def test_router_provider_name_in_direct_mode_uses_router_path(keyless: Settings) -> None:
    factory = ProviderFactory(keyless)

    without_url = factory.build(_role("router", model="r-3"))
    with_url = factory.build(_role("router", model="r-3", base_url="http://127.0.0.1:1"))

    assert isinstance(without_url, MockProvider)
    assert without_url.provider_name == "mock:router"
    assert isinstance(with_url, RouterProvider)
    assert with_url._base_url == "http://127.0.0.1:1"


def test_unknown_provider_name_reports_exact_reason_and_resets_on_next_build(keyless: Settings) -> None:
    factory = ProviderFactory(keyless)

    instance = factory.build(_role("nonexistent", model="m-x"))

    assert isinstance(instance, MockProvider)
    assert instance.provider_name == "mock:nonexistent"
    assert factory.last_unavailability_reason == "unknown provider: nonexistent"

    # A subsequent successful build clears the stale reason.
    ok = factory.build(_role("openai", mode="router", model="m-y", base_url="http://127.0.0.1:1"))
    assert isinstance(ok, RouterProvider)
    assert factory.last_unavailability_reason == ""


def test_factory_with_no_keys_routes_every_role_to_mock(keyless: Settings) -> None:
    factory = ProviderFactory(keyless)
    roles = [
        _role(keyless.reviewer_provider, mode=keyless.reviewer_mode, model=keyless.reviewer_model, role="reviewer"),
        _role(keyless.planner_provider, mode=keyless.planner_mode, model=keyless.planner_model, role="planner"),
        _role(keyless.builder_provider, mode=keyless.builder_mode, model=keyless.builder_model, role="builder"),
    ]

    built = {cfg.role: factory.build(cfg) for cfg in roles}

    assert set(built) == {"reviewer", "planner", "builder"}
    for cfg in roles:
        instance = built[cfg.role]
        assert isinstance(instance, MockProvider), cfg.role
        assert instance.provider_name == f"mock:{cfg.provider}"
        assert instance.provider_name.startswith("mock:")


# --------------------------------------------------------------------------- router provider


def test_unavailable_router_complete_raises(keyless: Settings) -> None:
    provider = RouterProvider(base_url="", api_key="", default_model="r-0")

    assert provider.is_available is False
    assert provider._client is None
    with pytest.raises(RuntimeError, match="missing router base URL"):
        asyncio.run(provider.complete(ProviderRequest(role="builder", prompt="hi")))


def test_router_complete_maps_messages_and_usage(keyless: Settings) -> None:
    provider = RouterProvider(base_url="http://127.0.0.1:1/", api_key="", default_model="default-model")
    completions = _FakeCompletions(result=_canned_response())
    provider._client = _FakeClient(completions)

    request = ProviderRequest(
        role="builder",
        prompt="do the thing",
        system_prompt="be terse",
        model="override-model",
        max_tokens=321,
        metadata={"request_id": "req-1", "job": "j-1"},
    )
    response = asyncio.run(provider.complete(request))

    assert len(completions.calls) == 1
    assert completions.calls[0] == {
        "model": "override-model",
        "messages": [
            {"role": "system", "content": "be terse"},
            {"role": "user", "content": "do the thing"},
        ],
        "max_tokens": 321,
    }
    assert response.role == "builder"
    assert response.provider_name == "router"
    assert response.model_used == "override-model"
    assert response.content == "hello from router"
    assert response.token_usage == TokenUsage(input_tokens=11, output_tokens=7, total_tokens=18)
    assert response.request_id == "resp-123"
    assert response.is_mock is False
    assert response.latency_ms >= 0
    assert response.metadata == {"base_url": "http://127.0.0.1:1", "finish_reason": "stop"}


def test_router_complete_without_system_prompt_uses_default_model_and_metadata_request_id(
    keyless: Settings,
) -> None:
    provider = RouterProvider(base_url="http://127.0.0.1:1", api_key="", default_model="default-model")
    completions = _FakeCompletions(result=_canned_response(with_id=False))
    provider._client = _FakeClient(completions)

    request = ProviderRequest(role="planner", prompt="plan it", metadata={"request_id": "req-9"})
    response = asyncio.run(provider.complete(request))

    assert len(completions.calls) == 1
    assert completions.calls[0]["model"] == "default-model"
    assert completions.calls[0]["messages"] == [{"role": "user", "content": "plan it"}]
    assert completions.calls[0]["max_tokens"] == 4096
    assert response.model_used == "default-model"
    assert response.request_id == "req-9"


def test_router_complete_propagates_client_errors(keyless: Settings) -> None:
    provider = RouterProvider(base_url="http://127.0.0.1:1", api_key="", default_model="r-1")
    completions = _FakeCompletions(error=RuntimeError("router exploded"))
    provider._client = _FakeClient(completions)

    with pytest.raises(RuntimeError, match="router exploded"):
        asyncio.run(provider.complete(ProviderRequest(role="reviewer", prompt="x")))

    assert len(completions.calls) == 1


# --------------------------------------------------------------------------- registry


def test_registry_resolves_every_role_to_mock_when_keyless(keyless: Settings) -> None:
    registry = ProviderRegistry(keyless)

    mapping = registry.role_map()
    assert set(mapping) == {"reviewer", "planner", "builder"}

    everything = registry.get_all_roles()
    assert set(everything) == {"reviewer", "planner", "builder"}
    for role, cfg in mapping.items():
        instance = everything[role]
        assert isinstance(instance, MockProvider), role
        assert instance.provider_name == f"mock:{cfg.provider}"

        first = registry.get_provider(role)
        second = registry.get_provider(role)
        # The registry builds through the factory each time; what must be
        # stable is the resolved provider identity, not object identity.
        assert isinstance(first, MockProvider) and isinstance(second, MockProvider)
        assert first.provider_name == second.provider_name == instance.provider_name
        assert first._model == second._model == cfg.model

    with pytest.raises(KeyError, match="No provider configured for role: verifier"):
        registry.get_provider("verifier")


def test_registry_summary_marks_keyless_roles_as_not_live(keyless: Settings) -> None:
    registry = ProviderRegistry(keyless)

    summary = registry.summary()

    assert [item["role"] for item in summary] == ["reviewer", "planner", "builder"]
    for item in summary:
        assert item["is_live"] is False
        assert item["is_available"] is True
        assert item["resolved_provider"] == f"mock:{item['configured_provider']}"
        assert item["configured_mode"] == "direct"
        assert item["configured_base_url"] == ""


def test_registry_honours_explicit_router_roles(keyless: Settings) -> None:
    registry = ProviderRegistry(keyless)
    roles = [
        _role("openai", mode="router", model="r-a", base_url="http://127.0.0.1:1", role="reviewer"),
        _role("openai", mode="router", model="r-b", role="builder"),
    ]

    resolved = registry.get_all_roles(roles)

    assert isinstance(resolved["reviewer"], RouterProvider)
    assert resolved["reviewer"]._default_model == "r-a"
    assert isinstance(resolved["builder"], MockProvider)
    assert resolved["builder"].provider_name == "mock:router"


# --------------------------------------------------------------------------- hermeticity regression


def test_hermetic_under_exported_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    """Regression for the rejected run: exported keys must not leak into tests.

    Keys are exported *before* the settings patches are applied, and the env
    vars are intentionally left in place. Everything must still resolve to
    mock because the module-level settings object is blanked and the fresh
    Settings uses explicit empty kwargs.
    """
    monkeypatch.setenv("OPENAI_API_KEY", "dummy-not-real")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "dummy-not-real")
    monkeypatch.setenv("GEMINI_API_KEY", "dummy-not-real")
    monkeypatch.setenv("ROUTER_API_KEY", "dummy-not-real")
    monkeypatch.setenv("ROUTER_BASE_URL", "http://127.0.0.1:1")

    _patch_module_settings(monkeypatch)
    app_settings = _fresh_keyless_settings()

    # Both settings objects are blank despite the exported env.
    for attr in _BLANKED_SETTINGS_ATTRS:
        assert getattr(config_module.settings, attr) == "", attr
        assert getattr(app_settings, attr) == "", attr
    assert router_module.log_provider_request is _noop
    assert router_module.log_provider_response is _noop

    factory = ProviderFactory(app_settings)

    # direct provider falls back to mock
    for provider in _DIRECT_PROVIDERS:
        instance = factory.build(_role(provider, model="m"))
        assert isinstance(instance, MockProvider), provider
        assert instance.provider_name == f"mock:{provider}"

    # router mode without an explicit base url does not pick up ROUTER_BASE_URL
    router_mock = factory.build(_role("openai", mode="router", model="m"))
    assert isinstance(router_mock, MockProvider)
    assert router_mock.provider_name == "mock:router"

    # router mode with explicit base url still selects the router with a placeholder key
    router_live = factory.build(_role("openai", mode="router", model="m", base_url="http://127.0.0.1:1/"))
    assert isinstance(router_live, RouterProvider)
    assert router_live._api_key == "router-local"

    # bare RouterProvider() falls back to the (now blank) module settings
    bare = RouterProvider()
    assert bare.is_available is False
    with pytest.raises(RuntimeError, match="missing router base URL"):
        asyncio.run(bare.complete(ProviderRequest(role="builder", prompt="hi")))

    # every registry role routes to mock
    registry = ProviderRegistry(app_settings)
    for role, instance in registry.get_all_roles().items():
        assert isinstance(instance, MockProvider), role
