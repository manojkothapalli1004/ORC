"""Typed result-ingestion and next-step planning models."""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum

from pydantic import BaseModel, Field


def _now() -> datetime:
    return datetime.now(timezone.utc)


class ResultOutcome(str, Enum):
    SUCCESS = "success"
    PARTIAL_SUCCESS = "partial_success"
    NEEDS_FOLLOWUP = "needs_followup"
    BLOCKED = "blocked"
    FAILED = "failed"


class NextStepAction(str, Enum):
    NO_ACTION = "no_action"
    REVIEW_RESULT = "review_result"
    CREATE_FOLLOWUP_JOB = "create_followup_job"
    REQUEST_APPROVAL = "request_approval"
    RETRY_WITH_CHANGES = "retry_with_changes"
    MARK_WORKFLOW_COMPLETE = "mark_workflow_complete"


class ResultAttachmentLink(BaseModel):
    workflow_id: str
    proposal_id: str = ""
    job_id: str = ""
    session_id: str = ""


class StructuredWorkerResult(BaseModel):
    session_id: str = ""
    summary: str = ""
    output_ref: str = ""
    details: str = ""
    artifacts: list[str] = Field(default_factory=list)
    error: str | None = None
    metadata: dict = Field(default_factory=dict)


class ResultIngestionRecord(BaseModel):
    id: str
    source: str = "local_worker"
    attached_to: ResultAttachmentLink
    result: StructuredWorkerResult
    outcome: ResultOutcome
    recorded_at: datetime = Field(default_factory=_now)


class NextStepSuggestion(BaseModel):
    action: NextStepAction
    reason: str
    confidence: str = "deterministic"
    followup_category: str = ""
    notes: list[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=_now)


class ResultPlanningRecord(BaseModel):
    ingestion: ResultIngestionRecord
    suggestion: NextStepSuggestion


class ResultIngestionRequest(BaseModel):
    source: str = "local_worker"
    workflow_id: str
    proposal_id: str = ""
    job_id: str = ""
    session_id: str = ""
    summary: str = ""
    output_ref: str = ""
    details: str = ""
    artifacts: list[str] = Field(default_factory=list)
    error: str | None = None
    metadata: dict = Field(default_factory=dict)


class ResultHistoryResponse(BaseModel):
    workflow_id: str
    job_id: str = ""
    results: list[ResultPlanningRecord] = Field(default_factory=list)


class NextStepResponse(BaseModel):
    workflow_id: str
    job_id: str = ""
    suggestion: NextStepSuggestion | None = None
