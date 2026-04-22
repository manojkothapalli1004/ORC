"""Session summarizer — deterministic handoff and restart-prompt generation."""

from __future__ import annotations

from backend.models.session import (
    GenerateSessionHandoffRequest,
    SessionCompressionMode,
    SessionHandoffSummary,
    SessionRestartPrompt,
    WorkSession,
)

# Per-mode caps on lifecycle events kept after compression.
_LIFECYCLE_LIMITS: dict[SessionCompressionMode, int | None] = {
    SessionCompressionMode.COMPACT: 3,
    SessionCompressionMode.NORMAL: 10,
    SessionCompressionMode.RICH: None,  # keep all
}


class SessionSummarizer:
    """Builds handoff summaries and restart prompts from session state."""

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def generate_handoff(
        self,
        session: WorkSession,
        request: GenerateSessionHandoffRequest | None = None,
    ) -> SessionHandoffSummary:
        """Build a ``SessionHandoffSummary`` from session + optional overrides."""
        req = request or GenerateSessionHandoffRequest()
        mode = req.mode

        current_stage = req.current_stage or _infer_stage(session)
        last_completed = req.last_completed_task or session.last_result_summary
        blockers = list(req.known_blockers) if req.known_blockers else _infer_blockers(session)
        next_step = req.next_safe_step or session.next_expected_action
        key_files = list(req.key_files) if req.key_files else []
        context_notes = list(req.context_notes) if req.context_notes else []

        # Mode-aware trimming
        if mode == SessionCompressionMode.COMPACT:
            context_notes = context_notes[:1]
            key_files = key_files[:3]
        elif mode == SessionCompressionMode.NORMAL:
            context_notes = context_notes[:5]
            key_files = key_files[:10]
        # RICH: keep everything

        return SessionHandoffSummary(
            current_stage=current_stage,
            last_completed_task=last_completed,
            known_blockers=blockers,
            next_safe_step=next_step,
            key_files=key_files,
            context_notes=context_notes,
            mode=mode,
        )

    def generate_restart_prompt(
        self,
        session: WorkSession,
        summary: SessionHandoffSummary,
        mode: SessionCompressionMode = SessionCompressionMode.NORMAL,
    ) -> SessionRestartPrompt:
        """Build a restart-prompt payload matching the project's handoff convention."""
        lines: list[str] = []

        if mode != SessionCompressionMode.COMPACT:
            lines.append("Read CONTEXT.md, STATUS.md, HANDOFF.md, and WORKLOG.md first.")
            lines.append("No recap.")
            lines.append("Assume saved constraints remain active.")
            lines.append("Resume from the last safe point or current stage.")
            lines.append("")

        lines.append(f"Current stage: {summary.current_stage}")
        lines.append(f"Last completed: {summary.last_completed_task}")

        if summary.known_blockers:
            lines.append(f"Blockers: {'; '.join(summary.known_blockers)}")
        else:
            lines.append("Blockers: none")

        lines.append(f"Next safe step: {summary.next_safe_step}")

        if summary.key_files:
            lines.append(f"Key files: {', '.join(summary.key_files)}")

        if mode == SessionCompressionMode.RICH and summary.context_notes:
            lines.append("")
            lines.append("Context notes:")
            for note in summary.context_notes:
                lines.append(f"- {note}")

        return SessionRestartPrompt(
            session_id=session.session_id,
            mode=mode,
            prompt_text="\n".join(lines),
            summary=summary,
        )

    def compress_session(
        self,
        session: WorkSession,
        request: GenerateSessionHandoffRequest | None = None,
    ) -> WorkSession:
        """Generate handoff + restart prompt, trim lifecycle, mark session compressed."""
        req = request or GenerateSessionHandoffRequest()
        mode = req.mode

        summary = self.generate_handoff(session, req)
        restart = self.generate_restart_prompt(session, summary, mode)

        session.handoff_summary = summary
        session.restart_prompt = restart
        session.compressed = True
        session.resumable = True
        session.compression_mode = mode

        # Trim lifecycle events per mode
        limit = _LIFECYCLE_LIMITS.get(mode)
        if limit is not None and len(session.lifecycle) > limit:
            session.lifecycle = session.lifecycle[-limit:]

        return session


# ------------------------------------------------------------------
# Private helpers
# ------------------------------------------------------------------


def _infer_stage(session: WorkSession) -> str:
    """Best-effort stage from session status + metadata."""
    if "current_stage" in session.metadata:
        return str(session.metadata["current_stage"])
    return session.status.value


def _infer_blockers(session: WorkSession) -> list[str]:
    """Extract blockers from metadata or status."""
    if "blockers" in session.metadata:
        raw = session.metadata["blockers"]
        if isinstance(raw, list):
            return [str(b) for b in raw]
        return [str(raw)]
    if session.status.value in ("blocked", "failed"):
        return [f"Session is {session.status.value}"]
    return []
