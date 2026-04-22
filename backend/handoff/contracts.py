"""Render typed Claude-ready handoff contracts for approved builder jobs."""

from __future__ import annotations

from backend.models.core import ApprovalStatus, Proposal, WorkflowState
from backend.models.handoff import (
    BuilderJobCategory,
    BuilderJobInboxPayload,
    ClaudeExpectedReturnFormat,
    ClaudeHandoffContract,
    ClaudeHandoffSection,
    ClaudeWorkflowScope,
)

_CATEGORY_TASKS = {
    BuilderJobCategory.PLAN: "plan the approved bounded work before implementation",
    BuilderJobCategory.BUILD: "implement the approved bounded work",
    BuilderJobCategory.REVIEW: "review the approved bounded work and identify issues",
    BuilderJobCategory.ANALYZE: "analyze the approved bounded work and explain implications",
    BuilderJobCategory.PROPOSAL_GENERATION: "generate scoped implementation proposals for the approved workflow",
    BuilderJobCategory.APPROVAL_FOLLOWUP: "prepare approval follow-up output for the approved workflow",
}

_CATEGORY_OUTPUTS = {
    BuilderJobCategory.PLAN: ["files to inspect or change", "implementation plan", "verification steps"],
    BuilderJobCategory.BUILD: ["files changed", "implementation summary", "verification result"],
    BuilderJobCategory.REVIEW: ["issues found", "risk summary", "verification result"],
    BuilderJobCategory.ANALYZE: ["analysis summary", "affected surfaces", "verification result"],
    BuilderJobCategory.PROPOSAL_GENERATION: ["proposal set", "scope summary", "verification result"],
    BuilderJobCategory.APPROVAL_FOLLOWUP: ["approval follow-up summary", "required decisions", "verification result"],
}


def _string_context(value) -> dict[str, str]:
    if not isinstance(value, dict):
        return {}
    result: dict[str, str] = {}
    for key, item in value.items():
        if item is None:
            continue
        if isinstance(item, (str, int, float, bool)):
            result[str(key)] = str(item)
    return result


class ClaudeHandoffRenderer:
    def render(self, *, job, state: WorkflowState, proposal: Proposal, category: BuilderJobCategory) -> ClaudeHandoffContract:
        if job.approval_status not in (ApprovalStatus.APPROVED, ApprovalStatus.AUTO_APPROVED):
            raise ValueError("Builder job is not approved for Claude handoff")

        scope = ClaudeWorkflowScope(
            workflow_id=job.workflow_id,
            proposal_id=job.proposal_id,
            proposal_batch_index=job.proposal_batch_index,
            workflow_status=state.status.value,
            approval_mode=state.approval_mode.value,
            project_scope=str(state.context.get("scope") or "orchestrator only"),
            files_affected=list(proposal.files_affected),
            context_summary=_string_context(state.context),
        )
        return_format = ClaudeExpectedReturnFormat(sections=_CATEGORY_OUTPUTS[category])
        sections = [
            ClaudeHandoffSection(key="role", title="Role", body="Builder"),
            ClaudeHandoffSection(key="task", title="Task", body=_CATEGORY_TASKS[category]),
            ClaudeHandoffSection(key="scope", title="Scope", body=self._render_scope(scope)),
            ClaudeHandoffSection(key="safety", title="Safety constraints", body=self._render_safety()),
            ClaudeHandoffSection(
                key="job_context",
                title="Approved builder job context",
                body=self._render_job_context(job=job, proposal=proposal),
            ),
            ClaudeHandoffSection(
                key="return_format",
                title="Expected return format",
                body=self._render_return_format(return_format),
            ),
            ClaudeHandoffSection(key="stop", title="Stop when", body="Stop when the requested output is complete."),
        ]
        prompt_text = "\n\n".join(f"{section.title}:\n{section.body}" for section in sections)
        return ClaudeHandoffContract(
            job_id=job.id,
            workflow_id=job.workflow_id,
            proposal_id=job.proposal_id,
            approval_status=job.approval_status,
            category=category,
            scope=scope,
            return_format=return_format,
            sections=sections,
            prompt_text=prompt_text,
            worker_metadata={
                "handoff_consumer": "local_claude_antigravity_handoff",
                "channel": job.channel.value,
                "contract_version": "claude_handoff_v1",
            },
        )

    def render_inbox_payload(self, *, job, state: WorkflowState, proposal: Proposal, category: BuilderJobCategory) -> BuilderJobInboxPayload:
        contract = self.render(job=job, state=state, proposal=proposal, category=category)
        return BuilderJobInboxPayload(
            job_id=job.id,
            workflow_id=job.workflow_id,
            proposal_id=job.proposal_id,
            category=category,
            approval_status=job.approval_status,
            contract=contract,
        )

    def _render_scope(self, scope: ClaudeWorkflowScope) -> str:
        lines = [
            "- only files inside orchestrator/",
            f"- workflow_id: {scope.workflow_id}",
            f"- proposal_id: {scope.proposal_id}",
            f"- batch_index: {scope.proposal_batch_index}",
            f"- workflow_status: {scope.workflow_status}",
            f"- approval_mode: {scope.approval_mode}",
            f"- project_scope: {scope.project_scope}",
        ]
        if scope.files_affected:
            lines.append(f"- files_affected: {', '.join(scope.files_affected)}")
        if scope.context_summary:
            lines.append("- workflow_context:")
            lines.extend(f"  - {key}: {value}" for key, value in scope.context_summary.items())
        return "\n".join(lines)

    def _render_safety(self) -> str:
        return "\n".join([
            "- do not modify anything outside orchestrator/",
            "- do not build screen automation",
            "- do not execute arbitrary shell commands",
            "- do not perform real dispatch or live runtime control",
            "- keep the output typed, auditable, and reusable",
        ])

    def _render_job_context(self, *, job, proposal: Proposal) -> str:
        lines = [
            f"- job_id: {job.id}",
            f"- approval_status: {job.approval_status.value}",
            f"- proposal_prompt: {job.proposal_prompt}",
        ]
        if proposal.response:
            lines.append(f"- proposal_response: {proposal.response}")
        if proposal.reviewer_notes:
            lines.append(f"- reviewer_notes: {proposal.reviewer_notes}")
        return "\n".join(lines)

    def _render_return_format(self, return_format: ClaudeExpectedReturnFormat) -> str:
        lines = [f"- format: {return_format.format_name}", "- return sections:"]
        lines.extend(f"  - {section}" for section in return_format.sections)
        lines.append("- instructions:")
        lines.extend(f"  - {instruction}" for instruction in return_format.instructions)
        return "\n".join(lines)
