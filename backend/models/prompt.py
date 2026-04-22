"""Typed Prompt OS models for reusable orchestration prompts."""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum

from pydantic import BaseModel, Field


def _now() -> datetime:
    return datetime.now(timezone.utc)


class PromptTemplateCategory(str, Enum):
    STARTUP = "startup"
    ROLE = "role"
    PARALLEL_RULE = "parallel_rule"
    SAFETY = "safety"
    RETURN_FORMAT = "return_format"
    ASSISTANT_SAVED = "assistant_saved"  # prompts saved directly from the Assistant Brain


class PromptRole(str, Enum):
    BUILDER = "builder"
    REVIEWER = "reviewer"
    VERIFIER = "verifier"
    PLANNER = "planner"


class PromptCompositionMode(str, Enum):
    COMPACT = "compact"
    NORMAL = "normal"
    RICH = "rich"


class PromptTemplate(BaseModel):
    id: str
    name: str
    category: PromptTemplateCategory
    role: PromptRole | None = None
    body: str
    variables: list[str] = Field(default_factory=list)
    variant: str = "default"
    composition_order: int = 100
    audience: str | None = None
    tags: list[str] = Field(default_factory=list)
    metadata: dict = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=_now)
    updated_at: datetime = Field(default_factory=_now)


class PromptContext(BaseModel):
    workflow_id: str = ""
    proposal_id: str = ""
    task: str = ""
    mode: PromptCompositionMode = PromptCompositionMode.COMPACT
    workflow_context: dict = Field(default_factory=dict)
    proposal_context: dict = Field(default_factory=dict)
    startup_context: dict = Field(default_factory=dict)
    safety_constraints: list[str] = Field(default_factory=list)
    parallel_session_rules: list[str] = Field(default_factory=list)
    expected_return_format: list[str] = Field(default_factory=list)
    verification_steps: list[str] = Field(default_factory=list)
    extra_sections: list[str] = Field(default_factory=list)
    template_filters: dict = Field(default_factory=dict)


class GeneratedPromptPayload(BaseModel):
    role: PromptRole
    mode: PromptCompositionMode
    template_ids: list[str] = Field(default_factory=list)
    sections: list[dict] = Field(default_factory=list)
    prompt_text: str
    token_estimate: int = 0
    created_at: datetime = Field(default_factory=_now)
    metadata: dict = Field(default_factory=dict)


class PromptPresetName(str, Enum):
    BUILDER_STANDALONE = "builder_standalone"
    BUILDER_PARALLEL = "builder_parallel"
    REVIEWER_STANDALONE = "reviewer_standalone"
    VERIFIER_STANDALONE = "verifier_standalone"
    PLANNER_STANDALONE = "planner_standalone"
    FULL_PARALLEL = "full_parallel"


class PromptPreset(BaseModel):
    name: PromptPresetName
    role: PromptRole
    mode: PromptCompositionMode
    description: str
    include_parallel_rules: bool = False
    default_task: str = ""
    default_variables: dict[str, str] = Field(default_factory=dict)
