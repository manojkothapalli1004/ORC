"""Workflow orchestration engine.

Implements the core loop:
1. Ask reviewer (OpenAI) for next scoped prompt
2. Send prompt to builder (Anthropic)
3. Collect builder response
4. Send response back to reviewer for approval
5. Apply approval policy
6. Repeat or stop
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone

from backend.models.core import (
    ApprovalMode,
    ApprovalStatus,
    DemoEvent,
    ExperimentSummary,
    Proposal,
    WorkflowState,
    WorkflowStatus,
)
from backend.providers.base import ProviderRequest
from backend.storage.store import StateStore

logger = logging.getLogger(__name__)


class WorkflowEngine:
    """Drives a safe planner-reviewer-builder demo loop."""

    def __init__(
        self,
        reviewer,
        builder,
        store: StateStore,
        planner=None,
    ) -> None:
        self.reviewer = reviewer
        self.planner = planner or reviewer
        self.builder = builder
        self.store = store

    def _record_event(
        self,
        state: WorkflowState,
        *,
        stage: str,
        status: WorkflowStatus,
        role: str,
        summary: str,
        provider_name: str = "",
        model: str = "",
        is_mock: bool = False,
    ) -> None:
        state.current_stage = stage
        state.status = status
        state.demo_events.append(
            DemoEvent(
                stage=stage,
                status=status.value,
                role=role,
                summary=summary,
                provider=provider_name,
                model=model,
                is_mock=is_mock,
            )
        )
        self.store.save(state)

    async def step(self, state: WorkflowState) -> WorkflowState:
        """Execute a deterministic, safe, local demo workflow loop."""
        if state.status in (WorkflowStatus.COMPLETED, WorkflowStatus.ERROR):
            return state

        try:
            if not state.summary:
                state.summary = ExperimentSummary(
                    workflow_id=state.id,
                    title=str(state.context.get("title") or "Safe demo workflow"),
                )

            self._record_event(
                state,
                stage="pending",
                status=WorkflowStatus.PENDING,
                role="system",
                summary="Workflow record created and queued for demo orchestration.",
            )

            planner_prompt = self._build_reviewer_prompt(state)
            planner_response = await self.planner.complete(
                ProviderRequest(
                    role="planner",
                    prompt=planner_prompt,
                    system_prompt=self._planner_system_prompt(state),
                    max_tokens=state.resolved_policy.budgets.planner_max_tokens,
                    metadata={"workflow_id": state.id, "phase": "planning", "demo": True, "workflow_mode": state.workflow_mode.value},
                )
            )
            next_prompt = planner_response.content.strip() or "Prepare a safe mock-only orchestrator demo artifact."
            self._record_event(
                state,
                stage="planning",
                status=WorkflowStatus.PLANNING,
                role="planner",
                summary="Planner assigned the next safe bounded task for the builder.",
                provider_name=planner_response.provider_name,
                model=planner_response.model_used,
                is_mock=planner_response.is_mock,
            )

            proposal = Proposal(
                id=uuid.uuid4().hex[:8],
                batch_index=len(state.proposals),
                prompt=next_prompt,
                files_affected=["orchestrator/backend", "orchestrator/ui"],
            )

            build_response = await self.builder.complete(
                ProviderRequest(
                    role="builder",
                    prompt=next_prompt,
                    system_prompt=self._builder_system_prompt(state),
                    max_tokens=state.resolved_policy.budgets.builder_max_tokens,
                    metadata={"workflow_id": state.id, "proposal_id": proposal.id, "phase": "building", "demo": True, "workflow_mode": state.workflow_mode.value},
                )
            )
            proposal.response = build_response.content
            proposal.token_count = build_response.token_usage.total_tokens or len(build_response.content.split())
            self._record_event(
                state,
                stage="building",
                status=WorkflowStatus.BUILDING,
                role="builder",
                summary="Builder completed a safe mock-capable implementation pass.",
                provider_name=build_response.provider_name,
                model=build_response.model_used,
                is_mock=build_response.is_mock,
            )

            reviewer_response = await self.reviewer.complete(
                ProviderRequest(
                    role="reviewer",
                    prompt=f"Review this safe demo workflow output and decide if it should proceed or block.\n\nPrompt:\n{proposal.prompt}\n\nResponse:\n{proposal.response}",
                    system_prompt=self._reviewer_system_prompt(state),
                    max_tokens=state.resolved_policy.budgets.reviewer_max_tokens,
                    metadata={"workflow_id": state.id, "proposal_id": proposal.id, "phase": "reviewing", "demo": True, "workflow_mode": state.workflow_mode.value},
                )
            )
            self._record_event(
                state,
                stage="reviewing",
                status=WorkflowStatus.REVIEWING,
                role="reviewer",
                summary="Reviewer evaluated the builder output against safe local-demo constraints.",
                provider_name=reviewer_response.provider_name,
                model=reviewer_response.model_used,
                is_mock=reviewer_response.is_mock,
            )

            approval = await self._evaluate_approval(state, proposal)
            proposal.approval = approval
            proposal.reviewer_notes = reviewer_response.content
            proposal.resolved_at = datetime.now(timezone.utc)
            state.proposals.append(proposal)

            if approval == ApprovalStatus.PENDING:
                self._record_event(
                    state,
                    stage="blocked",
                    status=WorkflowStatus.BLOCKED,
                    role="reviewer",
                    summary="Workflow is approval-aware and blocked pending a future non-demo approval action.",
                    provider_name=reviewer_response.provider_name,
                    model=reviewer_response.model_used,
                    is_mock=reviewer_response.is_mock,
                )
            else:
                approved_status = WorkflowStatus.APPROVED
                self._record_event(
                    state,
                    stage="approved",
                    status=approved_status,
                    role="reviewer",
                    summary="Workflow approved within safe demo limits.",
                    provider_name=reviewer_response.provider_name,
                    model=reviewer_response.model_used,
                    is_mock=reviewer_response.is_mock,
                )
                self._record_event(
                    state,
                    stage="completed",
                    status=WorkflowStatus.COMPLETED,
                    role="system",
                    summary="Safe end-to-end orchestrator demo completed successfully.",
                )

            state.summary.total_batches = len(state.proposals)
            state.summary.completed_batches = len(state.proposals)
            state.summary.total_tokens += proposal.token_count
            state.summary.files_changed = sorted({*state.summary.files_changed, *proposal.files_affected})
            state.summary.outcome = "success" if state.status == WorkflowStatus.COMPLETED else "blocked"
            if state.status == WorkflowStatus.COMPLETED:
                state.summary.finished_at = datetime.now(timezone.utc)

        except Exception as e:
            logger.exception("Workflow step failed")
            state.status = WorkflowStatus.ERROR
            state.current_stage = "error"
            state.error = str(e)

        self.store.save(state)
        return state

    async def _evaluate_approval(
        self, state: WorkflowState, proposal: Proposal
    ) -> ApprovalStatus:
        """Check proposal against approval policy."""
        if state.approval_mode == ApprovalMode.FULL_AUTO:
            return ApprovalStatus.AUTO_APPROVED

        if state.approval_mode == ApprovalMode.AUTO_WITH_LIMITS:
            within_file_limit = len(proposal.files_affected) <= state.resolved_policy.budgets.max_files_per_batch
            within_token_limit = proposal.token_count <= state.resolved_policy.budgets.builder_max_tokens
            if within_file_limit and within_token_limit:
                return ApprovalStatus.AUTO_APPROVED
            return ApprovalStatus.PENDING  # needs human review

        return ApprovalStatus.PENDING  # human mode

    def _build_reviewer_prompt(self, state: WorkflowState) -> str:
        """Build context for the reviewer to generate the next prompt."""
        recent_batches = state.resolved_policy.context.recent_batches
        preview_chars = state.resolved_policy.context.response_preview_chars
        history = ""
        for p in state.proposals[-recent_batches:]:
            status = p.approval.value
            history += (
                f"\n---\nBatch {p.batch_index} [{status}]:\n"
                f"Prompt: {p.prompt[:preview_chars]}\n"
                f"Response preview: {(p.response or '')[:preview_chars]}\n"
            )

        if state.resolved_policy.compression.compress_completed_batches and len(state.proposals) > recent_batches:
            history = f"\nCompressed prior batches: {len(state.proposals) - recent_batches} older batch summaries omitted for {state.resolved_policy.mode.value} mode." + history

        return f"""Workflow context:
