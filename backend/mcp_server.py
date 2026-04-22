"""Minimal MCP server surface for the orchestrator V1.

Exposes read and bounded-write tools for local operator tasks.
Runs via stdio (default) for use with MCP clients like Claude Desktop.

Usage:
    uv run python -m backend.mcp_server              # stdio (default)
    uv run python -m backend.mcp_server --sse        # SSE on 127.0.0.1:8101

Tools exposed:
    READ:
      health                   — system status and provider availability
      get_project_status       — high-signal snapshot (counts + provider state + memory)
      list_workflows           — all workflow summaries
      get_workflow             — single workflow detail
      list_ideas               — all idea threads
      get_idea                 — single idea thread
      list_sessions            — all session summaries
      get_session              — single session detail with lifecycle
      preview_prompt           — read-only prompt contract for an assigned session
      get_canonical_memory     — structured project memory by section
      list_saved_prompts       — operator-saved prompts with metadata and filters
      get_saved_prompt         — single saved prompt (body + metadata)
      list_linkable_objects    — workflows / ideas / sessions a prompt can link to

    WRITE (bounded, no shell execution, no live bot control):
      create_idea              — start a new idea thread
      add_idea_note            — append a note to an idea thread
      finalize_idea            — mark an idea ready for proposal
      convert_idea             — convert a finalized idea into a workflow
      apply_memory_update      — append or replace a distilled note in a memory section
      save_prompt              — save a new prompt (assistant_saved template)
      update_prompt_status     — set handoff lifecycle status on a saved prompt
"""

from __future__ import annotations

import sys
from typing import Any

from mcp.server.fastmcp import FastMCP

from backend.config import settings
from backend.models.memory import HANDOFF_STATUSES, MEMORY_SECTION_NAMES, MemoryUpdatePatch
from backend.models.prompt import PromptTemplate, PromptTemplateCategory
from backend.models.session import SessionStatus
from backend.storage import BuilderJobStore, IdeaStore, PromptTemplateStore, SessionStore, StateStore
from backend.storage.builder_jobs import bridge_summary_from_jobs
from backend.storage.memory import MemoryStore

# ---------------------------------------------------------------------------
# Stores (module-level singletons — same pattern as routes.py)
# ---------------------------------------------------------------------------

_state_store = StateStore()
_job_store = BuilderJobStore()
_session_store = SessionStore()
_idea_store = IdeaStore()
_prompt_store = PromptTemplateStore()
_memory_store = MemoryStore()

# ---------------------------------------------------------------------------
# Server
# ---------------------------------------------------------------------------

mcp = FastMCP(
    name="orchestrator",
    instructions=(
        "Local orchestrator V1 operator tools. "
        "Read tools are always safe. "
        "Write tools create or update ideas and workflows only — "
        "no shell execution, no live bot control, no destructive actions."
    ),
    host="127.0.0.1",
    port=8101,
)


# ---------------------------------------------------------------------------
# READ tools
# ---------------------------------------------------------------------------


@mcp.tool()
def health() -> dict[str, Any]:
    """Return orchestrator system status and provider availability.

    Equivalent to GET /api/health.
    """
    from backend.providers import ProviderRegistry
    from backend.models.core import WorkflowStatus

    registry = ProviderRegistry()
    provider_summary = registry.summary()
    workflow_ids = _state_store.list_ids()
    states = [s for wid in workflow_ids if (s := _state_store.load(wid)) is not None]
    return {
        "service": "orchestrator",
        "status": "ok",
        "mock_mode": any(not p["is_live"] for p in provider_summary),
        "workflow_count": len(states),
        "active_workflow_count": sum(
            1 for s in states
            if s.status not in (WorkflowStatus.COMPLETED, WorkflowStatus.ERROR)
        ),
        "provider_roles": provider_summary,
        "storage": {
            "state_dir": str(settings.state_dir),
            "session_dir": str(settings.session_dir),
            "builder_job_dir": str(settings.builder_job_dir),
            "idea_thread_dir": str(settings.idea_thread_dir),
        },
    }


