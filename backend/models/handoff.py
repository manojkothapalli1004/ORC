"""Typed Claude handoff contract models for approved builder jobs."""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum

from pydantic import BaseModel, Field

from .core import ApprovalStatus


class BuilderJobCategory(str, Enum):
    PLAN = "plan"
    BUILD = "build"
    REVIEW = "review"
    ANALYZE = "analyze"
    PROPOSAL_GENERATION = "proposal_generation"
    APPROVAL_FOLLOWUP = "approval_followup"


class ClaudeHandoffSection(BaseModel):
    key: str
    title: str
    body: str


class ClaudeWorkflowScope(BaseModel):
    workspace_root: str = "orchestrator/"
    workflow_id: str
    proposal_id: str
    proposal_batch_index: int = 0
    workflow_status: str = ""
    approval_mode: str = ""
    project_scope: str = "orchestrator only"
    files_affected: list[str] = Field(default_factory=list)
    context_summary: dict[str, str] = Field(default_factory=dict)


class ClaudeSafetyConstraints(BaseModel):
    operate_only_within_scope: bool = True
    forbid_outside_orchestrator_edits: bool = True
    forbid_screen_automation: bool = True
    forbid_arbitrary_shell_execution: bool = True
    forbid_real_dispatch: bool = True
    instructions: list[str] = Field(default_factory=lambda: [
        "Only modify files inside orchestrator/.",
        "Do not modify anything outside orchestrator/.",
        "Do not build screen automation.",
        "Do not execute arbitrary shell commands.",
        "This is a prompt-contract layer only; do not perform real dispatch.",
    ])


class ClaudeExpectedReturnFormat(BaseModel):
    format_name: str = "structured_text_contract_v1"
    instructions: list[str] = Field(default_factory=lambda: [
        "Return only the requested deliverable sections.",
        "List files changed or proposed to change.",
        "Summarize the prompt-contract output concisely.",
        "Include verification result or blocker.",
        "Stop when done.",
    ])
    sections: list[str] = Field(default_factory=lambda: [
        "files changed",
        "handoff/template paths",
        "supported job categories",
        "prompt structure produced",
        "how this plugs into the worker runtime later",
        "verification result",
    ])


class ClaudeHandoffContract(BaseModel):
    contract_version: str = "claude_handoff_v1"
    job_id: str
    workflow_id: str
    proposal_id: str
    approval_status: ApprovalStatus
    category: BuilderJobCategory
    scope: ClaudeWorkflowScope
    safety: ClaudeSafetyConstraints = Field(default_factory=ClaudeSafetyConstraints)
    return_format: ClaudeExpectedReturnFormat = Field(default_factory=ClaudeExpectedReturnFormat)
    sections: list[ClaudeHandoffSection] = Field(default_factory=list)
    prompt_text: str = ""
    worker_metadata: dict = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class BuilderJobInboxPayload(BaseModel):
    job_id: str
    workflow_id: str
    proposal_id: str
    category: BuilderJobCategory
    approval_status: ApprovalStatus
    contract: ClaudeHandoffContract
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
