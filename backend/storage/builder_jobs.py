"""File-based builder bridge queue storage and handoff payload helpers."""

from __future__ import annotations

import json
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path

from backend.config import settings
from backend.handoff import ClaudeHandoffRenderer
from backend.models.builder import (
    BuilderExecutionResult,
    BuilderJob,
    BuilderJobCategory,
    BuilderJobLifecycleEvent,
    BuilderJobLink,
    BuilderJobQueueItem,
    BuilderJobQueueSummary,
    BuilderJobResult,
    BuilderJobStatus,
    BuilderWorkerLease,
)
from backend.models.core import ApprovalStatus, Proposal, WorkflowState, WorkflowStatus
from backend.models.handoff import BuilderJobInboxPayload
from backend.models.results import NextStepAction, ResultOutcome, ResultPlanningRecord
from backend.planning import build_planning_record

CONTRACT_VERSION = "claude_handoff_v1"
HANDOFF_CONSUMER = "local_claude_antigravity_handoff"
EXPECTED_RETURN_FORMAT = "structured_text_contract_v1"
DEFAULT_CATEGORY = BuilderJobCategory.BUILD
SUPPORTED_CATEGORIES = list(BuilderJobCategory)


def build_job_view(job: BuilderJob) -> dict:
    inbox = Path(job.inbox_path)
    return {
        **job.model_dump(mode="json"),
        "handoff_contract": job.handoff_contract.model_dump(mode="json") if job.handoff_contract else None,
        "inbox_payload_path": job.inbox_path,
        "outbox_payload_path": job.outbox_path,
        "archived_inbox_path": str(inbox.parent.parent / "archive" / inbox.name),
        "bridge_consumer": HANDOFF_CONSUMER,
        "result_history": [item.model_dump(mode="json") for item in job.ingested_results],
        "next_step": job.next_step.model_dump(mode="json") if job.next_step else None,
    }


def bridge_summary_from_jobs(jobs: list[BuilderJob], store: "BuilderJobStore") -> dict:
    return {
        "channel": "file_inbox_outbox",
        "dispatch_rule": "approved_only",
        "consumer": HANDOFF_CONSUMER,
        "paths": {
            "root": str(store.root_dir),
            "inbox": str(store.inbox_dir),
            "outbox": str(store.outbox_dir),
            "records": str(store.root_dir / "records"),
        },
        "counts": {
            "total": len(jobs),
            "pending": sum(1 for job in jobs if job.status == BuilderJobStatus.PENDING),
            "running": sum(1 for job in jobs if job.status == BuilderJobStatus.RUNNING),
            "completed": sum(1 for job in jobs if job.status == BuilderJobStatus.COMPLETED),
            "failed": sum(1 for job in jobs if job.status == BuilderJobStatus.FAILED),
        },
        "safe_execution": False,
        "note": "Jobs are persisted as Claude handoff contracts for local handoff only. The orchestrator does not execute shell commands.",
        "supported_categories": [category.value for category in SUPPORTED_CATEGORIES],
        "contract_version": CONTRACT_VERSION,
        "expected_return_format": EXPECTED_RETURN_FORMAT,
    }


def dispatchability_payload(*, workflow_id: str, proposal_id: str, approval: ApprovalStatus, dispatchable: bool) -> dict:
    return {
        "workflow_id": workflow_id,
        "proposal_id": proposal_id,
        "approval": approval.value,
        "dispatchable": dispatchable,
        "supported_categories": [category.value for category in SUPPORTED_CATEGORIES],
        "default_category": DEFAULT_CATEGORY.value,
    }


def execution_request_metadata(job: BuilderJob, worker_id: str) -> dict:
    return {
        **job.metadata,
        "approval_status": job.approval_status.value,
        "worker_id": worker_id,
        "category": job.category.value,
        "contract_version": CONTRACT_VERSION,
        "consumer": HANDOFF_CONSUMER,
        "expected_return_format": EXPECTED_RETURN_FORMAT,
    }


def attach_result_to_workflow_state(state: WorkflowState, record: ResultPlanningRecord) -> WorkflowState:
    state.result_history.append(record)
    state.next_step = record
    state.context.setdefault("result_planning", {})[record.ingestion.id] = record.model_dump(mode="json")
    action = record.suggestion.action.value
    if action == "mark_workflow_complete":
        state.status = WorkflowStatus.COMPLETED
        state.current_stage = "result_complete"
    elif action == "request_approval":
        state.status = WorkflowStatus.BLOCKED
        state.current_stage = "result_blocked"
    elif action == "retry_with_changes":
        state.status = WorkflowStatus.ERROR
        state.current_stage = "result_failed"
    else:
        state.status = WorkflowStatus.APPROVED
        state.current_stage = "result_needs_followup"
    return state


