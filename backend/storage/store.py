"""JSON file-based state persistence."""

from __future__ import annotations

import json
import logging
import uuid
from pathlib import Path

from backend.config import settings
from backend.models.core import WorkflowState
from backend.storage.atomic import atomic_write_text
from backend.storage.search_index import get_search_index

logger = logging.getLogger("orchestrator.storage")


def _atomic_write_text(path: Path, text: str) -> None:
    """Write ``text`` to ``path`` atomically (see backend.storage.atomic)."""
    atomic_write_text(path, text)


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
        _atomic_write_text(self._path(state.id), state.model_dump_json(indent=2))
        _index_workflow(state)

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
            try:
                get_search_index().delete("workflow", workflow_id)
            except Exception:
                pass
            return True
        return False


def _index_workflow(state: WorkflowState) -> None:
    parts: list[str] = [state.id, state.status.value, state.current_stage or ""]
    if state.summary:
        parts.append(state.summary.title or "")
        parts.append(state.summary.outcome or "")
        parts.extend(state.summary.files_changed)
    for prop in state.proposals:
        if prop.prompt:
            parts.append(prop.prompt)
        if prop.response:
            parts.append(prop.response)
        if prop.reviewer_notes:
            parts.append(prop.reviewer_notes)
    if state.error:
        parts.append(state.error)
    title = (state.summary.title if state.summary and state.summary.title else f"workflow {state.id}")
    body = "\n".join(p for p in parts if p)
    try:
        get_search_index().upsert("workflow", state.id, title, body, state.updated_at)
    except Exception:
        pass
