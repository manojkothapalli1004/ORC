"""Typed builder bridge job, handoff, worker runtime, and result models."""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum

from pydantic import BaseModel, Field

from .core import ApprovalStatus
from .handoff import BuilderJobCategory, ClaudeHandoffContract
from .results import ResultPlanningRecord


def _now() -> datetime:
    return datetime.now(timezone.utc)


class BuilderJobStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class BuilderJobTerminalStatus(str, Enum):
    COMPLETED = "completed"
    FAILED = "failed"


class BuilderBridgeChannel(str, Enum):
    FILE_INBOX = "file_inbox"


class WorkerRuntimeSource(str, Enum):
    LOCAL_FILESYSTEM = "local_filesystem"


class WorkerAdapterKind(str, Enum):
    PROVIDER = "provider"
    MANUAL_SAFE = "manual_safe"
    MOCK = "mock"
    CUSTOM = "custom"


class BuilderWorkerLease(BaseModel):
    worker_id: str
    claimed_at: datetime = Field(default_factory=_now)
    lock_path: str
    source: WorkerRuntimeSource = WorkerRuntimeSource.LOCAL_FILESYSTEM


class BuilderExecutionRequest(BaseModel):
    job_id: str
    workflow_id: str
    proposal_id: str
    proposal_batch_index: int = 0
    proposal_prompt: str
    category: BuilderJobCategory = BuilderJobCategory.BUILD
    handoff_contract: ClaudeHandoffContract | None = None
    metadata: dict = Field(default_factory=dict)


class BuilderExecutionResult(BaseModel):
    status: BuilderJobTerminalStatus
    summary: str = ""
    output_ref: str = ""
    artifacts: list[str] = Field(default_factory=list)
    error: str | None = None
    metadata: dict = Field(default_factory=dict)


class BuilderJobResult(BaseModel):
    status: BuilderJobStatus
    summary: str = ""
    output_ref: str = ""
    artifacts: list[str] = Field(default_factory=list)
    error: str | None = None
    metadata: dict = Field(default_factory=dict)
    recorded_at: datetime = Field(default_factory=_now)


class BuilderJobLifecycleEvent(BaseModel):
    status: BuilderJobStatus
    recorded_at: datetime = Field(default_factory=_now)
    worker_id: str = ""
    note: str = ""
    metadata: dict = Field(default_factory=dict)


class BuilderJob(BaseModel):
    id: str
    workflow_id: str
    proposal_id: str
    proposal_batch_index: int = 0
    proposal_prompt: str
    approval_status: ApprovalStatus
    category: BuilderJobCategory = BuilderJobCategory.BUILD
    status: BuilderJobStatus = BuilderJobStatus.PENDING
    channel: BuilderBridgeChannel = BuilderBridgeChannel.FILE_INBOX
    inbox_path: str
    outbox_path: str
    created_at: datetime = Field(default_factory=_now)
    updated_at: datetime = Field(default_factory=_now)
    started_at: datetime | None = None
    completed_at: datetime | None = None
    claimed_by: BuilderWorkerLease | None = None
    lifecycle: list[BuilderJobLifecycleEvent] = Field(default_factory=list)
    handoff_contract: ClaudeHandoffContract | None = None
    result: BuilderJobResult | None = None
    ingested_results: list[ResultPlanningRecord] = Field(default_factory=list)
    next_step: ResultPlanningRecord | None = None
    metadata: dict = Field(default_factory=dict)


class BuilderJobDispatchRequest(BaseModel):
    proposal_id: str
    category: BuilderJobCategory = BuilderJobCategory.BUILD


class BuilderJobResultRequest(BaseModel):
    status: BuilderJobStatus
    summary: str = ""
    output_ref: str = ""
    artifacts: list[str] = Field(default_factory=list)
    error: str | None = None
    metadata: dict = Field(default_factory=dict)


class BuilderJobLink(BaseModel):
    workflow_id: str
    proposal_id: str


class BuilderJobQueueItem(BaseModel):
    job_id: str
    status: BuilderJobStatus
    category: BuilderJobCategory
    workflow: BuilderJobLink
    summary: str = ""
    approval_status: ApprovalStatus
    created_at: datetime
    updated_at: datetime
    started_at: datetime | None = None
    completed_at: datetime | None = None
    worker_id: str | None = None
    output_ref: str = ""
    error: str | None = None
    artifacts: list[str] = Field(default_factory=list)
    metadata: dict = Field(default_factory=dict)


class BuilderJobQueueSummary(BaseModel):
    total: int = 0
    pending: int = 0
    running: int = 0
    completed: int = 0
    failed: int = 0
    items: list[BuilderJobQueueItem] = Field(default_factory=list)
    updated_at: datetime = Field(default_factory=_now)
    bridge: dict = Field(default_factory=dict)
