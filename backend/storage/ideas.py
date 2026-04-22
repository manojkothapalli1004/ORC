"""Local persistence and mock-safe helpers for idea threads."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from pathlib import Path

from backend.config import settings
from backend.models.idea import (
    DiscussionMessage,
    IdeaMessageRole,
    IdeaSummary,
    IdeaThread,
    IdeaThreadStatus,
    ProposalDraft,
)


class IdeaStore:
    def __init__(self, root_dir: Path | None = None) -> None:
        self._dir = root_dir or settings.idea_thread_dir
        self._dir.mkdir(parents=True, exist_ok=True)

    def _path(self, idea_id: str) -> Path:
        return self._dir / f"{idea_id}.json"

    def create(self, *, title: str, initial_note: str = "") -> IdeaThread:
        thread = IdeaThread(id=uuid.uuid4().hex[:12], title=title)
        if initial_note:
            thread.messages.append(
                DiscussionMessage(
                    id=uuid.uuid4().hex[:8],
                    role=IdeaMessageRole.USER,
                    body=initial_note,
                )
            )
            thread.status = IdeaThreadStatus.DISCUSSING
        self.save(thread)
        return thread

    def save(self, thread: IdeaThread) -> IdeaThread:
        thread.updated_at = datetime.now(timezone.utc)
        tmp = self._path(thread.id).with_suffix('.tmp')
        tmp.write_text(thread.model_dump_json(indent=2))
        tmp.rename(self._path(thread.id))
        return thread

    def load(self, idea_id: str) -> IdeaThread | None:
        path = self._path(idea_id)
        if not path.exists():
            return None
        return IdeaThread.model_validate_json(path.read_text())

    def list(self) -> list[IdeaThread]:
        return [IdeaThread.model_validate_json(path.read_text()) for path in sorted(self._dir.glob('*.json'))]

    def add_message(self, idea_id: str, *, body: str, role: IdeaMessageRole) -> IdeaThread:
        thread = self.load(idea_id)
        if thread is None:
            raise FileNotFoundError(idea_id)
        thread.messages.append(DiscussionMessage(id=uuid.uuid4().hex[:8], role=role, body=body))
        if thread.status == IdeaThreadStatus.DRAFT:
            thread.status = IdeaThreadStatus.DISCUSSING
        return self.save(thread)

    def finalize(self, idea_id: str, *, note: str = "") -> IdeaThread:
        thread = self.load(idea_id)
        if thread is None:
            raise FileNotFoundError(idea_id)
        thread.summary = self._mock_summary(thread, note=note)
        thread.status = IdeaThreadStatus.FINALIZED
        return self.save(thread)

    def convert_to_proposal(self, idea_id: str) -> IdeaThread:
        thread = self.load(idea_id)
        if thread is None:
            raise FileNotFoundError(idea_id)
        if thread.summary is None:
            thread.summary = self._mock_summary(thread)
        thread.proposal_draft = self._mock_proposal(thread)
        thread.status = IdeaThreadStatus.CONVERTED_TO_PROPOSAL
        return self.save(thread)

    def _mock_summary(self, thread: IdeaThread, *, note: str = "") -> IdeaSummary:
        messages = [msg.body for msg in thread.messages if msg.body.strip()]
        latest = messages[-1] if messages else thread.title
        constraints = ["local only", "safe and typed", "no live bot control"]
        proposed_scope = ["orchestrator/ui", "orchestrator/backend/api", "orchestrator/backend/storage"]
        return IdeaSummary(
            title=thread.title,
            problem=messages[0] if messages else thread.title,
            desired_outcome=latest,
            constraints=constraints,
            proposed_scope=proposed_scope,
            notes=note or "Mock-safe summary generated from local discussion thread.",
            is_mock=True,
        )

    def _mock_proposal(self, thread: IdeaThread) -> ProposalDraft:
        summary = thread.summary or self._mock_summary(thread)
        return ProposalDraft(
            title=summary.title or thread.title,
            prompt=(
                f"Build the orchestrator idea '{thread.title}' within orchestrator only. "
                f"Desired outcome: {summary.desired_outcome}. "
                f"Constraints: {', '.join(summary.constraints)}."
            ),
            rationale=summary.notes or "Mock-safe proposal draft generated locally.",
            is_mock=True,
        )