def build_workflow_bridge_state(job: BuilderJob) -> dict:
    return {
        "job_id": job.id,
        "proposal_id": job.proposal_id,
        "workflow_id": job.workflow_id,
        "status": job.status.value,
        "category": job.category.value,
        "approval_status": job.approval_status.value,
        "inbox_path": job.inbox_path,
        "outbox_path": job.outbox_path,
        "claimed_by": job.claimed_by.model_dump(mode="json") if job.claimed_by else None,
        "result": job.result.model_dump(mode="json") if job.result else None,
        "result_history": [item.model_dump(mode="json") for item in job.ingested_results],
        "next_step": job.next_step.model_dump(mode="json") if job.next_step else None,
        "updated_at": job.updated_at.isoformat(),
    }


def build_queue_item(job: BuilderJob) -> BuilderJobQueueItem:
    metadata = dict(job.result.metadata if job.result else job.metadata)
    if job.next_step is not None:
        metadata.update(
            {
                "last_outcome": job.next_step.ingestion.outcome.value,
                "last_next_action": job.next_step.suggestion.action.value,
            }
        )
    return BuilderJobQueueItem(
        job_id=job.id,
        status=job.status,
        category=job.category,
        workflow=BuilderJobLink(workflow_id=job.workflow_id, proposal_id=job.proposal_id),
        summary=(job.result.summary if job.result and job.result.summary else job.proposal_prompt),
        approval_status=job.approval_status,
        created_at=job.created_at,
        updated_at=job.updated_at,
        started_at=job.started_at,
        completed_at=job.completed_at,
        worker_id=job.claimed_by.worker_id if job.claimed_by else None,
        output_ref=job.result.output_ref if job.result and job.result.output_ref else "",
        error=job.result.error if job.result else None,
        artifacts=list(job.result.artifacts) if job.result else [],
        metadata=metadata,
    )


def build_queue_summary(jobs: list[BuilderJob], store: "BuilderJobStore") -> BuilderJobQueueSummary:
    items = [build_queue_item(job) for job in sorted(jobs, key=lambda item: item.updated_at, reverse=True)]
    counts = bridge_summary_from_jobs(jobs, store)["counts"]
    return BuilderJobQueueSummary(
        total=counts["total"],
        pending=counts["pending"],
        running=counts["running"],
        completed=counts["completed"],
        failed=counts["failed"],
        items=items,
        bridge=bridge_summary_from_jobs(jobs, store),
    )


