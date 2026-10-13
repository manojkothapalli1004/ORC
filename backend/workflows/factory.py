"""Shared construction of WorkflowEngine instances.

Both the HTTP API and the local scheduler need to build a workflow engine
for a given workflow state. Centralising the construction here keeps them
identical (same provider resolution, same store wiring).
"""

from __future__ import annotations

from typing import Any

from backend.models.core import WorkflowState
from backend.workflows.engine import WorkflowEngine


_default_registry: Any = None


def _get_default_registry() -> Any:
    global _default_registry
    if _default_registry is None:
        # Imported lazily to avoid heavy provider imports / import cycles at
        # module-import time.
        from backend.providers import ProviderRegistry

        _default_registry = ProviderRegistry()
    return _default_registry


def build_engine(state: WorkflowState, store: Any, registry: Any = None) -> WorkflowEngine:
    """Build a WorkflowEngine for ``state`` exactly like the API does.

    ``registry`` defaults to a process-wide ProviderRegistry; callers that
    already own one (e.g. the API routes module) may pass it explicitly.
    """
    reg = registry if registry is not None else _get_default_registry()
    providers = reg.from_workflow(state)
    return WorkflowEngine(
        reviewer=providers["reviewer"],
        planner=providers.get("planner", providers["reviewer"]),
        builder=providers["builder"],
        store=store,
    )