@mcp.tool()
def list_workflows() -> dict[str, Any]:
    """List all workflow summaries (id, status, stage, approval mode, proposal count).

    Equivalent to GET /api/workflows/summary.
    """
    from backend.models.core import ApprovalStatus, WorkflowStatus

    summaries = []
    for wid in _state_store.list_ids():
        state = _state_store.load(wid)
        if state is None:
            continue
        pending = sum(1 for p in state.proposals if p.approval == ApprovalStatus.PENDING)
        approved = sum(
            1 for p in state.proposals
            if p.approval in (ApprovalStatus.APPROVED, ApprovalStatus.AUTO_APPROVED)
        )
        summaries.append({
            "id": state.id,
            "status": state.status.value,
            "approval_mode": state.approval_mode.value,
            "workflow_mode": state.workflow_mode.value,
            "current_stage": state.current_stage,
            "proposal_count": len(state.proposals),
            "pending_approvals": pending,
            "approved_proposals": approved,
            "is_blocked": state.status in (WorkflowStatus.AWAITING_APPROVAL, WorkflowStatus.BLOCKED),
            "is_demo": state.is_demo,
            "created_at": state.created_at.isoformat(),
            "updated_at": state.updated_at.isoformat(),
        })
    return {"workflows": summaries, "count": len(summaries)}


@mcp.tool()
def get_workflow(workflow_id: str) -> dict[str, Any]:
    """Return full detail for a single workflow including proposals and result history.

    Equivalent to GET /api/workflows/{workflow_id}.

    Args:
        workflow_id: The workflow ID (12-char hex).
    """
    state = _state_store.load(workflow_id)
    if state is None:
        return {"error": f"Workflow not found: {workflow_id}"}
    return state.model_dump(mode="json")


@mcp.tool()
def list_ideas() -> dict[str, Any]:
    """List all idea threads with id, title, status, and message count.

    Equivalent to GET /api/ideas.
    """
    ideas = []
    for idea in _idea_store.list():
        ideas.append({
            "id": idea.id,
            "title": idea.title,
            "status": idea.status,
            "message_count": len(idea.messages),
            "linked_workflow_id": idea.linked_workflow_id or "",
            "created_at": idea.created_at.isoformat(),
            "updated_at": idea.updated_at.isoformat(),
        })
    return {"ideas": ideas, "count": len(ideas)}


@mcp.tool()
def get_idea(idea_id: str) -> dict[str, Any]:
    """Return full detail for a single idea thread including all messages.

    Equivalent to GET /api/ideas/{idea_id}.

    Args:
        idea_id: The idea ID (12-char hex).
    """
    idea = _idea_store.load(idea_id)
    if idea is None:
        return {"error": f"Idea not found: {idea_id}"}
    return idea.model_dump(mode="json")


@mcp.tool()
def list_sessions() -> dict[str, Any]:
    """List all session summaries with status, assigned job, and last activity.

    Equivalent to GET /api/sessions.
    """
    sessions = []
    for session in _session_store.list_sessions():
        sessions.append({
            "session_id": session.session_id,
            "role": session.role.value,
            "status": session.status.value,
            "assigned_job_id": session.assigned_job_id or "",
            "last_result_summary": session.last_result_summary,
            "next_expected_action": session.next_expected_action,
            "compressed": session.compressed,
            "resumable": session.resumable,
            "last_activity_at": session.last_activity_at.isoformat(),
        })
    return {
        "sessions": sessions,
        "count": len(sessions),
        "active": sum(
            1 for s in _session_store.list_sessions()
            if s.status in (
                SessionStatus.ASSIGNED,
                SessionStatus.WAITING_FOR_PROMPT_DELIVERY,
                SessionStatus.RUNNING,
                SessionStatus.WAITING_FOR_RESULT,
            )
        ),
    }


@mcp.tool()
def get_session(session_id: str) -> dict[str, Any]:
    """Return full detail for a session including lifecycle events.

    Equivalent to GET /api/sessions/{session_id}.

    Args:
        session_id: The session ID.
    """
    detail = _session_store.detail_view(session_id)
    if detail is None:
        return {"error": f"Session not found: {session_id}"}
    return detail.model_dump(mode="json")


