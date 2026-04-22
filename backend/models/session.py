"""Typed local session-manager models for Claude/Antigravity work tracking."""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum

from pydantic import BaseModel, Field

from .results import ResultOutcome


def _now() -> datetime:
    return datetime.now(timezone.utc)


class SessionRole(str, Enum):
    CLAUDE = "claude"
    ANTIGRAVITY = "antigravity"
    GENERIC = "generic"


class SessionStatus(str, Enum):
    IDLE = "idle"
    ASSIGNED = "assigned"
    WAITING_FOR_PROMPT_DELIVERY = "waiting_for_prompt_delivery"
    RUNNING = "running"
    WAITING_FOR_RESULT = "waiting_for_result"
    COMPLETED = "completed"
    FAILED = "failed"
    BLOCKED = "blocked"


class SessionCompressionMode(str, Enum):
    COMPACT = "compact"
    NORMAL = "normal"
    RICH = "rich"


class SessionEventType(str, Enum):
    REGISTERED = "registered"
    JOB_ASSIGNED = "job_assigned"
    STATUS_UPDATED = "status_updated"
    RESULT_RECORDED = "result_recorded"
    PROMPT_DELIVERED = "prompt_delivered"


class SessionAssignment(BaseModel):
    assigned_job_id: str = ""
    assigned_at: datetime | None = None


class SessionLifecycleEvent(BaseModel):
    event_type: SessionEventType
    status: SessionStatus
    recorded_at: datetime = Field(default_factory=_now)
    note: str = ""
    assigned_job_id: str = ""
    result_summary: str = ""
    next_expected_action: str = ""
    metadata: dict = Field(default_factory=dict)


class SessionHandoffSummary(BaseModel):
    current_stage: str = ""
    last_completed_task: str = ""
    known_blockers: list[str] = Field(default_factory=list)
    next_safe_step: str = ""
    key_files: list[str] = Field(default_factory=list)
    context_notes: list[str] = Field(default_factory=list)
    mode: SessionCompressionMode = SessionCompressionMode.NORMAL
    generated_at: datetime = Field(default_factory=_now)


class SessionRestartPrompt(BaseModel):
    session_id: str
    mode: SessionCompressionMode = SessionCompressionMode.NORMAL
    prompt_text: str = ""
    summary: SessionHandoffSummary | None = None
    generated_at: datetime = Field(default_factory=_now)


class WorkSession(BaseModel):
    session_id: str
    role: SessionRole = SessionRole.GENERIC
    assignment: SessionAssignment = Field(default_factory=SessionAssignment)
    assigned_job_id: str = ""
    status: SessionStatus = SessionStatus.IDLE
    last_activity_at: datetime = Field(default_factory=_now)
    last_result_summary: str = ""
    next_expected_action: str = "Register a job or leave idle."
    created_at: datetime = Field(default_factory=_now)
    updated_at: datetime = Field(default_factory=_now)
    compressed: bool = False
    resumable: bool = False
    compression_mode: SessionCompressionMode | None = None
    handoff_summary: SessionHandoffSummary | None = None
    restart_prompt: SessionRestartPrompt | None = None
    lifecycle: list[SessionLifecycleEvent] = Field(default_factory=list)
    metadata: dict = Field(default_factory=dict)

    def model_post_init(self, __context) -> None:
        self.assigned_job_id = self.assignment.assigned_job_id
        if not self.lifecycle:
            self.lifecycle.append(
                SessionLifecycleEvent(
                    event_type=SessionEventType.REGISTERED,
                    status=self.status,
                    note="Session registered in local registry.",
                    assigned_job_id=self.assigned_job_id,
                    next_expected_action=self.next_expected_action,
                )
            )

    def can_transition_to(self, next_status: SessionStatus) -> bool:
        allowed: dict[SessionStatus, set[SessionStatus]] = {
            SessionStatus.IDLE: {SessionStatus.IDLE, SessionStatus.ASSIGNED, SessionStatus.FAILED, SessionStatus.BLOCKED},
            SessionStatus.ASSIGNED: {
                SessionStatus.ASSIGNED,
                SessionStatus.WAITING_FOR_PROMPT_DELIVERY,
                SessionStatus.WAITING_FOR_RESULT,
                SessionStatus.RUNNING,
                SessionStatus.FAILED,
                SessionStatus.BLOCKED,
            },
            SessionStatus.WAITING_FOR_PROMPT_DELIVERY: {
                SessionStatus.WAITING_FOR_PROMPT_DELIVERY,
                SessionStatus.RUNNING,
                SessionStatus.FAILED,
                SessionStatus.BLOCKED,
            },
            SessionStatus.RUNNING: {
                SessionStatus.RUNNING,
                SessionStatus.WAITING_FOR_RESULT,
                SessionStatus.COMPLETED,
                SessionStatus.FAILED,
                SessionStatus.BLOCKED,
            },
            SessionStatus.WAITING_FOR_RESULT: {
                SessionStatus.WAITING_FOR_RESULT,
                SessionStatus.RUNNING,
                SessionStatus.COMPLETED,
                SessionStatus.FAILED,
                SessionStatus.BLOCKED,
            },
            SessionStatus.COMPLETED: {SessionStatus.COMPLETED, SessionStatus.IDLE, SessionStatus.ASSIGNED},
            SessionStatus.FAILED: {SessionStatus.FAILED, SessionStatus.IDLE, SessionStatus.ASSIGNED},
            SessionStatus.BLOCKED: {SessionStatus.BLOCKED, SessionStatus.RUNNING, SessionStatus.IDLE, SessionStatus.ASSIGNED},
        }
        return next_status in allowed[self.status]

    def require_transition(self, next_status: SessionStatus) -> None:
        if not self.can_transition_to(next_status):
            raise ValueError(f"Invalid session transition: {self.status.value} -> {next_status.value}")

    def append_event(
        self,
        *,
        event_type: SessionEventType,
        note: str,
        result_summary: str = "",
        metadata: dict | None = None,
    ) -> None:
        self.assigned_job_id = self.assignment.assigned_job_id
        self.lifecycle.append(
            SessionLifecycleEvent(
                event_type=event_type,
                status=self.status,
                note=note,
                assigned_job_id=self.assigned_job_id,
                result_summary=result_summary or self.last_result_summary,
                next_expected_action=self.next_expected_action,
                metadata=dict(metadata or {}),
            )
        )

    def assign_job(self, job_id: str, next_expected_action: str) -> None:
        self.require_transition(SessionStatus.ASSIGNED)
        self.assignment = SessionAssignment(assigned_job_id=job_id, assigned_at=_now())
        self.assigned_job_id = job_id
        self.status = SessionStatus.ASSIGNED
        self.next_expected_action = next_expected_action
        self.append_event(
            event_type=SessionEventType.JOB_ASSIGNED,
            note="Job assigned to session.",
            metadata={"job_id": job_id},
        )

    def update_status(self, status: SessionStatus, next_expected_action: str = "", metadata: dict | None = None) -> None:
        self.require_transition(status)
        self.status = status
        if next_expected_action:
            self.next_expected_action = next_expected_action
        self.metadata = {**self.metadata, **dict(metadata or {})}
        if status == SessionStatus.IDLE:
            self.assignment = SessionAssignment()
            self.assigned_job_id = ""
        self.append_event(
            event_type=SessionEventType.STATUS_UPDATED,
            note="Session status updated.",
            metadata=metadata,
        )

    def record_result(self, summary: str, next_expected_action: str, metadata: dict | None = None) -> None:
        self.last_result_summary = summary
        self.next_expected_action = next_expected_action
        self.metadata = {**self.metadata, **dict(metadata or {})}
        if self.status == SessionStatus.RUNNING:
            self.status = SessionStatus.WAITING_FOR_RESULT
        self.append_event(
            event_type=SessionEventType.RESULT_RECORDED,
            note="Session result summary recorded.",
            result_summary=summary,
            metadata=metadata,
        )


