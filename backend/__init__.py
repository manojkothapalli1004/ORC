"""Backend package — public API is loaded lazily.

Eagerly importing the worker/provider chain here used to pull in
``google.genai`` at module-import time (~11s), which pushed MCP server
startup past Claude's 30s handshake timeout. We expose the same names
via PEP 562 ``__getattr__`` so anyone doing ``from backend import X``
still works, but the heavy modules only load on first access.
"""

from __future__ import annotations

from typing import Any

_LAZY_EXPORTS = {
    "BuilderExecutionAdapter": "backend.workers",
    "LocalBuilderWorkerRuntime": "backend.workers",
    "ManualSafeAdapter": "backend.workers",
    "MockExecutionAdapter": "backend.workers",
    "ProviderExecutionAdapter": "backend.workers",
    "builder_worker_entrypoint": "backend.workers",
    "run_local_builder_worker": "backend.workers",
}

__all__ = list(_LAZY_EXPORTS)


def __getattr__(name: str) -> Any:
    module_path = _LAZY_EXPORTS.get(name)
    if module_path is None:
        raise AttributeError(f"module 'backend' has no attribute {name!r}")
    import importlib

    module = importlib.import_module(module_path)
    value = getattr(module, name)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(_LAZY_EXPORTS))
