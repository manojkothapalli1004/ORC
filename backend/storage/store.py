"""JSON file-based state persistence."""

from __future__ import annotations

import json
import logging
import uuid
from pathlib import Path

from backend.config import settings
from backend.models.core import WorkflowState

logger = logging.getLogger("orchestrator.storage")


class StateStore:
    """Atomic JSON read/write for workflow state."""

    def __init__(self, state_dir: Path | None = None) -> None:
        self._dir = state_dir or settings.state_dir
        self._dir.mkdir(parents=True, exist_ok=True)

    def _path(self, workflow_id: str) -> Path:
        return self._dir / f"{workflow_id}.json"

    def create(self) -> WorkflowState:
        state = WorkflowState(id=uuid.uuid4().hex[:12])
        self.save(state)
        return state

    def load(self, workflow_id: str) -> WorkflowState | None:
        p = self._path(workflow_id)
        if not p.exists():
            return None
        try:
            return WorkflowState.model_validate_json(p.read_text())
        except Exception as exc:
            logger.warning("skipping invalid state file %s: %s", workflow_id, exc)
            return None

    def save(self, state: WorkflowState) -> None:
        from datetime import datetime, timezone
        state.updated_at = datetime.now(timezone.utc)
        tmp = self._path(state.id).with_suffix(".tmp")
        tmp.write_text(state.model_dump_json(indent=2))
        tmp.rename(self._path(state.id))

    def list_ids(self) -> list[str]:
        # Workflow IDs are 12-char hex (see `create()`); filter out co-located
        # non-workflow JSON files in the same dir (project_memory.json, assistant_threads.json).
        def _is_workflow_id(stem: str) -> bool:
            return len(stem) == 12 and all(c in "0123456789abcdef" for c in stem)
        return sorted(
            p.stem for p in self._dir.glob("*.json") if _is_workflow_id(p.stem)
        )

    def delete(self, workflow_id: str) -> bool:
        p = self._path(workflow_id)
        if p.exists():
            p.unlink()
            return True
        return False