class BuilderJobStore:
    def __init__(self, root_dir: Path | None = None) -> None:
        self._root = root_dir or settings.builder_job_dir
        self._records = self._root / "records"
        self._inbox = self._root / "inbox"
        self._outbox = self._root / "outbox"
        self._archive = self._root / "archive"
        self._locks = self._root / "locks"
        self._renderer = ClaudeHandoffRenderer()
        for path in (self._records, self._inbox, self._outbox, self._archive, self._locks):
            path.mkdir(parents=True, exist_ok=True)

    @property
    def root_dir(self) -> Path:
        return self._root

    @property
    def inbox_dir(self) -> Path:
        return self._inbox

    @property
    def outbox_dir(self) -> Path:
        return self._outbox

    @property
    def locks_dir(self) -> Path:
        return self._locks

    def _record_path(self, job_id: str) -> Path:
        return self._records / f"{job_id}.json"

    def _inbox_path(self, job_id: str) -> Path:
        return self._inbox / f"{job_id}.json"

    def _outbox_path(self, job_id: str) -> Path:
        return self._outbox / f"{job_id}.json"

    def _archive_path(self, job_id: str) -> Path:
        return self._archive / f"{job_id}.json"

    def _lock_path(self, job_id: str) -> Path:
        return self._locks / f"{job_id}.lock"

    def list_jobs(self) -> list[BuilderJob]:
        jobs = []
        for path in sorted(self._records.glob("*.json")):
            jobs.append(BuilderJob.model_validate_json(path.read_text()))
        return jobs

    def load(self, job_id: str) -> BuilderJob | None:
        path = self._record_path(job_id)
        if not path.exists():
            return None
        return BuilderJob.model_validate_json(path.read_text())

    def save(self, job: BuilderJob) -> BuilderJob:
        job.updated_at = datetime.now(timezone.utc)
        tmp = self._record_path(job.id).with_suffix(".tmp")
        tmp.write_text(job.model_dump_json(indent=2))
        tmp.rename(self._record_path(job.id))
        return job

    def _append_lifecycle(self, job: BuilderJob, *, status: BuilderJobStatus, worker_id: str = "", note: str = "", metadata: dict | None = None) -> None:
        job.lifecycle.append(
            BuilderJobLifecycleEvent(
                status=status,
                worker_id=worker_id,
                note=note,
                metadata=dict(metadata or {}),
            )
        )

    def _create_lock(self, job_id: str, worker_id: str) -> Path:
        lock_path = self._lock_path(job_id)
        fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        try:
            payload = {
                "job_id": job_id,
                "worker_id": worker_id,
                "claimed_at": datetime.now(timezone.utc).isoformat(),
            }
            os.write(fd, json.dumps(payload, indent=2).encode())
        finally:
            os.close(fd)
        return lock_path

    def _release_lock(self, job_id: str) -> None:
        lock_path = self._lock_path(job_id)
        if lock_path.exists():
            lock_path.unlink()

    def find_active_for_proposal(self, workflow_id: str, proposal_id: str) -> BuilderJob | None:
        for job in self.list_jobs():
            if job.workflow_id == workflow_id and job.proposal_id == proposal_id and job.status in (
                BuilderJobStatus.PENDING,
                BuilderJobStatus.RUNNING,
            ):
                return job
        return None

    def list_pending_jobs(self) -> list[BuilderJob]:
        return [job for job in self.list_jobs() if job.status == BuilderJobStatus.PENDING]

    def create_from_proposal(self, state: WorkflowState, proposal: Proposal, *, category: BuilderJobCategory = DEFAULT_CATEGORY) -> BuilderJob:
        if proposal.approval not in (ApprovalStatus.APPROVED, ApprovalStatus.AUTO_APPROVED):
            raise ValueError("Proposal is not approved for dispatch")

        existing = self.find_active_for_proposal(state.id, proposal.id)
        if existing is not None:
            return existing

        job_id = uuid.uuid4().hex[:12]
        job = BuilderJob(
            id=job_id,
            workflow_id=state.id,
            proposal_id=proposal.id,
            proposal_batch_index=proposal.batch_index,
            proposal_prompt=proposal.prompt,
            approval_status=proposal.approval,
            category=category,
            inbox_path=str(self._inbox_path(job_id)),
            outbox_path=str(self._outbox_path(job_id)),
            metadata={
                "handoff": "claude_local_bridge",
                "workflow_status": state.status.value,
                "approval_mode": state.approval_mode.value,
                "category": category.value,
                "contract_version": CONTRACT_VERSION,
                "consumer": HANDOFF_CONSUMER,
                "expected_return_format": EXPECTED_RETURN_FORMAT,
                "prompt_contract_only": True,
            },
        )
        job.handoff_contract = self._renderer.render(job=job, state=state, proposal=proposal, category=category)
        self._append_lifecycle(job, status=BuilderJobStatus.PENDING, note="Job persisted to local inbox.")
        self.save(job)
        inbox_payload = BuilderJobInboxPayload(
            job_id=job.id,
            workflow_id=job.workflow_id,
            proposal_id=job.proposal_id,
            category=job.category,
            approval_status=job.approval_status,
            contract=job.handoff_contract,
        )
        self._inbox_path(job.id).write_text(inbox_payload.model_dump_json(indent=2))
        return job

    def claim_next_pending(self, worker_id: str) -> BuilderJob | None:
        for inbox_path in sorted(self._inbox.glob("*.json")):
            job_id = inbox_path.stem
            job = self.load(job_id)
            if job is None or job.status != BuilderJobStatus.PENDING:
                continue
            try:
                lock_path = self._create_lock(job.id, worker_id)
            except FileExistsError:
                continue
            job = self.load(job.id) or job
            job.status = BuilderJobStatus.RUNNING
            if job.started_at is None:
                job.started_at = datetime.now(timezone.utc)
            job.claimed_by = BuilderWorkerLease(worker_id=worker_id, lock_path=str(lock_path))
            self._append_lifecycle(job, status=BuilderJobStatus.RUNNING, worker_id=worker_id, note="Job claimed by local worker.")
            job.result = BuilderJobResult(
                status=BuilderJobStatus.RUNNING,
                summary="Worker claimed job and started execution.",
                metadata={"worker_id": worker_id, "category": job.category.value},
            )
            self.save(job)
            return job
        return None

    def mark_result(self, job_id: str, result: BuilderJobResult) -> BuilderJob:
        job = self.load(job_id)
        if job is None:
            raise FileNotFoundError(job_id)

        job.status = result.status
        job.result = result
        now = datetime.now(timezone.utc)
        if result.status == BuilderJobStatus.RUNNING and job.started_at is None:
            job.started_at = now
        if result.status in (BuilderJobStatus.COMPLETED, BuilderJobStatus.FAILED):
            if job.started_at is None:
                job.started_at = now
            job.completed_at = now
            self._append_lifecycle(
                job,
                status=result.status,
                worker_id=job.claimed_by.worker_id if job.claimed_by else "",
                note="Job finished and outbox record written.",
                metadata=result.metadata,
            )
            self._outbox_path(job.id).write_text(job.model_dump_json(indent=2))
            inbox = self._inbox_path(job.id)
            if inbox.exists():
                inbox.replace(self._archive_path(job.id))
            self._release_lock(job.id)
        self.save(job)
        return job

    def finalize_execution(self, job_id: str, execution: BuilderExecutionResult) -> BuilderJob:
        return self.mark_result(
            job_id,
            BuilderJobResult(
                status=BuilderJobStatus(execution.status.value),
                summary=execution.summary,
                output_ref=execution.output_ref,
                artifacts=execution.artifacts,
                error=execution.error,
                metadata=execution.metadata,
            ),
        )

    def ingest_planning_record(self, job_id: str, record: ResultPlanningRecord) -> BuilderJob:
        job = self.load(job_id)
        if job is None:
            raise FileNotFoundError(job_id)
        job.ingested_results.append(record)
        job.next_step = record
        job.metadata = {
            **job.metadata,
            "last_outcome": record.ingestion.outcome.value,
            "last_next_action": record.suggestion.action.value,
        }

        # Advance job status when it is still pending but a result has been recorded.
        # A pending job with ingested result state is incoherent: no worker claimed it,
        # yet execution outcome is known. Canonicalise based on outcome.
        if job.status == BuilderJobStatus.PENDING:
            outcome = record.ingestion.outcome
            if outcome in (ResultOutcome.SUCCESS, ResultOutcome.PARTIAL_SUCCESS):
                new_status = BuilderJobStatus.COMPLETED
            elif outcome in (ResultOutcome.FAILED,):
                new_status = BuilderJobStatus.FAILED
            else:
                # NEEDS_FOLLOWUP, BLOCKED — terminal enough; treat as completed so
                # the job exits pending and the next-step suggestion drives follow-up.
                new_status = BuilderJobStatus.COMPLETED
            now = datetime.now(timezone.utc)
            if job.started_at is None:
                job.started_at = now
            job.completed_at = now
            job.status = new_status
            job.result = BuilderJobResult(
                status=new_status,
                summary=record.ingestion.result.summary or f"Result ingested; outcome={outcome.value}",
                output_ref=record.ingestion.result.output_ref,
                artifacts=list(record.ingestion.result.artifacts),
                metadata={
                    **record.ingestion.result.metadata,
                    "advanced_from_pending_on_ingest": True,
                    "outcome": outcome.value,
                    "next_action": record.suggestion.action.value,
                },
            )
            # Move inbox → archive, write outbox, release any stale lock.
            self._outbox_path(job.id).write_text(job.model_dump_json(indent=2))
            inbox = self._inbox_path(job.id)
            if inbox.exists():
                inbox.replace(self._archive_path(job.id))
            self._release_lock(job.id)
            self._append_lifecycle(
                job,
                status=new_status,
                note=(
                    f"Job advanced from pending on result ingest (outcome={outcome.value}). "
                    "No worker claim existed; lifecycle gap closed on ingest."
                ),
                metadata={
                    "outcome": outcome.value,
                    "next_action": record.suggestion.action.value,
                    "advanced_from_pending_on_ingest": True,
                },
            )
        else:
            self._append_lifecycle(
                job,
                status=job.status,
                note="Structured result ingested and next-step suggestion recorded.",
                metadata={
                    "outcome": record.ingestion.outcome.value,
                    "next_action": record.suggestion.action.value,
                },
            )

        self.save(job)
        return job

    def ingest_result_request(self, job_id: str, req) -> BuilderJob:
        return self.ingest_planning_record(job_id, build_planning_record(req))

    def result_history(self, job_id: str) -> list[ResultPlanningRecord]:
        job = self.load(job_id)
        if job is None:
            raise FileNotFoundError(job_id)
        return list(job.ingested_results)

    def suggested_next_step(self, job_id: str) -> ResultPlanningRecord | None:
        job = self.load(job_id)
        if job is None:
            raise FileNotFoundError(job_id)
        return job.next_step

    def workflow_jobs(self, workflow_id: str) -> list[BuilderJob]:
        return [job for job in self.list_jobs() if job.workflow_id == workflow_id]

    def workflow_history_payload(self, workflow_id: str) -> dict:
        results = []
        for job in self.workflow_jobs(workflow_id):
            results.extend(item.model_dump(mode="json") for item in job.ingested_results)
        return {"workflow_id": workflow_id, "job_id": "", "results": results}

    def workflow_next_step_payload(self, workflow_id: str) -> dict:
        latest = None
        for job in sorted(self.workflow_jobs(workflow_id), key=lambda item: item.updated_at, reverse=True):
            if job.next_step is not None:
                latest = job.next_step
                break
        return {
            "workflow_id": workflow_id,
            "job_id": "",
            "suggestion": latest.suggestion.model_dump(mode="json") if latest else None,
        }
