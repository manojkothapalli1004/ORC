"""Local JSON registry for Claude/Antigravity work sessions."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from backend.config import settings
from backend.models.session import (
    GenerateSessionHandoffRequest,
    SessionCompressionMode,
    SessionDetailView,
    SessionHandoffSummary,
    SessionHandoffView,
    SessionRegistryView,
    SessionRestartPrompt,
    SessionStatus,
    SessionSummaryItem,
    WorkSession,
)
from backend.services.summarizer import SessionSummarizer


class SessionStore:
    def __init__(self, root_dir: Path | None = None) -> None:
        self._dir = root_dir or settings.session_dir
        self._dir.mkdir(parents=True, exist_ok=True)

    def _path(self, session_id: str) -> Path:
        return self._dir / f"{session_id}.json"

    def list_sessions(self) -> list[WorkSession]:
        sessions: list[WorkSession] = []
        for path in sorted(self._dir.glob("*.json")):
            sessions.append(WorkSession.model_validate_json(path.read_text()))
        return sessions

    def load(self, session_id: str) -> WorkSession | None:
        path = self._path(session_id)
        if not path.exists():
            return None
        return WorkSession.model_validate_json(path.read_text())

    def save(self, session: WorkSession) -> WorkSession:
        now = datetime.now(timezone.utc)
        session.updated_at = now
        session.last_activity_at = now
        tmp = self._path(session.session_id).with_suffix('.tmp')
        tmp.write_text(session.model_dump_json(indent=2))
        tmp.rename(self._path(session.session_id))
        return session

    def create(self, session: WorkSession) -> WorkSession:
        existing = self.load(session.session_id)
        if existing is not None:
            return existing
        return self.save(session)

    def detail_view(self, session_id: str) -> SessionDetailView | None:
        session = self.load(session_id)
        if session is None:
            return None
        return SessionDetailView(
            session_id=session.session_id,
            role=session.role,
            assigned_job_id=session.assigned_job_id,
            assigned_at=session.assignment.assigned_at,
            status=session.status,
            last_activity_at=session.last_activity_at,
            last_result_summary=session.last_result_summary,
            next_expected_action=session.next_expected_action,
            lifecycle=session.lifecycle,
            compressed=session.compressed,
            resumable=session.resumable,
            compression_mode=session.compression_mode,
            metadata=session.metadata,
            updated_at=session.updated_at,
        )

    def build_registry_view(self) -> SessionRegistryView:
        sessions = sorted(self.list_sessions(), key=lambda item: item.last_activity_at, reverse=True)
        return SessionRegistryView(
            total=len(sessions),
            idle=sum(1 for item in sessions if item.status == SessionStatus.IDLE),
            assigned=sum(1 for item in sessions if item.status == SessionStatus.ASSIGNED),
            waiting_for_prompt_delivery=sum(1 for item in sessions if item.status == SessionStatus.WAITING_FOR_PROMPT_DELIVERY),
            running=sum(1 for item in sessions if item.status == SessionStatus.RUNNING),
            waiting_for_result=sum(1 for item in sessions if item.status == SessionStatus.WAITING_FOR_RESULT),
            completed=sum(1 for item in sessions if item.status == SessionStatus.COMPLETED),
            failed=sum(1 for item in sessions if item.status == SessionStatus.FAILED),
            blocked=sum(1 for item in sessions if item.status == SessionStatus.BLOCKED),
            compressed=sum(1 for item in sessions if item.compressed),
            resumable=sum(1 for item in sessions if item.resumable),
            sessions=[
                SessionSummaryItem(
                    session_id=item.session_id,
                    role=item.role,
                    assigned_job_id=item.assigned_job_id,
                    status=item.status,
                    last_activity_at=item.last_activity_at,
                    last_result_summary=item.last_result_summary,
                    next_expected_action=item.next_expected_action,
                    compressed=item.compressed,
                    resumable=item.resumable,
                    compression_mode=item.compression_mode,
                    assigned_at=item.assignment.assigned_at,
                    lifecycle_count=len(item.lifecycle),
                )
                for item in sessions
            ],
        )

    # ------------------------------------------------------------------
    # Handoff / compression
    # ------------------------------------------------------------------

    def generate_handoff(
        self,
        session_id: str,
        request: GenerateSessionHandoffRequest | None = None,
    ) -> SessionHandoffView | None:
        session = self.load(session_id)
        if session is None:
            return None
        summarizer = SessionSummarizer()
        session = summarizer.compress_session(session, request)
        self.save(session)
        return SessionHandoffView(
            session_id=session.session_id,
            resumable=session.resumable,
            summary=session.handoff_summary,
            restart_prompt=session.restart_prompt,
            updated_at=session.updated_at,
        )

    def mark_compressed(self, session_id: str) -> WorkSession | None:
        session = self.load(session_id)
        if session is None:
            return None
        session.compressed = True
        session.resumable = True
        return self.save(session)

    def list_resumable(self) -> list[SessionHandoffView]:
        results: list[SessionHandoffView] = []
        for session in self.list_sessions():
            if session.resumable:
                results.append(
                    SessionHandoffView(
                        session_id=session.session_id,
                        resumable=True,
                        summary=session.handoff_summary,
                        restart_prompt=session.restart_prompt,
                        updated_at=session.updated_at,
                    )
                )
        return results