@mcp.tool()
def preview_prompt(session_id: str) -> dict[str, Any]:
    """Return the handoff contract prompt for the job assigned to this session.

    Read-only. Does not mutate session or job state.
    Equivalent to GET /api/sessions/{session_id}/prompt-preview.

    Args:
        session_id: The session ID with an assigned job.
    """
    session = _session_store.load(session_id)
    if session is None:
        return {"error": f"Session not found: {session_id}"}
    if not session.assigned_job_id:
        return {"error": "Session has no assigned job."}
    job = _job_store.load(session.assigned_job_id)
    if job is None:
        return {"error": f"Assigned job not found: {session.assigned_job_id}"}
    if job.handoff_contract is None:
        return {"error": "Job has no handoff contract."}
    state = _state_store.load(job.workflow_id)
    prompt_text = job.handoff_contract.prompt_text
    token_estimate = (
        job.handoff_contract.worker_metadata.get("token_estimate")
        if isinstance(job.handoff_contract.worker_metadata, dict)
        else None
    ) or max(len(prompt_text) // 4, 1)
    return {
        "preview_only": True,
        "session": {
            "session_id": session.session_id,
            "role": session.role.value,
            "status": session.status.value,
        },
        "job": {
            "job_id": job.id,
            "category": job.category.value,
            "workflow_id": job.workflow_id,
            "proposal_id": job.proposal_id,
        },
        "workflow": {
            "workflow_id": state.id if state else job.workflow_id,
            "workflow_mode": state.workflow_mode.value if state else "",
            "workflow_status": state.status.value if state else "",
        },
        "prompt": {
            "prompt_text": prompt_text,
            "token_estimate": token_estimate,
            "contract_version": job.handoff_contract.contract_version,
            "expected_return_format": job.handoff_contract.return_format.instructions,
        },
    }


# ---------------------------------------------------------------------------
# WRITE tools (bounded — idea/workflow creation only)
# ---------------------------------------------------------------------------


@mcp.tool()
def create_idea(title: str, initial_note: str = "") -> dict[str, Any]:
    """Create a new idea thread.

    Equivalent to POST /api/ideas.

    Args:
        title: Short descriptive title for the idea.
        initial_note: Optional opening note or context.
    """
    idea = _idea_store.create(title=title, initial_note=initial_note)
    return {"idea_id": idea.id, "title": idea.title, "status": idea.status}


@mcp.tool()
def add_idea_note(idea_id: str, body: str, role: str = "user") -> dict[str, Any]:
    """Append a note to an existing idea thread.

    Equivalent to POST /api/ideas/{idea_id}/messages.

    Args:
        idea_id: The idea ID to append to.
        body: The note text.
        role: Message author role — "user" (default), "assistant", or "system".
    """
    idea = _idea_store.load(idea_id)
    if idea is None:
        return {"error": f"Idea not found: {idea_id}"}
    idea = _idea_store.add_message(idea_id, body=body, role=role)
    return {"idea_id": idea.id, "message_count": len(idea.messages), "status": idea.status}


@mcp.tool()
def finalize_idea(idea_id: str, note: str = "") -> dict[str, Any]:
    """Mark an idea as finalized and ready for proposal conversion.

    Equivalent to POST /api/ideas/{idea_id}/finalize.

    Args:
        idea_id: The idea ID to finalize.
        note: Optional finalization note.
    """
    idea = _idea_store.load(idea_id)
    if idea is None:
        return {"error": f"Idea not found: {idea_id}"}
    idea = _idea_store.finalize(idea_id, note=note)
    _idea_store.save(idea)
    return {"idea_id": idea.id, "status": idea.status, "finalized": True}


@mcp.tool()
def convert_idea(idea_id: str, approval_mode: str = "auto_with_limits") -> dict[str, Any]:
    """Convert a finalized idea into a workflow.

    Creates a WorkflowState linked to the idea. The workflow begins in pending/approved
    status depending on the approval mode. Does not run the workflow automatically.

    Equivalent to POST /api/ideas/{idea_id}/convert.

    Args:
        idea_id: The idea ID to convert.
        approval_mode: "auto_with_limits" (default), "full_auto", or "human".
    """
    from backend.models.core import ApprovalMode, WorkflowExecutionMode, WorkflowStatus
    from backend.workflows import WorkflowModeResolver
    import uuid

    idea = _idea_store.load(idea_id)
    if idea is None:
        return {"error": f"Idea not found: {idea_id}"}

    valid_modes = {m.value for m in ApprovalMode}
    if approval_mode not in valid_modes:
        return {"error": f"Invalid approval_mode. Valid values: {sorted(valid_modes)}"}

    resolver = WorkflowModeResolver()
    default_mode = resolver.default_mode()
    policy = resolver.resolve(default_mode)

    from backend.models.core import WorkflowState
    state = WorkflowState(id=uuid.uuid4().hex[:12])
    state.context = {
        "title": idea.title,
        "scope": "orchestrator only",
        "idea_id": idea.id,
        "proposal_draft_title": idea.proposal_draft.title if idea.proposal_draft else idea.title,
        "proposal_draft_prompt": idea.proposal_draft.prompt if idea.proposal_draft else "",
        "proposal_draft_rationale": idea.proposal_draft.rationale if idea.proposal_draft else "",
    }
    state.approval_mode = ApprovalMode(approval_mode)
    state.workflow_mode = default_mode
    state.resolved_policy = policy
    state.is_demo = False
    state.status = WorkflowStatus.PENDING
    state.current_stage = "pending"
    _state_store.save(state)

    idea = _idea_store.convert_to_proposal(idea_id)
    idea.linked_workflow_id = state.id
    idea.linked_proposal_id = f"idea-draft:{idea.id}"
    idea.metadata["approval_mode"] = approval_mode
    _idea_store.save(idea)

    return {
        "idea_id": idea.id,
        "workflow_id": state.id,
        "workflow_status": state.status.value,
        "approval_mode": approval_mode,
    }


# ---------------------------------------------------------------------------
# Memory, saved prompts, and project status
# ---------------------------------------------------------------------------


@mcp.tool()
def get_canonical_memory() -> dict[str, Any]:
    """Return structured canonical project memory, keyed by section name.

    Sections: vision, systems, status, decisions, preferences, known_failures, roadmap.
    Equivalent to GET /api/memory.
    """
    memory = _memory_store.load()
    return {
        "sections": {name: getattr(memory, name, "") or "" for name in MEMORY_SECTION_NAMES},
        "updated_at": memory.updated_at.isoformat(),
    }


@mcp.tool()
def apply_memory_update(
    section: str,
    note: str,
    replace: bool = False,
    source: str = "mcp_update",
) -> dict[str, Any]:
    """Append or replace a distilled note in a canonical memory section.

    By default the note is appended under a dated header (UTC timestamp + source).
    Set replace=true to overwrite the section body instead. Invalid section names
    are skipped rather than raising.

    Equivalent to POST /api/memory/apply-update with a single-patch body.

    Args:
        section: one of vision, systems, status, decisions, preferences,
                 known_failures, roadmap.
        note: distilled note text.
        replace: if true, overwrite the section body; otherwise append.
        source: short label for the origin of this update (default "mcp_update").
    """
    patch = MemoryUpdatePatch(
        section=section,
        note=note,
        replace=replace,
        source=(source or "mcp_update"),
    )
    memory, applied, skipped = _memory_store.apply_patches([patch])
    return {
        "applied": applied,
        "skipped": skipped,
        "sections": {name: getattr(memory, name, "") or "" for name in MEMORY_SECTION_NAMES},
        "updated_at": memory.updated_at.isoformat(),
    }


@mcp.tool()
def list_saved_prompts(
    query: str = "",
    source_role: str = "",
    link_type: str = "",
    link_id: str = "",
    sort: str = "newest",
) -> dict[str, Any]:
    """Return operator-saved prompt templates (assistant_saved category).

    Optional filters: query (case-insensitive substring on name or body),
    source_role (e.g. "assistant", "builder"), link_type+link_id pair.
    Sort: newest (default), oldest, or name.

    Equivalent to GET /api/assistant/saved-prompts.
    """
    templates = _prompt_store.by_category(PromptTemplateCategory.ASSISTANT_SAVED)
    if link_type and link_id:
        templates = [
            t for t in templates
            if t.metadata.get("link_type") == link_type
            and t.metadata.get("link_id") == link_id
        ]
    if source_role:
        templates = [
            t for t in templates
            if t.metadata.get("source_role", "assistant") == source_role
        ]
    if query:
        q = query.lower()
        templates = [t for t in templates if q in t.name.lower() or q in t.body.lower()]
    if sort == "oldest":
        templates = sorted(templates, key=lambda t: t.created_at)
    elif sort == "name":
        templates = sorted(templates, key=lambda t: t.name.lower())
    else:
        templates = sorted(templates, key=lambda t: t.created_at, reverse=True)
    return {
        "prompts": [
            {
                "id": t.id,
                "name": t.name,
                "source_role": t.metadata.get("source_role", "assistant"),
                "link_type": t.metadata.get("link_type", ""),
                "link_id": t.metadata.get("link_id", ""),
                "handoff_status": t.metadata.get("handoff_status", "drafted"),
                "created_at": t.created_at.isoformat(),
                "updated_at": t.updated_at.isoformat(),
            }
            for t in templates
        ],
        "count": len(templates),
    }


@mcp.tool()
def get_saved_prompt(prompt_id: str) -> dict[str, Any]:
    """Return a single saved prompt template with body and metadata.

    Args:
        prompt_id: the prompt template ID (e.g. "asst-abc123def4").
    """
    t = _prompt_store.load(prompt_id)
    if t is None:
        return {"error": f"Prompt not found: {prompt_id}"}
    return {
        "id": t.id,
        "name": t.name,
        "category": t.category.value,
        "body": t.body,
        "metadata": dict(t.metadata or {}),
        "source_role": t.metadata.get("source_role", "assistant"),
        "link_type": t.metadata.get("link_type", ""),
        "link_id": t.metadata.get("link_id", ""),
        "handoff_status": t.metadata.get("handoff_status", "drafted"),
        "created_at": t.created_at.isoformat(),
        "updated_at": t.updated_at.isoformat(),
    }


@mcp.tool()
def save_prompt(
    content: str,
    name: str = "",
    source_role: str = "assistant",
    link_type: str = "",
    link_id: str = "",
) -> dict[str, Any]:
    """Save a new assistant-authored prompt as a reusable template.

    If link_type + link_id are provided, a compact reference is also attached
    to that workflow (via workflow.context["attached_prompts"]) or session
    (via session.metadata["attached_prompts"]). Valid link_type values:
    "workflow", "session", or "" (standalone only).

    Equivalent to POST /api/assistant/save-prompt.

    Args:
        content: the prompt body text.
        name: optional short label ("Assistant prompt" if empty).
        source_role: short label for provenance (default "assistant").
        link_type: "workflow", "session", or "".
        link_id: target object ID when link_type is set.
    """
    import uuid

    if link_type and link_type not in ("workflow", "session"):
        return {"error": f"Invalid link_type: {link_type!r} (expected 'workflow', 'session', or empty)."}

    label = name.strip() or "Assistant prompt"
    prompt_id = f"asst-{uuid.uuid4().hex[:10]}"
    meta = {
        "source_role": source_role,
        "link_type": link_type,
        "link_id": link_id,
    }
    template = PromptTemplate(
        id=prompt_id,
        name=label,
        category=PromptTemplateCategory.ASSISTANT_SAVED,
        body=content,
        variables=[],
        variant="assistant_saved",
        audience="operator",
        metadata=meta,
    )
    saved = _prompt_store.save(template)

    linked = False
    link_record = {
        "prompt_id": prompt_id,
        "name": label,
        "created_at": saved.created_at.isoformat(),
        "source_role": source_role,
    }
    if link_type == "workflow" and link_id:
        wf = _state_store.load(link_id)
        if wf is not None:
            wf.context.setdefault("attached_prompts", []).append(link_record)
            _state_store.save(wf)
            linked = True
    elif link_type == "session" and link_id:
        sess = _session_store.load(link_id)
        if sess is not None:
            sess.metadata.setdefault("attached_prompts", []).append(link_record)
            _session_store.save(sess)
            linked = True

    return {
        "id": saved.id,
        "name": saved.name,
        "linked": linked,
        "link_type": link_type,
        "link_id": link_id,
        "created_at": saved.created_at.isoformat(),
    }


@mcp.tool()
def update_prompt_status(prompt_id: str, status: str) -> dict[str, Any]:
    """Update the handoff lifecycle status on a saved prompt.

    Valid status values: "drafted", "ready_to_send", "sent_manually".
    Equivalent to PATCH /api/assistant/saved-prompts/{id}/status.
    """
    if status not in HANDOFF_STATUSES:
        return {"error": f"Invalid status {status!r}. Valid: {list(HANDOFF_STATUSES)}"}
    t = _prompt_store.load(prompt_id)
    if t is None:
        return {"error": f"Prompt not found: {prompt_id}"}
    t.metadata["handoff_status"] = status
    _prompt_store.save(t)
    return {"id": prompt_id, "handoff_status": status}


@mcp.tool()
def list_linkable_objects(kind: str = "all") -> dict[str, Any]:
    """List objects a saved prompt can be linked to.

    Args:
        kind: "all" (default), "workflows", "ideas", or "sessions".
    """
    selected = (kind or "all").lower().strip()
    if selected not in ("all", "workflows", "ideas", "sessions"):
        return {"error": f"Invalid kind: {kind!r} (expected all|workflows|ideas|sessions)."}
    result: dict[str, Any] = {}
    if selected in ("all", "workflows"):
        items: list[dict[str, Any]] = []
        for wid in _state_store.list_ids():
            s = _state_store.load(wid)
            if s is None:
                continue
            items.append({
                "id": s.id,
                "status": s.status.value,
                "title": s.context.get("title", ""),
                "current_stage": s.current_stage,
                "updated_at": s.updated_at.isoformat(),
            })
        result["workflows"] = items
    if selected in ("all", "ideas"):
        result["ideas"] = [
            {
                "id": i.id,
                "title": i.title,
                "status": i.status,
                "linked_workflow_id": i.linked_workflow_id or "",
                "updated_at": i.updated_at.isoformat(),
            }
            for i in _idea_store.list()
        ]
    if selected in ("all", "sessions"):
        result["sessions"] = [
            {
                "session_id": s.session_id,
                "role": s.role.value,
                "status": s.status.value,
                "last_activity_at": s.last_activity_at.isoformat(),
            }
            for s in _session_store.list_sessions()
        ]
    return result


@mcp.tool()
def get_project_status() -> dict[str, Any]:
    """Return a high-signal project snapshot for the operator or Claude.

    Combines provider availability, workflow/session/idea/saved-prompt counts,
    and canonical-memory freshness into a single structured payload.
    """
    from backend.models.core import WorkflowStatus
    from backend.providers import ProviderRegistry

    registry = ProviderRegistry()
    provider_summary = registry.summary()

    workflow_ids = _state_store.list_ids()
    states = [s for wid in workflow_ids if (s := _state_store.load(wid)) is not None]
    sessions = list(_session_store.list_sessions())
    ideas = _idea_store.list()
    saved_prompt_count = len(_prompt_store.by_category(PromptTemplateCategory.ASSISTANT_SAVED))

    memory = _memory_store.load()
    populated_sections = [
        name for name in MEMORY_SECTION_NAMES
        if (getattr(memory, name, "") or "").strip()
    ]

    active_session_count = sum(
        1 for s in sessions
        if s.status in (
            SessionStatus.ASSIGNED,
            SessionStatus.WAITING_FOR_PROMPT_DELIVERY,
            SessionStatus.RUNNING,
            SessionStatus.WAITING_FOR_RESULT,
        )
    )

    return {
        "service": "orchestrator",
        "status": "ok",
        "mock_mode": any(not p["is_live"] for p in provider_summary),
        "workflows": {
            "total": len(states),
            "active": sum(
                1 for s in states
                if s.status not in (WorkflowStatus.COMPLETED, WorkflowStatus.ERROR)
            ),
            "awaiting_approval": sum(
                1 for s in states if s.status == WorkflowStatus.AWAITING_APPROVAL
            ),
        },
        "sessions": {"total": len(sessions), "active": active_session_count},
        "ideas": {"total": len(ideas)},
        "saved_prompts": {"total": saved_prompt_count},
        "memory": {
            "updated_at": memory.updated_at.isoformat(),
            "populated_sections": populated_sections,
            "section_names": list(MEMORY_SECTION_NAMES),
        },
        "provider_roles": provider_summary,
    }


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main() -> None:
    if "--sse" in sys.argv:
        mcp.run(transport="sse")
    else:
        mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
