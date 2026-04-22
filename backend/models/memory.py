from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

def _now() -> datetime:
    return datetime.utcnow()

class ProjectMemory(BaseModel):
    """Structured, canonical memory for the project."""

    vision: str = Field(default="", description="High-level project vision and goals")
    systems: str = Field(default="", description="Current systems and architecture")
    status: str = Field(default="", description="Current status of the work")
    decisions: str = Field(default="", description="Key decisions made and why")
    preferences: str = Field(default="", description="Operator preferences and constraints")
    known_failures: str = Field(default="", description="Known failures, dead ends, or bugs")
    roadmap: str = Field(default="", description="Next priorities and roadmap")

    updated_at: datetime = Field(default_factory=_now)

class AssistantMessage(BaseModel):
    """A message in the assistant chat."""
    role: str = Field(description="'user' or 'assistant'")
    content: str = Field(description="Message content")
    timestamp: datetime = Field(default_factory=_now)

class AssistantThread(BaseModel):
    """Persisted assistant chat history."""
    thread_id: str = Field(default="", description="Unique thread identifier")
    title: str = Field(default="", description="User-assigned title")
    messages: list[AssistantMessage] = Field(default_factory=list)
    archived: bool = Field(default=False, description="Soft-archived thread")
    created_at: datetime = Field(default_factory=_now)
    updated_at: datetime = Field(default_factory=_now)


class ThreadListItem(BaseModel):
    """Compact summary for thread listing."""
    thread_id: str
    title: str
    message_count: int
    archived: bool
    created_at: datetime
    updated_at: datetime
    preview: str = Field(default="", description="First user message snippet")


class CreateThreadRequest(BaseModel):
    title: str = Field(default="", description="Optional title for the new thread")


class RenameThreadRequest(BaseModel):
    title: str = Field(description="New title for the thread")

class AssistantChatRequest(BaseModel):
    """Request to send a message to the assistant."""
    message: str
    history: list[AssistantMessage] = Field(default_factory=list)
    system_prompt_override: str | None = None

class AssistantChatResponse(BaseModel):
    """Response from the assistant."""
    reply: str
    usage: dict[str, int] = Field(default_factory=dict)

class HandoffStatus:
    """Constants for manual operator handoff lifecycle.
    Stored as a plain string in metadata['handoff_status'] to avoid enum migration pain.
    """
    DRAFTED      = "drafted"        # saved, not yet ready
    READY        = "ready_to_send"  # operator says it's ready
    SENT         = "sent_manually"  # operator confirms it was sent

HANDOFF_STATUSES = [HandoffStatus.DRAFTED, HandoffStatus.READY, HandoffStatus.SENT]

class UpdateHandoffStatusRequest(BaseModel):
    """Set the handoff status on a saved assistant prompt."""
    status: str = Field(description="'drafted' | 'ready_to_send' | 'sent_manually'")

MEMORY_SECTION_NAMES = (
    "vision",
    "systems",
    "status",
    "decisions",
    "preferences",
    "known_failures",
    "roadmap",
)

class MemoryUpdatePatch(BaseModel):
    """One distilled, structured update to a single canonical memory section.

    Kept small and typed on purpose: the operator approves a concise delta,
    not a raw chat dump. `note` is appended under a dated header; the
    existing section body is preserved unless `replace` is true.
    """
    section: str = Field(description="One of: vision, systems, status, decisions, preferences, known_failures, roadmap")
    note: str = Field(description="Distilled update text (bullets or short paragraphs)")
    replace: bool = Field(default=False, description="If true, replace the section body instead of appending")
    source: str = Field(default="assistant_distill", description="Freeform origin tag (e.g. thread_id)")


class MemoryUpdateRequest(BaseModel):
    """Apply one or more typed distilled updates to canonical memory."""
    patches: list[MemoryUpdatePatch] = Field(default_factory=list)


class MemoryUpdateResponse(BaseModel):
    applied: list[str] = Field(default_factory=list)
    skipped: list[str] = Field(default_factory=list)
    memory: ProjectMemory


class SavePromptRequest(BaseModel):
    """Operator request to persist an assistant-generated prompt as an orchestrator record.

    If link_type + link_id are provided the prompt is also attached to that
    workflow or session record via its metadata["attached_prompts"] list.
    The standalone data/prompts/asst-*.json record is always created regardless.
    """
    content: str = Field(description="The prompt text to save")
    name: str = Field(default="", description="Short label chosen by the operator")
    source_role: str = Field(default="assistant", description="Which assistant message this came from")
    link_type: str = Field(default="", description="'workflow' | 'session' | '' for standalone only")
    link_id: str = Field(default="", description="ID of the workflow or session to attach to")