class CreateSessionRequest(BaseModel):
    session_id: str
    role: SessionRole = SessionRole.GENERIC
    next_expected_action: str = "Await assignment."
    metadata: dict = Field(default_factory=dict)


class AssignSessionJobRequest(BaseModel):
    job_id: str
    next_expected_action: str = "Deliver prompt contract to session."


class UpdateSessionStatusRequest(BaseModel):
    status: SessionStatus
    next_expected_action: str = ""
    metadata: dict = Field(default_factory=dict)


class RecordSessionResultRequest(BaseModel):
    outcome: ResultOutcome
    last_result_summary: str
    notes: str = ""
    output_ref: str = ""
    artifacts: list[str] = Field(default_factory=list)
    next_expected_action: str = "Await next assignment."
    metadata: dict = Field(default_factory=dict)


class GenerateSessionHandoffRequest(BaseModel):
    mode: SessionCompressionMode = SessionCompressionMode.NORMAL
    current_stage: str = ""
    last_completed_task: str = ""
    known_blockers: list[str] = Field(default_factory=list)
    next_safe_step: str = ""
    key_files: list[str] = Field(default_factory=list)
    context_notes: list[str] = Field(default_factory=list)


class SessionSummaryItem(BaseModel):
    session_id: str
    role: SessionRole
    assigned_job_id: str = ""
    status: SessionStatus
    last_activity_at: datetime
    last_result_summary: str = ""
    next_expected_action: str = ""
    compressed: bool = False
    resumable: bool = False
    compression_mode: SessionCompressionMode | None = None
    assigned_at: datetime | None = None
    lifecycle_count: int = 0


class SessionDetailView(BaseModel):
    session_id: str
    role: SessionRole
    assigned_job_id: str = ""
    assigned_at: datetime | None = None
    status: SessionStatus
    last_activity_at: datetime
    last_result_summary: str = ""
    next_expected_action: str = ""
    lifecycle: list[SessionLifecycleEvent] = Field(default_factory=list)
    compressed: bool = False
    resumable: bool = False
    compression_mode: SessionCompressionMode | None = None
    metadata: dict = Field(default_factory=dict)
    updated_at: datetime = Field(default_factory=_now)


class SessionRegistryView(BaseModel):
    total: int = 0
    idle: int = 0
    assigned: int = 0
    waiting_for_prompt_delivery: int = 0
    running: int = 0
    waiting_for_result: int = 0
    completed: int = 0
    failed: int = 0
    blocked: int = 0
    compressed: int = 0
    resumable: int = 0
    sessions: list[SessionSummaryItem] = Field(default_factory=list)
    updated_at: datetime = Field(default_factory=_now)


class SessionHandoffView(BaseModel):
    session_id: str
    resumable: bool = False
    summary: SessionHandoffSummary | None = None
    restart_prompt: SessionRestartPrompt | None = None
    updated_at: datetime = Field(default_factory=_now)
