"""Typed idea intake and discussion models."""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum

from pydantic import BaseModel, Field


def _now() -> datetime:
    return datetime.now(timezone.utc)


class IdeaThreadStatus(str, Enum):
    DRAFT = "draft"
    DISCUSSING = "discussing"
    FINALIZED = "finalized"
    CONVERTED_TO_PROPOSAL = "converted_to_proposal"


class IdeaMessageRole(str, Enum):
    USER = "user"
    SYSTEM = "system"
    ASSISTANT = "assistant"


class DiscussionMessage(BaseModel):
    id: str
    role: IdeaMessageRole = IdeaMessageRole.USER
    body: str
    created_at: datetime = Field(default_factory=_now)
    metadata: dict = Field(default_factory=dict)


class IdeaSummary(BaseModel):
    title: str = ""
    problem: str = ""
    desired_outcome: str = ""
    constraints: list[str] = Field(default_factory=list)
    proposed_scope: list[str] = Field(default_factory=list)
    notes: str = ""
    generated_at: datetime = Field(default_factory=_now)
    is_mock: bool = False


class ProposalDraft(BaseModel):
    title: str = ""
    prompt: str = ""
    rationale: str = ""
    generated_at: datetime = Field(default_factory=_now)
    is_mock: bool = False


class IdeaThread(BaseModel):
    id: str
    title: str
    status: IdeaThreadStatus = IdeaThreadStatus.DRAFT
    messages: list[DiscussionMessage] = Field(default_factory=list)
    summary: IdeaSummary | None = None
    proposal_draft: ProposalDraft | None = None
    linked_workflow_id: str | None = None
    linked_proposal_id: str | None = None
    created_at: datetime = Field(default_factory=_now)
    updated_at: datetime = Field(default_factory=_now)
    metadata: dict = Field(default_factory=dict)


class CreateIdeaRequest(BaseModel):
    title: str
    initial_note: str = ""


class AddIdeaMessageRequest(BaseModel):
    body: str
    role: IdeaMessageRole = IdeaMessageRole.USER


class FinalizeIdeaRequest(BaseModel):
    note: str = ""


class ConvertIdeaRequest(BaseModel):
    approval_mode: str = "auto_with_limits"
