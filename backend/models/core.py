"""Typed shared models for the orchestrator control tower."""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field

from .results import ResultPlanningRecord



def _now() -> datetime:
    return datetime.now(timezone.utc)


class DemoEvent(BaseModel):
    stage: str
    status: str
    role: str
    summary: str
    provider: str = ""
    model: str = ""
    is_mock: bool = False
    created_at: datetime = Field(default_factory=_now)


class RoleAssignment(BaseModel):
    role: str
    configured_provider: str
    configured_model: str
    resolved_provider: str
    is_live: bool
    is_available: bool


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class WorkflowStatus(str, Enum):
    PENDING = "pending"
    IDLE = "idle"
    PLANNING = "planning"
    BUILDING = "building"
    REVIEWING = "reviewing"
    AWAITING_APPROVAL = "awaiting_approval"
    APPROVED = "approved"
    BLOCKED = "blocked"
    REJECTED = "rejected"
    ERROR = "error"
    COMPLETED = "completed"


class ApprovalMode(str, Enum):
    HUMAN = "human"
    AUTO_WITH_LIMITS = "auto_with_limits"
    FULL_AUTO = "full_auto"


class ApprovalStatus(str, Enum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    AUTO_APPROVED = "auto_approved"


class WorkflowExecutionMode(str, Enum):
    COMPACT = "compact"
    NORMAL = "normal"
    RICH = "rich"
    GO_WILD = "go_wild"


# ---------------------------------------------------------------------------
# Provider role mapping
# ---------------------------------------------------------------------------

class ProviderRole(BaseModel):
    """Maps an LLM provider to a role in the workflow."""

    role: str = Field(description="Role name: 'reviewer', 'planner', or 'builder'")
    provider: str = Field(description="Provider key: 'openai', 'anthropic', 'gemini', or 'router'")
    model: str = Field(description="Model identifier, e.g. 'gpt-4o' or 'claude-sonnet-4-20250514'")
    mode: str = Field(default="direct", description="Execution mode: 'direct' or 'router'")
    base_url: str = Field(default="", description="Optional OpenAI-compatible endpoint override")
    api_key_env: str = Field(default="", description="Optional env var name for provider auth override")
    metadata: dict[str, Any] = Field(default_factory=dict)


class WorkflowModeRolePolicy(BaseModel):
    role: str
    provider: str
    model: str
    mode: str = "direct"
    base_url: str = ""
    max_tokens: int = 4096


class WorkflowModeBudgetPolicy(BaseModel):
    max_files_per_batch: int = 5
    reviewer_max_tokens: int = 4096
    planner_max_tokens: int = 4096
    builder_max_tokens: int = 4096
    total_token_target: int = 12000
    cost_intensity: str = "balanced"


class WorkflowModePromptPolicy(BaseModel):
    template_compactness: str = "balanced"
    reviewer_system_style: str = "balanced"
    builder_system_style: str = "balanced"
    planner_system_style: str = "balanced"


class WorkflowModeContextPolicy(BaseModel):
    recent_batches: int = 5
    response_preview_chars: int = 200
    context_detail: str = "standard"


class WorkflowModeCompressionPolicy(BaseModel):
    summarization: str = "balanced"
    compress_completed_batches: bool = True
    compress_long_context: bool = True


class WorkflowModeParallelismPolicy(BaseModel):
    session_fan_out: int = 1
    provider_parallelism: int = 1
    allow_parallel_roles: bool = False


class WorkflowModeReviewPolicy(BaseModel):
    review_depth: str = "standard"
    reviewer_passes: int = 1
    builder_retry_budget: int = 0


class WorkflowModePolicy(BaseModel):
    mode: WorkflowExecutionMode = WorkflowExecutionMode.NORMAL
    label: str = "Normal"
    summary: str = "Balanced workflow mode."
    roles: list[WorkflowModeRolePolicy] = Field(default_factory=list)
    budgets: WorkflowModeBudgetPolicy = Field(default_factory=WorkflowModeBudgetPolicy)
    prompts: WorkflowModePromptPolicy = Field(default_factory=WorkflowModePromptPolicy)
    context: WorkflowModeContextPolicy = Field(default_factory=WorkflowModeContextPolicy)
    compression: WorkflowModeCompressionPolicy = Field(default_factory=WorkflowModeCompressionPolicy)
    parallelism: WorkflowModeParallelismPolicy = Field(default_factory=WorkflowModeParallelismPolicy)
    review: WorkflowModeReviewPolicy = Field(default_factory=WorkflowModeReviewPolicy)


class WorkflowModeOverride(BaseModel):
    provider: str | None = None
    model: str | None = None
    mode: str | None = None
    max_tokens: int | None = None


class WorkflowModeOverrides(BaseModel):
    role_overrides: dict[str, WorkflowModeOverride] = Field(default_factory=dict)
    job_overrides: dict[str, WorkflowModeOverride] = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# Proposal — a single unit of work proposed by the reviewer
# ---------------------------------------------------------------------------

class Proposal(BaseModel):
    """A scoped prompt or code change proposed by the reviewer for the builder."""

    id: str = Field(default_factory=lambda: "")
    batch_index: int = Field(default=0, description="Position in the workflow sequence")
    prompt: str = Field(description="The prompt sent to the builder")
    response: str | None = Field(default=None, description="Builder response")
    files_affected: list[str] = Field(default_factory=list)
    token_count: int = Field(default=0, description="Tokens used in builder response")
    approval: ApprovalStatus = Field(default=ApprovalStatus.PENDING)
    reviewer_notes: str = Field(default="")
    created_at: datetime = Field(default_factory=_now)
    resolved_at: datetime | None = Field(default=None)


# ---------------------------------------------------------------------------
# Experiment summary — high-level view of a completed workflow
# ---------------------------------------------------------------------------

class ExperimentSummary(BaseModel):
    """Snapshot summary of a completed or in-progress experiment."""

    workflow_id: str
    title: str = ""
    total_batches: int = 0
    completed_batches: int = 0
    total_tokens: int = 0
    files_changed: list[str] = Field(default_factory=list)
    started_at: datetime = Field(default_factory=_now)
    finished_at: datetime | None = None
    outcome: str = Field(default="in_progress", description="in_progress | success | failed | aborted")


# ---------------------------------------------------------------------------
# Workflow state — the root state object
# ---------------------------------------------------------------------------

class WorkflowState(BaseModel):
    """Root state for a single orchestration workflow."""

    id: str = Field(default_factory=lambda: "")
    status: WorkflowStatus = Field(default=WorkflowStatus.PENDING)
    approval_mode: ApprovalMode = Field(default=ApprovalMode.AUTO_WITH_LIMITS)
    workflow_mode: WorkflowExecutionMode = Field(default=WorkflowExecutionMode.NORMAL)
    mode_overrides: WorkflowModeOverrides = Field(default_factory=WorkflowModeOverrides)
    resolved_policy: WorkflowModePolicy = Field(default_factory=WorkflowModePolicy)
    providers: list[ProviderRole] = Field(default_factory=lambda: [
        ProviderRole(role="reviewer", provider="openai", model="gpt-4o"),
        ProviderRole(role="planner", provider="openai", model="gpt-4o"),
        ProviderRole(role="builder", provider="anthropic", model="claude-sonnet-4-20250514"),
    ])
    role_assignments: list[RoleAssignment] = Field(default_factory=list)
    proposals: list[Proposal] = Field(default_factory=list)
    summary: ExperimentSummary | None = None
    context: dict[str, Any] = Field(default_factory=dict, description="Arbitrary metadata")
    demo_events: list[DemoEvent] = Field(default_factory=list)
    result_history: list[ResultPlanningRecord] = Field(default_factory=list)
    next_step: ResultPlanningRecord | None = None
    current_stage: str = Field(default="pending")
    is_demo: bool = Field(default=False)
    created_at: datetime = Field(default_factory=_now)
    updated_at: datetime = Field(default_factory=_now)
    error: str | None = None
