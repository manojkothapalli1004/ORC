"""REST API routes for the orchestrator control tower."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from backend.config import settings
from backend.models.builder import (
    BuilderJobDispatchRequest,
    BuilderJobResult,
    BuilderJobResultRequest,
    BuilderJobStatus,
)
from backend.models.core import ApprovalMode, ApprovalStatus, RoleAssignment, WorkflowExecutionMode, WorkflowState, WorkflowStatus
from backend.models.idea import AddIdeaMessageRequest, ConvertIdeaRequest, CreateIdeaRequest, FinalizeIdeaRequest
from backend.models.prompt import PromptCompositionMode, PromptContext, PromptPresetName, PromptRole, PromptTemplateCategory
from backend.models.results import NextStepResponse, ResultHistoryResponse, ResultIngestionRequest, ResultOutcome
from backend.models.session import (
    AssignSessionJobRequest,
    CreateSessionRequest,
    GenerateSessionHandoffRequest,
    RecordSessionResultRequest,
    SessionEventType,
    SessionStatus,
    UpdateSessionStatusRequest,
    WorkSession,
)
from backend.providers import ProviderRegistry
from backend.prompts import PRESETS, PromptComposer
from backend.storage import BuilderJobStore, IdeaStore, PromptTemplateStore, SessionStore, StateStore
from backend.storage.builder_jobs import (
    attach_result_to_workflow_state,
    bridge_summary_from_jobs,
    build_job_view,
    build_queue_summary,
    build_workflow_bridge_state,
    dispatchability_payload,
)
from backend.workflows import WorkflowEngine, WorkflowModeResolver

router = APIRouter(prefix="/api")
store = StateStore()
job_store = BuilderJobStore()
idea_store = IdeaStore()
prompt_store = PromptTemplateStore()
prompt_composer = PromptComposer(prompt_store)
session_store = SessionStore()
registry = ProviderRegistry()
mode_resolver = WorkflowModeResolver()


class CreateWorkflowRequest(BaseModel):
    context: dict = Field(default_factory=dict)
    approval_mode: str = "auto_with_limits"
    workflow_mode: str | None = None


class ApprovalRequest(BaseModel):
    approval: str
    notes: str = ""


class UpdateWorkflowModeRequest(BaseModel):
    workflow_mode: str


def _workflow_summary(state: WorkflowState) -> dict:
    pending = sum(1 for p in state.proposals if p.approval == ApprovalStatus.PENDING)
    approved = sum(
        1 for p in state.proposals if p.approval in (ApprovalStatus.APPROVED, ApprovalStatus.AUTO_APPROVED)
    )
    rejected = sum(1 for p in state.proposals if p.approval == ApprovalStatus.REJECTED)
    return {
        "id": state.id,
        "status": state.status.value,
        "approval_mode": state.approval_mode.value,
        "workflow_mode": state.workflow_mode.value,
        "mode_label": state.resolved_policy.label,
        "proposal_count": len(state.proposals),
        "pending_approvals": pending,
        "approved_proposals": approved,
        "rejected_proposals": rejected,
        "is_blocked": state.status in (WorkflowStatus.AWAITING_APPROVAL, WorkflowStatus.BLOCKED),
        "current_stage": state.current_stage,
        "is_demo": state.is_demo,
        "created_at": state.created_at.isoformat(),
        "updated_at": state.updated_at.isoformat(),
        "error": state.error,
    }


def _build_role_assignments(state: WorkflowState) -> list[RoleAssignment]:
    return [RoleAssignment(**item) for item in registry.summary(state.providers)]


def _demo_context(base: dict | None = None) -> dict:
    payload = dict(base or {})
    payload.setdefault("title", "Planner / Reviewer / Builder local demo")
    payload.setdefault("goal", "Demonstrate a safe end-to-end orchestrator loop without touching live bots.")
    payload.setdefault("scope", "orchestrator only")
    payload.setdefault("demo_safe", True)
    return payload


def _detail_payload(state: WorkflowState) -> dict:
    return {
        **state.model_dump(mode="json"),
        "summary_view": _workflow_summary(state),
    }


def _create_state(*, context: dict, approval_mode: str, workflow_mode: str | None, is_demo: bool) -> WorkflowState:
    state = store.create()
    state.context = context
    state.approval_mode = ApprovalMode(approval_mode)
    state.workflow_mode = WorkflowExecutionMode(workflow_mode or mode_resolver.default_mode().value)
    state.resolved_policy = mode_resolver.resolve(state.workflow_mode, overrides=state.mode_overrides)
    state.providers = mode_resolver.provider_roles_for(state.resolved_policy, overrides=state.mode_overrides)
    state.role_assignments = _build_role_assignments(state)
    state.is_demo = is_demo
    state.status = WorkflowStatus.PENDING
    state.current_stage = "pending"
    store.save(state)
    return state


def _engine_for(state: WorkflowState) -> WorkflowEngine:
    providers = registry.from_workflow(state)
    return WorkflowEngine(
        reviewer=providers["reviewer"],
        planner=providers.get("planner", providers["reviewer"]),
        builder=providers["builder"],
        store=store,
    )


async def _run_demo_loop(state: WorkflowState) -> WorkflowState:
    engine = _engine_for(state)
    state = await engine.step(state)
    return store.load(state.id) or state


def _config_summary() -> dict:
    return {
        "server": {
            "host": settings.host,
            "port": settings.port,
            "local_network_only": settings.local_network_only,
        },
        "approval_limits": {
            "max_files_per_batch": settings.max_files_per_batch,
            "max_tokens_per_response": settings.max_tokens_per_response,
        },
        "storage": {
            "state_dir": str(settings.state_dir),
            "log_dir": str(settings.log_dir),
            "builder_job_dir": str(settings.builder_job_dir),
            "session_dir": str(settings.session_dir),
            "idea_thread_dir": str(settings.idea_thread_dir),
        },
        "provider_defaults": {
            "reviewer": {"provider": settings.reviewer_provider, "mode": settings.reviewer_mode, "model": settings.reviewer_model},
            "planner": {"provider": settings.planner_provider, "mode": settings.planner_mode, "model": settings.planner_model},
            "builder": {"provider": settings.builder_provider, "mode": settings.builder_mode, "model": settings.builder_model},
        },
        "workflow_modes": {
            "default": mode_resolver.default_mode().value,
            "available": mode_resolver.available_modes(),
            "resolved_defaults": {
                mode.value: mode_resolver.summary_payload(mode_resolver.resolve(mode))
                for mode in WorkflowExecutionMode
            },
        },
        "router": {
            "base_url": settings.router_base_url,
            "provider_name": settings.router_provider_name,
            "timeout_seconds": settings.router_timeout_seconds,
        },
    }


def _system_status() -> dict:
    workflow_ids = store.list_ids()
    states = [store.load(wid) for wid in workflow_ids]
    valid_states = [state for state in states if state is not None]
    invalid_count = len(workflow_ids) - len(valid_states)
    provider_summary = registry.summary()
    return {
        "service": "orchestrator",
        "status": "ok",
        "mock_mode": any(not item["is_live"] for item in provider_summary),
        "workflow_count": len(valid_states),
        "invalid_state_count": invalid_count,
        "active_workflow_count": sum(
            1 for state in valid_states if state.status not in (WorkflowStatus.COMPLETED, WorkflowStatus.ERROR)
        ),
        "provider_roles": provider_summary,
    }


def _bridge_summary() -> dict:
    return bridge_summary_from_jobs(job_store.list_jobs(), job_store)


def _job_payload(job) -> dict:
    return build_job_view(job)


def _find_workflow_or_404(workflow_id: str) -> WorkflowState:
    state = store.load(workflow_id)
    if state is None:
        raise HTTPException(404, "Workflow not found")
    return state


def _find_job_or_404(job_id: str):
    job = job_store.load(job_id)
    if job is None:
        raise HTTPException(404, "Builder job not found")
    return job


def _find_session_or_404(session_id: str) -> WorkSession:
    session = session_store.load(session_id)
    if session is None:
        raise HTTPException(404, "Session not found")
    return session


def _find_idea_or_404(idea_id: str):
    idea = idea_store.load(idea_id)
    if idea is None:
        raise HTTPException(404, "Idea thread not found")
    return idea


def _find_proposal_or_404(state: WorkflowState, proposal_id: str):
    for proposal in state.proposals:
        if proposal.id == proposal_id:
            return proposal
    raise HTTPException(404, "Proposal not found")


def _attach_job_result_to_workflow(state: WorkflowState, job) -> None:
    state.context.setdefault("builder_bridge", {})[job.id] = build_workflow_bridge_state(job)
    state.current_stage = f"builder_job_{job.status.value}"
    if job.status == BuilderJobStatus.FAILED:
        state.status = WorkflowStatus.ERROR
        state.error = (job.result.error if job.result else None) or "Builder job failed"
    elif job.status == BuilderJobStatus.COMPLETED:
        state.status = WorkflowStatus.APPROVED
        state.error = None
    store.save(state)


@router.post("/demo/workflows")
async def create_demo_workflow(req: CreateWorkflowRequest):
    state = _create_state(
        context=_demo_context(req.context),
        approval_mode=req.approval_mode,
        workflow_mode=req.workflow_mode,
        is_demo=True,
    )
    return _detail_payload(state)


@router.post("/demo/workflows/{workflow_id}/run")
async def run_demo_workflow(workflow_id: str):
    state = _find_workflow_or_404(workflow_id)
    state.is_demo = True
    if not state.context:
        state.context = _demo_context({})
    state.resolved_policy = mode_resolver.resolve(state.workflow_mode, overrides=state.mode_overrides)
    state.providers = mode_resolver.provider_roles_for(state.resolved_policy, overrides=state.mode_overrides)
    state.role_assignments = _build_role_assignments(state)
    store.save(state)
    state = await _run_demo_loop(state)
    return _detail_payload(state)


@router.get("/demo/workflows/{workflow_id}")
async def get_demo_workflow(workflow_id: str):
    state = _find_workflow_or_404(workflow_id)
    return _detail_payload(state)


@router.get("/demo/workflows")
async def list_demo_workflows():
    workflows = []
    for wid in store.list_ids():
        state = store.load(wid)
        if state and state.is_demo:
            workflows.append(_workflow_summary(state))
    return {"workflows": workflows}


@router.post("/demo/run")
async def create_and_run_demo(req: CreateWorkflowRequest):
    state = _create_state(
        context=_demo_context(req.context),
        approval_mode=req.approval_mode,
        workflow_mode=req.workflow_mode,
        is_demo=True,
    )
    state = await _run_demo_loop(state)
    return _detail_payload(state)


@router.get("/health")
async def health():
    return _system_status()


@router.get("/config")
async def config_status():
    return _config_summary()


@router.get("/providers")
async def provider_role_summary():
    return {"providers": registry.summary()}


@router.get("/workflows/summary")
async def workflow_state_summary():
    summaries = []
    for wid in store.list_ids():
        state = store.load(wid)
        if state:
            summaries.append(_workflow_summary(state))
    return {"workflows": summaries, "count": len(summaries)}


@router.get("/workflows")
async def list_workflows():
    workflows = []
    for wid in store.list_ids():
        state = store.load(wid)
        if state:
            workflows.append(_workflow_summary(state))
    return {"workflows": workflows}


@router.post("/workflows")
async def create_workflow(req: CreateWorkflowRequest):
    state = _create_state(
        context=req.context,
        approval_mode=req.approval_mode,
        workflow_mode=req.workflow_mode,
        is_demo=False,
    )
    return {"id": state.id, "status": state.status.value}


@router.get("/workflows/{workflow_id}")
async def get_workflow(workflow_id: str):
    state = _find_workflow_or_404(workflow_id)
    return _detail_payload(state)


@router.delete("/workflows/{workflow_id}")
async def delete_workflow(workflow_id: str):
    if not store.delete(workflow_id):
        raise HTTPException(404, "Workflow not found")
    return {"deleted": True}


@router.post("/workflows/{workflow_id}/step")
async def step_workflow(workflow_id: str):
    state = _find_workflow_or_404(workflow_id)
    engine = _engine_for(state)
    state = await engine.step(state)
    return _detail_payload(state)


@router.post("/workflows/{workflow_id}/proposals/{proposal_id}/approve")
async def approve_proposal(workflow_id: str, proposal_id: str, req: ApprovalRequest):
    state = _find_workflow_or_404(workflow_id)
    proposal = _find_proposal_or_404(state, proposal_id)
    proposal.approval = ApprovalStatus(req.approval)
    proposal.reviewer_notes = req.notes

    pending = [p for p in state.proposals if p.approval == ApprovalStatus.PENDING]
    if not pending and state.status in (WorkflowStatus.AWAITING_APPROVAL, WorkflowStatus.BLOCKED):
        state.status = WorkflowStatus.APPROVED

    store.save(state)
    return {"status": state.status.value, "proposal": proposal_id}


@router.get("/builder-bridge")
async def builder_bridge_status():
    return {"bridge": _bridge_summary()}


@router.get("/ideas")
async def list_ideas():
    ideas = [item.model_dump(mode="json") for item in idea_store.list()]
    return {"ideas": ideas, "count": len(ideas)}


@router.get("/prompts/templates")
async def list_prompt_templates():
    templates = [item.model_dump(mode="json") for item in prompt_store.list()]
    return {"templates": templates, "count": len(templates)}


@router.get("/prompts/templates/by-category/{category}")
async def list_prompt_templates_by_category(category: str):
    try:
        prompt_category = PromptTemplateCategory(category)
    except ValueError as exc:
        raise HTTPException(400, f"Unknown prompt template category: {category}") from exc
    templates = [item.model_dump(mode="json") for item in prompt_store.by_category(prompt_category)]
    return {"templates": templates, "count": len(templates), "category": prompt_category.value}


@router.get("/prompts/templates/by-role/{role}")
async def list_prompt_templates_by_role(role: str):
    try:
        prompt_role = PromptRole(role)
    except ValueError as exc:
        raise HTTPException(400, f"Unknown prompt role: {role}") from exc
    templates = [item.model_dump(mode="json") for item in prompt_store.by_role(prompt_role)]
    return {"templates": templates, "count": len(templates), "role": prompt_role.value}


@router.get("/prompts/templates/{template_id}")
async def get_prompt_template(template_id: str):
    template = prompt_store.get(template_id)
    if template is None:
        raise HTTPException(404, "Prompt template not found")
    return {"template": template.model_dump(mode="json")}


def _prompt_generation_context(
    *,
    mode: str,
    workflow_id: str = "",
    proposal_id: str = "",
    include_parallel_rules: bool = True,
) -> PromptContext:
    prompt_mode = PromptCompositionMode(mode)
    workflow_context = {}
    proposal_context = {}
    startup_context = {"composition_mode": prompt_mode.value}
    if workflow_id:
        state = _find_workflow_or_404(workflow_id)
        workflow_context = {"status": state.status.value, **state.context}
        startup_context["workflow_mode"] = state.workflow_mode.value
        if proposal_id:
            proposal = _find_proposal_or_404(state, proposal_id)
            proposal_context = {
                "prompt": proposal.prompt,
                "approval": proposal.approval.value,
                "files_affected": ", ".join(proposal.files_affected),
            }
    return PromptContext(
        workflow_id=workflow_id,
        proposal_id=proposal_id,
        mode=prompt_mode,
        workflow_context=workflow_context,
        proposal_context=proposal_context,
        startup_context=startup_context,
        parallel_session_rules=[
            "Work only within your assigned scope.",
            "Do not overwrite unrelated files.",
            "Keep shared-file edits minimal and explicit.",
        ] if include_parallel_rules else [],
        safety_constraints=[
            "Only operate within orchestrator/.",
            "Do not add live execution behavior.",
            "Do not use arbitrary shell commands.",
        ],
        expected_return_format=[
            "Return only requested scope.",
            "List changed or inspected files.",
            "Stop when done.",
        ],
    )


@router.get("/prompts/generate/startup")
async def generate_startup_prompt(mode: str = "compact", workflow_id: str = "", proposal_id: str = "", include_parallel_rules: bool = True):
    payload = prompt_composer.compose(
        role=PromptRole.PLANNER,
        context=_prompt_generation_context(
            mode=mode,
            workflow_id=workflow_id,
            proposal_id=proposal_id,
            include_parallel_rules=include_parallel_rules,
        ),
    )
    payload.metadata["prompt_kind"] = "startup"
    return payload.model_dump(mode="json")


@router.get("/prompts/generate/{role}")
async def generate_prompt(role: str, mode: str = "compact", workflow_id: str = "", proposal_id: str = "", include_parallel_rules: bool = True):
    prompt_role = PromptRole(role)
    payload = prompt_composer.compose(
        role=prompt_role,
        context=_prompt_generation_context(
            mode=mode,
            workflow_id=workflow_id,
            proposal_id=proposal_id,
            include_parallel_rules=include_parallel_rules,
        ),
    )
    payload.metadata["prompt_kind"] = "role"
    return payload.model_dump(mode="json")


@router.get("/prompts/presets")
async def list_prompt_presets():
    presets = [
        {"name": p.name.value, "role": p.role.value, "mode": p.mode.value, "description": p.description, "include_parallel_rules": p.include_parallel_rules}
        for p in PRESETS.values()
    ]
    return {"presets": presets, "count": len(presets)}


@router.get("/prompts/presets/{name}/generate")
async def generate_preset_prompt(name: str, request_vars: str = ""):
    try:
        preset_name = PromptPresetName(name)
    except ValueError as exc:
        raise HTTPException(400, f"Unknown preset: {name}") from exc
    variables: dict[str, str] = {}
    if request_vars:
        for pair in request_vars.split(","):
            if "=" in pair:
                k, _, v = pair.partition("=")
                variables[k.strip()] = v.strip()
    payload = prompt_composer.compose_preset(preset_name, variables=variables)
    return payload.model_dump(mode="json")


@router.post("/ideas")
async def create_idea(req: CreateIdeaRequest):
    idea = idea_store.create(title=req.title, initial_note=req.initial_note)
    return {"idea": idea.model_dump(mode="json")}


@router.get("/ideas/{idea_id}")
async def get_idea(idea_id: str):
    idea = _find_idea_or_404(idea_id)
    return {"idea": idea.model_dump(mode="json")}


@router.post("/ideas/{idea_id}/messages")
async def add_idea_message(idea_id: str, req: AddIdeaMessageRequest):
    idea = idea_store.add_message(idea_id, body=req.body, role=req.role)
    return {"idea": idea.model_dump(mode="json")}


@router.post("/ideas/{idea_id}/finalize")
async def finalize_idea(idea_id: str, req: FinalizeIdeaRequest):
    idea = idea_store.finalize(idea_id, note=req.note)
    idea.metadata["finalized"] = True
    idea_store.save(idea)
    return {"idea": idea.model_dump(mode="json")}


@router.post("/ideas/{idea_id}/convert")
async def convert_idea_to_proposal(idea_id: str, req: ConvertIdeaRequest):
    idea = idea_store.convert_to_proposal(idea_id)
    workflow = _create_state(
        context={
            "title": idea.title,
            "scope": "orchestrator only",
            "idea_id": idea.id,
            "proposal_draft_title": idea.proposal_draft.title if idea.proposal_draft else idea.title,
            "proposal_draft_prompt": idea.proposal_draft.prompt if idea.proposal_draft else "",
            "proposal_draft_rationale": idea.proposal_draft.rationale if idea.proposal_draft else "",
        },
        approval_mode=req.approval_mode,
        workflow_mode=None,
        is_demo=False,
    )
    idea.linked_workflow_id = workflow.id
    idea.linked_proposal_id = f"idea-draft:{idea.id}"
    idea.metadata["approval_mode"] = req.approval_mode
    idea_store.save(idea)
    return {
        "idea": idea.model_dump(mode="json"),
        "workflow": {"id": workflow.id, "status": workflow.status.value},
    }


@router.get("/sessions")
async def list_sessions():
    return session_store.build_registry_view().model_dump(mode="json")


@router.post("/sessions")
async def create_session(req: CreateSessionRequest):
    session = session_store.create(
        WorkSession(
            session_id=req.session_id,
            role=req.role,
            next_expected_action=req.next_expected_action,
            metadata=req.metadata,
        )
    )
    detail = session_store.detail_view(session.session_id)
    return {"session": detail.model_dump(mode="json") if detail else session.model_dump(mode="json")}


@router.get("/sessions/resumable")
async def list_resumable_sessions():
    views = session_store.list_resumable()
    return {"resumable": [v.model_dump(mode="json") for v in views], "count": len(views)}


@router.get("/sessions/{session_id}")
async def get_session(session_id: str):
    detail = session_store.detail_view(session_id)
    if detail is None:
        raise HTTPException(404, "Session not found")
    return {"session": detail.model_dump(mode="json")}


@router.post("/sessions/{session_id}/assign")
async def assign_session_job(session_id: str, req: AssignSessionJobRequest):
    session = _find_session_or_404(session_id)
    _find_job_or_404(req.job_id)
    active_assignment_states = {
        SessionStatus.ASSIGNED,
        SessionStatus.WAITING_FOR_PROMPT_DELIVERY,
        SessionStatus.RUNNING,
        SessionStatus.WAITING_FOR_RESULT,
    }
    duplicate = next(
        (
            item for item in session_store.list_sessions()
            if item.session_id != session_id
            and item.assigned_job_id == req.job_id
            and item.status in active_assignment_states
        ),
        None,
    )
    if duplicate is not None:
        raise HTTPException(400, f"Job {req.job_id} is already actively assigned to session {duplicate.session_id}")
    try:
        session.assign_job(req.job_id, req.next_expected_action)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    session_store.save(session)
    detail = session_store.detail_view(session_id)
    return {"session": detail.model_dump(mode="json") if detail else session.model_dump(mode="json")}


@router.get("/sessions/{session_id}/prompt-preview")
async def get_session_prompt_preview(session_id: str):
    session = _find_session_or_404(session_id)
    if not session.assigned_job_id:
        raise HTTPException(400, "Session has no assigned job to preview")
    job = _find_job_or_404(session.assigned_job_id)
    if job.handoff_contract is None:
        raise HTTPException(400, "Assigned job has no handoff contract to preview")
    workflow = _find_workflow_or_404(job.workflow_id)
    prompt_text = job.handoff_contract.prompt_text
    token_estimate = job.handoff_contract.worker_metadata.get("token_estimate") if isinstance(job.handoff_contract.worker_metadata, dict) else None
    if not token_estimate:
        token_estimate = max(len(prompt_text) // 4, 1)
    return {
        "preview_only": True,
        "session": {
            "session_id": session.session_id,
            "role": session.role.value,
            "status": session.status.value,
            "next_expected_action": session.next_expected_action,
        },
        "job": {
            "job_id": job.id,
            "category": job.category.value,
            "workflow_id": job.workflow_id,
            "proposal_id": job.proposal_id,
            "proposal_batch_index": job.proposal_batch_index,
        },
        "workflow": {
            "workflow_id": workflow.id,
            "workflow_mode": workflow.workflow_mode.value,
            "workflow_status": workflow.status.value,
        },
        "prompt": {
            "prompt_text": prompt_text,
            "token_estimate": token_estimate,
            "contract_version": job.handoff_contract.contract_version,
            "consumer": job.handoff_contract.worker_metadata.get("consumer", "local_claude_antigravity_handoff") if isinstance(job.handoff_contract.worker_metadata, dict) else "local_claude_antigravity_handoff",
            "expected_return_format": job.handoff_contract.return_format.instructions,
        },
    }


class MarkDeliveredRequest(BaseModel):
    operator: str = "operator"
    note: str = ""


@router.post("/sessions/{session_id}/mark-delivered")
async def mark_session_prompt_delivered(session_id: str, req: MarkDeliveredRequest):
    session = _find_session_or_404(session_id)
    if not session.assigned_job_id:
        raise HTTPException(400, "Session has no assigned job")
    job = _find_job_or_404(session.assigned_job_id)
    if job.handoff_contract is None:
        raise HTTPException(400, "Assigned job has no handoff contract")
    prompt_text = job.handoff_contract.prompt_text
    token_estimate = max(len(prompt_text) // 4, 1)
    try:
        session.update_status(
            SessionStatus.WAITING_FOR_RESULT,
            next_expected_action="Prompt delivered manually. Waiting for session result.",
        )
        session.append_event(
            event_type=SessionEventType.PROMPT_DELIVERED,
            note=req.note or f"Prompt delivered manually by {req.operator}.",
            metadata={
                "delivered_by": req.operator,
                "job_id": job.id,
                "workflow_id": job.workflow_id,
                "proposal_id": job.proposal_id,
                "token_estimate": token_estimate,
            },
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    session_store.save(session)
    detail = session_store.detail_view(session_id)
    return {
        "session": detail.model_dump(mode="json") if detail else session.model_dump(mode="json"),
        "delivery": {
            "delivered": True,
            "delivered_by": req.operator,
            "session_id": session.session_id,
            "job_id": job.id,
            "token_estimate": token_estimate,
            "status": session.status.value,
            "next_expected_action": session.next_expected_action,
        },
    }


@router.post("/sessions/{session_id}/status")
async def update_session_status(session_id: str, req: UpdateSessionStatusRequest):
    session = _find_session_or_404(session_id)
    try:
        session.update_status(req.status, req.next_expected_action, req.metadata)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    session_store.save(session)
    detail = session_store.detail_view(session_id)
    return {"session": detail.model_dump(mode="json") if detail else session.model_dump(mode="json")}


@router.post("/sessions/{session_id}/result")
async def record_session_result(session_id: str, req: RecordSessionResultRequest):
    session = _find_session_or_404(session_id)
    if session.status not in {
        SessionStatus.RUNNING,
        SessionStatus.WAITING_FOR_RESULT,
    }:
        raise HTTPException(400, "Session result can only be recorded from waiting_for_result or running")

    session_metadata = {
        **req.metadata,
        "outcome": req.outcome.value,
        "notes": req.notes,
        "output_ref": req.output_ref,
        "artifacts": req.artifacts,
        "manual_result_entry": True,
    }
    session.record_result(req.last_result_summary, req.next_expected_action, session_metadata)

    planning_record = None
    if session.assigned_job_id:
        job = _find_job_or_404(session.assigned_job_id)
        ingestion_request = ResultIngestionRequest(
            source="manual_session_entry",
            workflow_id=job.workflow_id,
            proposal_id=job.proposal_id,
            job_id=job.id,
            session_id=session.session_id,
            summary=req.last_result_summary,
            output_ref=req.output_ref,
            details=req.notes,
            artifacts=req.artifacts,
            error=req.notes if req.outcome == ResultOutcome.FAILED else None,
            metadata=session_metadata,
        )
        planning_record = job_store.ingest_result_request(job.id, ingestion_request).next_step
        state = store.load(job.workflow_id)
        if state is not None and planning_record is not None:
            attach_result_to_workflow_state(state, planning_record)
            store.save(state)

    target_status = SessionStatus.WAITING_FOR_RESULT
    if req.outcome == ResultOutcome.SUCCESS:
        target_status = SessionStatus.COMPLETED
    elif req.outcome == ResultOutcome.BLOCKED:
        target_status = SessionStatus.BLOCKED
    elif req.outcome == ResultOutcome.FAILED:
        target_status = SessionStatus.FAILED

    try:
        session.update_status(target_status)
    except ValueError:
        session.status = target_status
        session.append_event(
            event_type=SessionEventType.STATUS_UPDATED,
            note="Session status updated.",
            metadata={"status": target_status.value, "manual_result_entry": True},
        )

    session_store.save(session)
    detail = session_store.detail_view(session_id)
    return {
        "session": detail.model_dump(mode="json") if detail else session.model_dump(mode="json"),
        "linked": {
            "job_id": session.assigned_job_id,
            "workflow_id": _find_job_or_404(session.assigned_job_id).workflow_id if session.assigned_job_id else "",
            "next_step": planning_record.model_dump(mode="json") if planning_record else None,
        },
    }


@router.get("/builder-jobs")
async def list_builder_jobs():
    return {
        "jobs": [_job_payload(job) for job in job_store.list_jobs()],
        "bridge": _bridge_summary(),
    }


@router.get("/builder-jobs/queue")
async def builder_job_queue_summary():
    jobs = job_store.list_jobs()
    summary = build_queue_summary(jobs, job_store)
    return summary.model_dump(mode="json")


@router.get("/builder-jobs/{job_id}")
async def get_builder_job(job_id: str):
    job = _find_job_or_404(job_id)
    return {"job": _job_payload(job), "bridge": _bridge_summary()}


@router.get("/workflows/{workflow_id}/builder-jobs")
async def list_workflow_builder_jobs(workflow_id: str):
    _find_workflow_or_404(workflow_id)
    jobs = [_job_payload(job) for job in job_store.list_jobs() if job.workflow_id == workflow_id]
    return {"workflow_id": workflow_id, "jobs": jobs, "bridge": _bridge_summary()}


@router.get("/workflows/{workflow_id}/proposals/{proposal_id}/dispatchability")
async def proposal_dispatchability(workflow_id: str, proposal_id: str):
    state = _find_workflow_or_404(workflow_id)
    proposal = _find_proposal_or_404(state, proposal_id)
    dispatchable = proposal.approval in (ApprovalStatus.APPROVED, ApprovalStatus.AUTO_APPROVED)
    return {
        **dispatchability_payload(
            workflow_id=workflow_id,
            proposal_id=proposal_id,
            approval=proposal.approval,
            dispatchable=dispatchable,
        ),
        "bridge": _bridge_summary(),
    }


@router.post("/workflows/{workflow_id}/builder-jobs")
async def dispatch_builder_job(workflow_id: str, req: BuilderJobDispatchRequest):
    state = _find_workflow_or_404(workflow_id)
    proposal = _find_proposal_or_404(state, req.proposal_id)
    if proposal.approval not in (ApprovalStatus.APPROVED, ApprovalStatus.AUTO_APPROVED):
        raise HTTPException(400, "Proposal must be approved before dispatch")
    job = job_store.create_from_proposal(state, proposal, category=req.category)
    state.current_stage = "builder_job_pending"
    state.context.setdefault("builder_bridge", {})[job.id] = build_workflow_bridge_state(job)
    store.save(state)
    return {
        "job": _job_payload(job),
        "accepted": {
            "job_id": job.id,
            "accepted": True,
            "category": job.category.value,
            "inbox_path": job.inbox_path,
            "contract_version": "claude_handoff_v1",
            "consumer": _bridge_summary()["consumer"],
            "expected_return_format": _bridge_summary()["expected_return_format"],
            "handoff_contract": job.handoff_contract.model_dump(mode="json") if job.handoff_contract else None,
        },
        "bridge": _bridge_summary(),
    }


@router.post("/builder-jobs/{job_id}/result")
async def mark_builder_job_result(job_id: str, req: BuilderJobResultRequest):
    result = BuilderJobResult(
        status=req.status,
        summary=req.summary,
        output_ref=req.output_ref,
        artifacts=req.artifacts,
        error=req.error,
        metadata=req.metadata,
    )
    job = job_store.mark_result(job_id, result)
    state = store.load(job.workflow_id)
    if state is not None:
        _attach_job_result_to_workflow(state, job)
    return {"job": _job_payload(job), "bridge": _bridge_summary()}


@router.post("/results")
async def ingest_result(req: ResultIngestionRequest):
    if req.job_id:
        job = job_store.ingest_result_request(req.job_id, req)
        state = store.load(req.workflow_id)
        if state is not None and job.next_step is not None:
            attach_result_to_workflow_state(state, job.next_step)
            store.save(state)
        if req.session_id:
            session = session_store.load(req.session_id)
            if session is not None and job.next_step is not None:
                session.record_result(
                    req.summary,
                    job.next_step.suggestion.reason,
                    {
                        **req.metadata,
                        "last_outcome": job.next_step.ingestion.outcome.value,
                        "last_next_action": job.next_step.suggestion.action.value,
                    },
                )
                target_status = SessionStatus.WAITING_FOR_RESULT
                if job.next_step.ingestion.outcome.value == "success":
                    target_status = SessionStatus.COMPLETED
                elif job.next_step.ingestion.outcome.value == "blocked":
                    target_status = SessionStatus.BLOCKED
                elif job.next_step.ingestion.outcome.value == "failed":
                    target_status = SessionStatus.FAILED
                try:
                    session.update_status(target_status)
                except ValueError:
                    session.status = target_status
                session_store.save(session)
        return {"job": _job_payload(job), "next_step": job.next_step.model_dump(mode="json") if job.next_step else None}

    state = _find_workflow_or_404(req.workflow_id)
    from backend.planning import build_planning_record
    record = build_planning_record(req)
    attach_result_to_workflow_state(state, record)
    store.save(state)
    return {"workflow_id": state.id, "next_step": record.model_dump(mode="json")}


@router.get("/workflows/{workflow_id}/results")
async def workflow_result_history(workflow_id: str):
    _find_workflow_or_404(workflow_id)
    return ResultHistoryResponse.model_validate(job_store.workflow_history_payload(workflow_id)).model_dump(mode="json")


@router.get("/builder-jobs/{job_id}/results")
async def job_result_history(job_id: str):
    job = _find_job_or_404(job_id)
    return ResultHistoryResponse(
        workflow_id=job.workflow_id,
        job_id=job.id,
        results=job_store.result_history(job_id),
    ).model_dump(mode="json")


@router.get("/workflows/{workflow_id}/next-step")
async def workflow_next_step(workflow_id: str):
    _find_workflow_or_404(workflow_id)
    return NextStepResponse.model_validate(job_store.workflow_next_step_payload(workflow_id)).model_dump(mode="json")


@router.get("/builder-jobs/{job_id}/next-step")
async def job_next_step(job_id: str):
    job = _find_job_or_404(job_id)
    record = job_store.suggested_next_step(job_id)
    return NextStepResponse(
        workflow_id=job.workflow_id,
        job_id=job.id,
        suggestion=record.suggestion if record else None,
    ).model_dump(mode="json")


# ---------------------------------------------------------------------------
# Workflow execution modes
# ---------------------------------------------------------------------------

@router.get("/modes/active-default")
async def active_default_mode():
    default = mode_resolver.default_mode()
    policy = mode_resolver.resolve(default)
    return {
        "mode": default.value,
        "label": policy.label,
        "summary": policy.summary,
        "policy": mode_resolver.summary_payload(policy),
    }


@router.get("/modes/{mode_value}")
async def get_mode(mode_value: str):
    try:
        target = WorkflowExecutionMode(mode_value)
    except ValueError:
        raise HTTPException(404, f"Unknown mode: {mode_value}")
    policy = mode_resolver.resolve(target)
    return {
        "mode": target.value,
        "label": policy.label,
        "summary": policy.summary,
        "policy": mode_resolver.summary_payload(policy),
    }


@router.get("/modes")
async def list_modes():
    modes = []
    for m in WorkflowExecutionMode:
        policy = mode_resolver.resolve(m)
        modes.append({
            "mode": m.value,
            "label": policy.label,
            "summary": policy.summary,
            "policy": mode_resolver.summary_payload(policy),
        })
    return {
        "modes": modes,
        "default": mode_resolver.default_mode().value,
        "count": len(modes),
    }


@router.patch("/workflows/{workflow_id}/mode")
async def update_workflow_mode(workflow_id: str, req: UpdateWorkflowModeRequest):
    state = _find_workflow_or_404(workflow_id)
    try:
        new_mode = WorkflowExecutionMode(req.workflow_mode)
    except ValueError:
        raise HTTPException(400, f"Unknown mode: {req.workflow_mode}")
    state.workflow_mode = new_mode
    state.resolved_policy = mode_resolver.resolve(new_mode, overrides=state.mode_overrides)
    state.providers = mode_resolver.provider_roles_for(state.resolved_policy, overrides=state.mode_overrides)
    state.role_assignments = _build_role_assignments(state)
    store.save(state)
    return _detail_payload(state)


# ---------------------------------------------------------------------------
# Session handoff / compression
# ---------------------------------------------------------------------------


@router.post("/sessions/{session_id}/handoff")
async def generate_session_handoff(session_id: str, req: GenerateSessionHandoffRequest):
    view = session_store.generate_handoff(session_id, req)
    if view is None:
        raise HTTPException(404, f"Session not found: {session_id}")
    return {"handoff": view.model_dump(mode="json")}


@router.get("/sessions/{session_id}/handoff")
async def get_session_handoff(session_id: str):
    session = _find_session_or_404(session_id)
    if session.handoff_summary is None:
        raise HTTPException(404, f"No handoff generated yet for session: {session_id}")
    return {
        "handoff": {
            "session_id": session.session_id,
            "resumable": session.resumable,
            "compressed": session.compressed,
            "compression_mode": session.compression_mode.value if session.compression_mode else None,
            "summary": session.handoff_summary.model_dump(mode="json"),
            "restart_prompt": session.restart_prompt.model_dump(mode="json") if session.restart_prompt else None,
            "updated_at": session.updated_at.isoformat(),
        }
    }

# ------------------------------------------------------------------
# Project Memory & Assistant Brain
# ------------------------------------------------------------------

from backend.models.memory import (
    ProjectMemory, AssistantChatRequest, AssistantChatResponse,
    AssistantThread, AssistantMessage, ThreadListItem,
    CreateThreadRequest, RenameThreadRequest,
    SavePromptRequest, UpdateHandoffStatusRequest, HandoffStatus, HANDOFF_STATUSES,
    MemoryUpdateRequest, MemoryUpdateResponse, MEMORY_SECTION_NAMES,
)
from backend.storage.memory import MemoryStore, ThreadStore
from backend.services.assistant import AssistantService
from backend.providers.claude_code_local_provider import (
    ClaudeCodeLocalAuthError,
    ClaudeCodeLocalProvider,
)

memory_store = MemoryStore()
thread_store = ThreadStore()
assistant_svc = AssistantService()

@router.get("/memory", response_model=ProjectMemory)
async def get_project_memory():
    return memory_store.load()

@router.put("/memory", response_model=ProjectMemory)
async def update_project_memory(memory: ProjectMemory):
    memory_store.save(memory)
    return memory

@router.post("/memory/apply-update", response_model=MemoryUpdateResponse)
async def apply_memory_update(request: MemoryUpdateRequest):
    """Apply one or more distilled, typed updates to canonical memory.

    Intended for the "save distilled memory update" flow in the Assistant
    Brain UI. Each patch targets one canonical section and is either
    appended under a dated header (default) or fully replaces the section
    body (`replace=true`). Invalid section names are skipped.
    """
    memory, applied, skipped = memory_store.apply_patches(request.patches)
    return MemoryUpdateResponse(applied=applied, skipped=skipped, memory=memory)

@router.get("/memory/sections")
async def list_memory_sections():
    """Return the canonical memory section names the UI should offer."""
    return {"sections": list(MEMORY_SECTION_NAMES)}

# ── Thread management ──────────────────────────────────────────────────

@router.get("/assistant/threads")
async def list_assistant_threads(include_archived: bool = False):
    threads = thread_store.list_threads(include_archived=include_archived)
    active_id = thread_store.get_active_id()
    return {"threads": [t.model_dump() for t in threads], "active_thread_id": active_id}

@router.post("/assistant/threads")
async def create_assistant_thread(request: CreateThreadRequest):
    thread = thread_store.create(title=request.title)
    return {"thread_id": thread.thread_id, "title": thread.title}

@router.post("/assistant/threads/{thread_id}/switch")
async def switch_assistant_thread(thread_id: str):
    thread = thread_store.switch(thread_id)
    if not thread:
        raise HTTPException(status_code=404, detail="Thread not found")
    return {"thread_id": thread.thread_id, "title": thread.title}

@router.patch("/assistant/threads/{thread_id}/rename")
async def rename_assistant_thread(thread_id: str, request: RenameThreadRequest):
    thread = thread_store.rename(thread_id, request.title)
    if not thread:
        raise HTTPException(status_code=404, detail="Thread not found")
    return {"thread_id": thread.thread_id, "title": thread.title}

@router.post("/assistant/threads/{thread_id}/archive")
async def archive_assistant_thread(thread_id: str):
    thread = thread_store.archive(thread_id)
    if not thread:
        raise HTTPException(status_code=404, detail="Thread not found")
    return {"thread_id": thread.thread_id, "archived": True}

@router.delete("/assistant/threads/{thread_id}")
async def delete_assistant_thread(thread_id: str):
    ok = thread_store.delete(thread_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Thread not found")
    return {"deleted": True}

# ── Active-thread chat (backward compatible) ───────────────────────────

@router.get("/assistant/history", response_model=AssistantThread)
async def get_assistant_history():
    return thread_store.load()

@router.delete("/assistant/history", response_model=AssistantThread)
async def clear_assistant_history():
    """Clear messages in the active thread (keeps the thread itself)."""
    thread = thread_store.load()
    thread.messages = []
    thread_store.save(thread)
    return thread

@router.get("/assistant/provider-status")
async def get_assistant_provider_status():
    """Return which provider the assistant would use right now and why."""
    _, resolution = assistant_svc.resolve_provider()
    payload: dict[str, object] = {
        "requested_mode": settings.assistant_provider,
        "active_provider": resolution.provider_name,
        "model": resolution.model,
        "is_mock": resolution.is_mock,
        "reason": resolution.reason,
    }
    # Surface local-CLI auth state so the UI can show an explicit "not logged
    # in" badge instead of a misleading "active" when the CLI is unusable.
    if resolution.provider_name == "claude_code_local":
        snap = ClaudeCodeLocalProvider.auth_state_snapshot()
        payload["auth_state"] = snap["state"]
        payload["auth_message"] = snap["message"]
    return payload

@router.post("/assistant/claude-code-local/recheck-auth")
async def recheck_claude_code_local_auth():
    """Clear the cached local-CLI auth state so the next chat re-probes.

    Operator use: run `claude /login` in a non-Claude-Code terminal, then
    call this endpoint so the Assistant Brain retries instead of staying on
    the cached "not_logged_in" state.
    """
    ClaudeCodeLocalProvider.reset_auth_state()
    return {"auth_state": "unknown", "message": "cache cleared; next chat will re-probe"}

@router.post("/assistant/chat")
async def chat_with_assistant(request: AssistantChatRequest):
    mem = memory_store.load()
    thread = thread_store.load()

    # Prepend existing history from disk if UI didn't pass full history
    request.history = thread.messages

    try:
        resp = await assistant_svc.chat(request, mem)
    except ClaudeCodeLocalAuthError as exc:
        resolution = assistant_svc.last_resolution
        error_msg = str(exc)
        return {
            "reply": (
                "Local Claude Code CLI is not logged in. "
                "Run `claude /login` in a non-Claude-Code terminal, then "
                "POST /api/assistant/claude-code-local/recheck-auth."
            ),
            "usage": {},
            "provider": resolution.provider_name if resolution else "claude_code_local",
            "is_mock": False,
            "error": error_msg,
            "error_type": "not_logged_in",
        }
    except Exception as exc:
        resolution = assistant_svc.last_resolution
        error_msg = str(exc)
        return {
            "reply": f"Provider error: {error_msg}",
            "usage": {},
            "provider": resolution.provider_name if resolution else "unknown",
            "is_mock": True,
            "error": error_msg,
        }

    # Save conversation
    thread.messages.append(AssistantMessage(role="user", content=request.message))
    thread.messages.append(AssistantMessage(role="assistant", content=resp.reply))
    thread_store.save(thread)

    # Include provider info in response
    resolution = assistant_svc.last_resolution
    return {
        "reply": resp.reply,
        "usage": resp.usage,
        "provider": resolution.provider_name if resolution else "unknown",
        "is_mock": resolution.is_mock if resolution else True,
    }

@router.post("/assistant/save-prompt")
async def save_assistant_prompt(request: SavePromptRequest):
    """Persist an assistant-generated prompt.

    Always writes a standalone data/prompts/asst-*.json record.
    If link_type + link_id are provided, also attaches a reference to that
    workflow (via workflow.context) or session (via session.metadata).
    """
    import uuid
    from datetime import datetime, timezone
    from backend.models.prompt import PromptTemplate, PromptTemplateCategory

    label = request.name.strip() or "Assistant prompt"
    prompt_id = f"asst-{uuid.uuid4().hex[:10]}"
    created_at = datetime.now(timezone.utc)

    # Build metadata with optional link info
    meta: dict = {
        "source_role": request.source_role,
        "link_type": request.link_type,
        "link_id": request.link_id,
    }

    template = PromptTemplate(
        id=prompt_id,
        name=label,
        category=PromptTemplateCategory.ASSISTANT_SAVED,
        body=request.content,
        variables=[],
        variant="assistant_saved",
        audience="operator",
        metadata=meta,
    )
    saved = prompt_store.save(template)

    # Attach reference to linked record (no schema change — uses existing metadata/context dicts)
    link_record = {
        "prompt_id": prompt_id,
        "name": label,
        "created_at": created_at.isoformat(),
        "source_role": request.source_role,
    }
    if request.link_type == "workflow" and request.link_id:
        wf = store.load(request.link_id)
        if wf:
            prompts = wf.context.setdefault("attached_prompts", [])
            prompts.append(link_record)
            store.save(wf)

    elif request.link_type == "session" and request.link_id:
        sess = session_store.load(request.link_id)
        if sess:
            prompts = sess.metadata.setdefault("attached_prompts", [])
            prompts.append(link_record)
            session_store.save(sess)

    return {
        "id": saved.id,
        "name": saved.name,
        "category": saved.category,
        "created_at": saved.created_at.isoformat(),
        "linked": bool(request.link_type and request.link_id),
        "link_type": request.link_type,
        "link_id": request.link_id,
    }

@router.get("/assistant/saved-prompts")
async def list_saved_prompts(
    link_type: str = "",
    link_id: str = "",
    q: str = "",
    source_role: str = "",
    sort: str = "newest",
):
    """Return prompts saved from the Assistant Brain.

    Query params (all optional, combinable):
      q=<text>                           — case-insensitive substring match on name or body
      source_role=<role>                 — filter by source_role (e.g. assistant, builder)
      link_type=workflow&link_id=<id>    — only prompts linked to that object
      link_type=session&link_id=<id>     — only prompts linked to that session
      sort=newest|oldest|name            — ordering (default: newest)
    """
    from backend.models.prompt import PromptTemplateCategory
    templates = prompt_store.by_category(PromptTemplateCategory.ASSISTANT_SAVED)

    # Filter by link
    if link_type and link_id:
        templates = [
            t for t in templates
            if t.metadata.get("link_type") == link_type and t.metadata.get("link_id") == link_id
        ]

    # Filter by source_role
    if source_role:
        templates = [
            t for t in templates
            if t.metadata.get("source_role", "assistant") == source_role
        ]

    # Text search: case-insensitive substring match on name or body
    if q:
        q_lower = q.lower()
        templates = [
            t for t in templates
            if q_lower in t.name.lower() or q_lower in t.body.lower()
        ]

    # Sort
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
                "body": t.body,
                "created_at": t.created_at.isoformat(),
                "source_role": t.metadata.get("source_role", "assistant"),
                "link_type": t.metadata.get("link_type", ""),
                "link_id": t.metadata.get("link_id", ""),
                "handoff_status": t.metadata.get("handoff_status", HandoffStatus.DRAFTED),
            }
            for t in templates
        ]
    }

@router.patch("/assistant/saved-prompts/{prompt_id}/status")
async def update_prompt_handoff_status(prompt_id: str, request: UpdateHandoffStatusRequest):
    """Operator-driven only: update the handoff lifecycle status of a saved prompt."""
    if request.status not in HANDOFF_STATUSES:
        raise HTTPException(400, f"Invalid status. Must be one of: {HANDOFF_STATUSES}")
    t = prompt_store.load(prompt_id)
    if not t:
        raise HTTPException(404, f"Prompt not found: {prompt_id}")
    t.metadata["handoff_status"] = request.status
    prompt_store.save(t)
    return {"id": prompt_id, "handoff_status": request.status}

def _expand_prompt_refs(refs: list[dict]) -> list[dict]:
    """Inline full prompt body into attachment references from the prompt store."""
    expanded = []
    for ref in refs:
        pid = ref.get("prompt_id", "")
        t = prompt_store.load(pid) if pid else None
        expanded.append({**ref, "body": t.body if t else ""})
    return expanded

@router.get("/workflows/{workflow_id}/attached-prompts")
async def get_workflow_attached_prompts(workflow_id: str):
    """Return attached prompts with full body inline — no second lookup needed."""
    wf = store.load(workflow_id)
    if not wf:
        raise HTTPException(status_code=404, detail="Workflow not found")
    refs = wf.context.get("attached_prompts", [])
    return {"workflow_id": workflow_id, "attached_prompts": _expand_prompt_refs(refs)}

@router.get("/sessions/{session_id}/attached-prompts")
async def get_session_attached_prompts(session_id: str):
    """Return attached prompts with full body inline — no second lookup needed."""
    sess = session_store.load(session_id)
    if not sess:
        raise HTTPException(status_code=404, detail="Session not found")
    refs = sess.metadata.get("attached_prompts", [])
    return {"session_id": session_id, "attached_prompts": _expand_prompt_refs(refs)}

@router.get("/assistant/linkable-objects")
async def get_linkable_objects():
    """Return a compact list of valid workflows and sessions the operator can link a prompt to."""
    workflows = []
    for wid in store.list_ids():
        try:
            wf = store.load(wid)
        except Exception:
            continue   # skip stale or malformed state files
        if wf is not None and wf.id.strip():   # skip blank-id stale records
            label = f"Workflow {wf.id[:8]} [{wf.status.value}]"
            workflows.append({"id": wf.id, "label": label})
    sessions = [
        {"id": s.session_id, "label": f"Session {s.session_id[:8]} [{s.status.value}]"}
        for s in session_store.list_sessions()
    ]
    return {"workflows": workflows, "sessions": sessions}
