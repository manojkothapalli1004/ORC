"""Deterministic result-ingestion and next-step planning helpers."""

from __future__ import annotations

import uuid

from backend.models.results import (
    NextStepAction,
    NextStepSuggestion,
    ResultAttachmentLink,
    ResultIngestionRecord,
    ResultIngestionRequest,
    ResultOutcome,
    ResultPlanningRecord,
    StructuredWorkerResult,
)


def classify_outcome(req: ResultIngestionRequest) -> ResultOutcome:
    if req.error:
        return ResultOutcome.FAILED
    lowered = f"{req.summary}\n{req.details}".lower()
    if any(token in lowered for token in ["blocked", "waiting for approval", "cannot proceed"]):
        return ResultOutcome.BLOCKED
    if any(token in lowered for token in ["follow-up", "follow up", "needs followup", "needs follow-up"]):
        return ResultOutcome.NEEDS_FOLLOWUP
    if any(token in lowered for token in ["partial", "partially complete", "incomplete"]):
        return ResultOutcome.PARTIAL_SUCCESS
    return ResultOutcome.SUCCESS


def suggest_next_step(outcome: ResultOutcome) -> NextStepSuggestion:
    if outcome == ResultOutcome.SUCCESS:
        return NextStepSuggestion(
            action=NextStepAction.MARK_WORKFLOW_COMPLETE,
            reason="Result completed successfully with no explicit blocker or follow-up signal.",
        )
    if outcome == ResultOutcome.PARTIAL_SUCCESS:
        return NextStepSuggestion(
            action=NextStepAction.REVIEW_RESULT,
            reason="Result is only partially complete and should be reviewed before deciding the next action.",
        )
    if outcome == ResultOutcome.NEEDS_FOLLOWUP:
        return NextStepSuggestion(
            action=NextStepAction.CREATE_FOLLOWUP_JOB,
            reason="Result explicitly indicates follow-up work is needed.",
            followup_category="build",
        )
    if outcome == ResultOutcome.BLOCKED:
        return NextStepSuggestion(
            action=NextStepAction.REQUEST_APPROVAL,
            reason="Result is blocked and requires approval or an external decision to continue.",
        )
    return NextStepSuggestion(
        action=NextStepAction.RETRY_WITH_CHANGES,
        reason="Result failed and should be retried only after changing the prompt, inputs, or scope.",
        followup_category="review",
    )


def build_planning_record(req: ResultIngestionRequest) -> ResultPlanningRecord:
    outcome = classify_outcome(req)
    ingestion = ResultIngestionRecord(
        id=uuid.uuid4().hex[:12],
        source=req.source,
        attached_to=ResultAttachmentLink(
            workflow_id=req.workflow_id,
            proposal_id=req.proposal_id,
            job_id=req.job_id,
            session_id=req.session_id,
        ),
        result=StructuredWorkerResult(
            session_id=req.session_id,
            summary=req.summary,
            output_ref=req.output_ref,
            details=req.details,
            artifacts=req.artifacts,
            error=req.error,
            metadata=req.metadata,
        ),
        outcome=outcome,
    )
    return ResultPlanningRecord(
        ingestion=ingestion,
        suggestion=suggest_next_step(outcome),
    )
