"""Local state diagnostics and safe reconciliation helpers."""

from __future__ import annotations

from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from typing import Any

from backend.models.builder import BuilderJobLifecycleEvent, BuilderJobResult, BuilderJobStatus
from backend.models.core import WorkflowStatus
from backend.models.results import ResultOutcome
from backend.models.session import SessionEventType, SessionLifecycleEvent, SessionStatus
from backend.storage.builder_jobs import BuilderJobStore
from backend.storage.sessions import SessionStore
from backend.storage.store import StateStore


_ACTIVE_SESSION_STATES = {
    SessionStatus.ASSIGNED,
    SessionStatus.WAITING_FOR_PROMPT_DELIVERY,
    SessionStatus.RUNNING,
    SessionStatus.WAITING_FOR_RESULT,
}


@dataclass
class ReconcileIssue:
    kind: str
    severity: str
    target_id: str
    message: str
    details: dict[str, Any]
    auto_repairable: bool
    repaired: bool = False


class StateReconciler:
    def __init__(
        self,
        *,
        state_store: StateStore | None = None,
        job_store: BuilderJobStore | None = None,
        session_store: SessionStore | None = None,
    ) -> None:
        self.state_store = state_store or StateStore()
        self.job_store = job_store or BuilderJobStore()
        self.session_store = session_store or SessionStore()

    def run(self, *, apply: bool = False) -> dict[str, Any]:
        issues: list[ReconcileIssue] = []
        issues.extend(self._workflow_followup_conflicts(apply=apply))
        issues.extend(self._duplicate_active_assignments(apply=apply))
        issues.extend(self._legacy_waiting_for_prompt_residue(apply=apply))
        issues.extend(self._pending_jobs_with_ingested_results(apply=apply))
        return {
            "apply": apply,
            "issues": [asdict(issue) for issue in issues],
            "summary": {
                "total": len(issues),
                "auto_repairable": sum(1 for issue in issues if issue.auto_repairable),
                "repaired": sum(1 for issue in issues if issue.repaired),
                "flagged_only": sum(1 for issue in issues if not issue.repaired),
            },
        }

    def _workflow_followup_conflicts(self, *, apply: bool) -> list[ReconcileIssue]:
        issues: list[ReconcileIssue] = []
        for workflow_id in self.state_store.list_ids():
            state = self.state_store.load(workflow_id)
            if state is None or state.next_step is None:
                continue
            action = state.next_step.suggestion.action.value
            if state.status == WorkflowStatus.COMPLETED and action != "mark_workflow_complete":
                issue = ReconcileIssue(
                    kind="workflow_stale_completed",
                    severity="high",
                    target_id=workflow_id,
                    message="Workflow is completed but latest linked result requires nonterminal follow-up.",
                    details={
                        "status": state.status.value,
                        "latest_action": action,
                        "latest_outcome": state.next_step.ingestion.outcome.value,
                    },
                    auto_repairable=True,
                )
                if apply:
                    state.status = WorkflowStatus.APPROVED
                    state.current_stage = "result_needs_followup"
                    self.state_store.save(state)
                    issue.repaired = True
                issues.append(issue)
        return issues

    def _duplicate_active_assignments(self, *, apply: bool = False) -> list[ReconcileIssue]:
        issues: list[ReconcileIssue] = []
        from backend.models.session import WorkSession
        active_by_job: dict[str, list[WorkSession]] = {}
        for session in self.session_store.list_sessions():
            if session.assigned_job_id and session.status in _ACTIVE_SESSION_STATES:
                active_by_job.setdefault(session.assigned_job_id, []).append(session)
        for job_id, sessions in active_by_job.items():
            if len(sessions) <= 1:
                continue
            sorted_sessions = sorted(sessions, key=lambda s: s.updated_at, reverse=True)
            canonical = sorted_sessions[0]
            stale = sorted_sessions[1:]
            issue = ReconcileIssue(
                kind="duplicate_active_session_assignment",
                severity="critical",
                target_id=job_id,
                message="Multiple active sessions are linked to the same builder job.",
                details={
                    "job_id": job_id,
                    "session_ids": [s.session_id for s in sessions],
                    "canonical_session": canonical.session_id,
                    "stale_sessions": [s.session_id for s in stale],
                },
                auto_repairable=True,
            )
            if apply:
                for s in stale:
                    s.status = SessionStatus.BLOCKED
                    s.next_expected_action = f"Superseded by canonical session {canonical.session_id} during reconciliation."
                    s.lifecycle.append(
                        SessionLifecycleEvent(
                            event_type=SessionEventType.STATUS_UPDATED,
                            status=s.status,
                            recorded_at=datetime.now(timezone.utc),
                            note=f"Demoted: duplicate active assignment for job {job_id}. Canonical session: {canonical.session_id}.",
                            assigned_job_id=s.assigned_job_id,
                            next_expected_action=s.next_expected_action,
                            metadata={
                                "reconciled": True,
                                "reason": "duplicate_active_session_assignment",
                                "canonical_session": canonical.session_id,
                            },
                        )
                    )
                    self.session_store.save(s)
                issue.repaired = True
            issues.append(issue)
        return issues

    def _legacy_waiting_for_prompt_residue(self, *, apply: bool) -> list[ReconcileIssue]:
        issues: list[ReconcileIssue] = []
        for session in self.session_store.list_sessions():
            if session.status != SessionStatus.WAITING_FOR_PROMPT_DELIVERY:
                continue
            delivered = any(event.event_type == SessionEventType.PROMPT_DELIVERED for event in session.lifecycle)
            if not delivered:
                continue
            issue = ReconcileIssue(
                kind="legacy_waiting_for_prompt_residue",
                severity="medium",
                target_id=session.session_id,
                message="Session still says waiting_for_prompt_delivery even though prompt delivery was already recorded.",
                details={
                    "session_id": session.session_id,
                    "assigned_job_id": session.assigned_job_id,
                },
                auto_repairable=True,
            )
            if apply:
                session.status = SessionStatus.WAITING_FOR_RESULT
                session.next_expected_action = "Prompt delivered manually. Waiting for session result."
                session.lifecycle.append(
                    SessionLifecycleEvent(
                        event_type=SessionEventType.STATUS_UPDATED,
                        status=session.status,
                        recorded_at=datetime.now(timezone.utc),
                        note="Reconciled legacy waiting_for_prompt_delivery residue to waiting_for_result.",
                        assigned_job_id=session.assigned_job_id,
                        next_expected_action=session.next_expected_action,
                        metadata={"reconciled": True, "reason": "legacy_waiting_for_prompt_residue"},
                    )
                )
                self.session_store.save(session)
                issue.repaired = True
            issues.append(issue)
        return issues

    def _pending_jobs_with_ingested_results(self, *, apply: bool) -> list[ReconcileIssue]:
        """Detect jobs that are still pending but already have ingested result state.

        This covers the lifecycle gap where result-ingestion was recorded on a job
        before the fix landed, leaving the job incoherently stuck in pending.
        Repair mirrors the canonical logic in BuilderJobStore.ingest_planning_record:
        advance the job to completed/failed, write outbox, archive inbox.
        """
        issues: list[ReconcileIssue] = []
        for job in self.job_store.list_jobs():
            if job.status != BuilderJobStatus.PENDING:
                continue
            if not job.ingested_results:
                continue
            latest = job.next_step or job.ingested_results[-1]
            outcome = latest.ingestion.outcome
            issue = ReconcileIssue(
                kind="pending_job_with_ingested_results",
                severity="high",
                target_id=job.id,
                message=(
                    "Builder job is pending but has ingested result state. "
                    "No worker claim exists; job is stuck in a lifecycle gap."
                ),
                details={
                    "job_id": job.id,
                    "workflow_id": job.workflow_id,
                    "proposal_id": job.proposal_id,
                    "ingested_results_count": len(job.ingested_results),
                    "last_outcome": outcome.value,
                    "last_next_action": latest.suggestion.action.value,
                    "claimed_by": job.claimed_by.worker_id if job.claimed_by else None,
                },
                auto_repairable=True,
            )
            if apply:
                now = datetime.now(timezone.utc)
                if outcome in (ResultOutcome.FAILED,):
                    new_status = BuilderJobStatus.FAILED
                else:
                    new_status = BuilderJobStatus.COMPLETED
                if job.started_at is None:
                    job.started_at = now
                job.completed_at = now
                job.status = new_status
                job.result = BuilderJobResult(
                    status=new_status,
                    summary=latest.ingestion.result.summary or f"Reconciled; outcome={outcome.value}",
                    output_ref=latest.ingestion.result.output_ref,
                    artifacts=list(latest.ingestion.result.artifacts),
                    metadata={
                        **latest.ingestion.result.metadata,
                        "reconciled": True,
                        "reason": "pending_job_with_ingested_results",
                        "outcome": outcome.value,
                        "next_action": latest.suggestion.action.value,
                    },
                )
                # Outbox / archive / lock cleanup
                self.job_store._outbox_path(job.id).write_text(job.model_dump_json(indent=2))
                inbox = self.job_store._inbox_path(job.id)
                if inbox.exists():
                    inbox.replace(self.job_store._archive_path(job.id))
                self.job_store._release_lock(job.id)
                job.lifecycle.append(
                    BuilderJobLifecycleEvent(
                        status=new_status,
                        recorded_at=now,
                        note=(
                            f"Reconciled: job advanced from pending to {new_status.value} "
                            f"(outcome={outcome.value}). Lifecycle gap closed by reconciler."
                        ),
                        metadata={
                            "reconciled": True,
                            "reason": "pending_job_with_ingested_results",
                            "outcome": outcome.value,
                        },
                    )
                )
                self.job_store.save(job)

                # Sync the workflow bridge snapshot
                state = self.state_store.load(job.workflow_id)
                if state is not None:
                    bridge = state.context.setdefault("builder_bridge", {})
                    if job.id in bridge:
                        bridge[job.id]["status"] = new_status.value
                        bridge[job.id]["updated_at"] = now.isoformat()
                    if new_status == BuilderJobStatus.COMPLETED:
                        if state.status not in (WorkflowStatus.COMPLETED, WorkflowStatus.ERROR):
                            state.status = WorkflowStatus.APPROVED
                    self.state_store.save(state)

                issue.repaired = True
            issues.append(issue)
        return issues
