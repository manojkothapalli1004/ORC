"""Pydantic models for the local cron scheduler."""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


def _now() -> datetime:
    return datetime.now(timezone.utc)


class ScheduledActionType(str, Enum):
    """Whitelisted action types.

    No scheduled action runs a shell command. ``run_workflow_step`` advances a
    workflow through the configured LLM providers: it can make network calls
    when any provider is configured, including a router base URL with no key,
    and a role using ``claude_code_local`` starts a local ``claude`` CLI
    process, which talks to Anthropic. With no provider configured it uses the
    mock provider. ``summarize_memory_levels`` generates derived L1/L2
    summaries of canonical memory notes through the reviewer role's provider:
    it makes network calls when that provider is configured (direct key or a
    router base URL with no key), and a reviewer role using
    ``claude_code_local`` starts a local ``claude`` CLI process for each call,
    which talks to Anthropic; with no provider configured it uses the mock
    provider and writes nothing.
    """

    FIRE_SAVED_PROMPT = "fire_saved_prompt"
    RECORD_MEMORY_NOTE = "record_memory_note"
    RUN_WORKFLOW_STEP = "run_workflow_step"
    SUMMARIZE_MEMORY_LEVELS = "summarize_memory_levels"


class ScheduledJob(BaseModel):
    id: str = ""
    name: str = ""
    cron: str = ""                     # 5-field cron expression, local time
    action_type: ScheduledActionType
    action_payload: dict[str, Any] = Field(default_factory=dict)
    enabled: bool = True
    created_at: datetime = Field(default_factory=_now)
    updated_at: datetime = Field(default_factory=_now)
    last_run_at: datetime | None = None
    last_status: str | None = None     # "ok" | "error"
    last_output: str = ""
    next_run_at: datetime | None = None


class CreateScheduledJobRequest(BaseModel):
    name: str
    cron: str
    action_type: ScheduledActionType
    action_payload: dict[str, Any] = Field(default_factory=dict)
    enabled: bool = True


class UpdateScheduledJobRequest(BaseModel):
    name: str | None = None
    cron: str | None = None
    enabled: bool | None = None
    action_payload: dict[str, Any] | None = None


class ScheduledRun(BaseModel):
    job_id: str
    fired_at: datetime
    status: str          # "ok" | "error"
    output: str = ""
