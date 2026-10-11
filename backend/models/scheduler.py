"""Pydantic models for the local cron scheduler."""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


def _now() -> datetime:
    return datetime.now(timezone.utc)


class ScheduledActionType(str, Enum):
    """Whitelisted action types. No shell, no external network."""

    FIRE_SAVED_PROMPT = "fire_saved_prompt"
    RECORD_MEMORY_NOTE = "record_memory_note"
    RUN_WORKFLOW_STEP = "run_workflow_step"


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
    last_status: str | None = None     # "ok" | "error" | "skipped"
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
    status: str          # "ok" | "error" | "skipped"
    output: str = ""