- Total batches completed: {len(state.proposals)}
- Current status: {state.status.value}
- Workflow mode: {state.workflow_mode.value} ({state.resolved_policy.label})
- Context detail: {state.resolved_policy.context.context_detail}
- Review depth: {state.resolved_policy.review.review_depth}
- Project metadata: {state.context}

Recent history:{history or ' (none yet — this is the first batch)'}

Generate the next scoped builder prompt, or respond WORKFLOW_COMPLETE if done."""

    def _planner_system_prompt(self, state: WorkflowState) -> str:
        compactness = state.resolved_policy.prompts.planner_system_style
        return (
            "You are the planner for a safe local orchestrator demo. "
            f"Use {compactness} planning for {state.resolved_policy.label} mode. "
            "Produce a concise implementation plan for the builder without invoking anything outside orchestrator."
        )

    def _builder_system_prompt(self, state: WorkflowState) -> str:
        compactness = state.resolved_policy.prompts.builder_system_style
        return (
            "You are the builder for a safe orchestrator demo. "
            f"Use a {compactness} response style for {state.resolved_policy.label} mode. "
            "Describe a bounded implementation result without invoking tools outside orchestrator."
        )

    def _reviewer_system_prompt(self, state: WorkflowState) -> str:
        review_depth = state.resolved_policy.review.review_depth
        return (
            "You are the reviewer for a safe orchestrator demo. "
            f"Apply {review_depth} review for {state.resolved_policy.label} mode. "
            "Approve bounded local-demo changes; block anything that would require real approvals or live control."
        )
