"""Storage tests for BuilderJobStore (backend/storage/builder_jobs.py).

These exercise the real file-backed API against a tmp_path root: save/load
round-trips, missing ids, list ordering, update isolation, and the current
behaviour when a corrupt or non-JSON record is present in the records dir.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from pydantic import ValidationError

from backend.models.builder import (
    BuilderJob,
    BuilderJobLifecycleEvent,
    BuilderJobResult,
    BuilderJobStatus,
    BuilderWorkerLease,
)
from backend.models.core import ApprovalStatus
from backend.models.handoff import (
    BuilderJobCategory,
    ClaudeHandoffContract,
    ClaudeHandoffSection,
    ClaudeWorkflowScope,
)
from backend.models.results import (
    NextStepAction,
    NextStepSuggestion,
    ResultAttachmentLink,
    ResultIngestionRecord,
    ResultOutcome,
    ResultPlanningRecord,
    StructuredWorkerResult,
)
from backend.storage.builder_jobs import BuilderJobStore


def _job(store: BuilderJobStore, job_id: str, **overrides) -> BuilderJob:
    fields = {
        "id": job_id,
        "workflow_id": "wf-1",
        "proposal_id": "prop-1",
        "proposal_prompt": f"prompt for {job_id}",
        "approval_status": ApprovalStatus.APPROVED,
        "inbox_path": str(store.inbox_dir / f"{job_id}.json"),
        "outbox_path": str(store.outbox_dir / f"{job_id}.json"),
    }
    fields.update(overrides)
    return BuilderJob(**fields)


def _planning_record(job_id: str) -> ResultPlanningRecord:
    return ResultPlanningRecord(
        ingestion=ResultIngestionRecord(
            id="ing-0001",
            source="local_worker",
            attached_to=ResultAttachmentLink(
                workflow_id="wf-1", proposal_id="prop-1", job_id=job_id, session_id="sess-1"
            ),
            result=StructuredWorkerResult(
                session_id="sess-1",
                summary="done",
                output_ref="out/ref.txt",
                details="all good",
                artifacts=["a.txt", "b.txt"],
                error=None,
                metadata={"k": "v"},
            ),
            outcome=ResultOutcome.SUCCESS,
        ),
        suggestion=NextStepSuggestion(
            action=NextStepAction.MARK_WORKFLOW_COMPLETE,
            reason="success",
            notes=["n1"],
        ),
    )


def _full_job(store: BuilderJobStore, job_id: str) -> BuilderJob:
    now = datetime.now(timezone.utc)
    record = _planning_record(job_id)
    return _job(
        store,
        job_id,
        proposal_batch_index=3,
        approval_status=ApprovalStatus.AUTO_APPROVED,
        category=BuilderJobCategory.REVIEW,
        status=BuilderJobStatus.COMPLETED,
        created_at=now - timedelta(minutes=10),
        started_at=now - timedelta(minutes=5),
        completed_at=now - timedelta(minutes=1),
        claimed_by=BuilderWorkerLease(worker_id="worker-7", lock_path=str(store.locks_dir / f"{job_id}.lock")),
        lifecycle=[
            BuilderJobLifecycleEvent(status=BuilderJobStatus.PENDING, note="queued"),
            BuilderJobLifecycleEvent(
                status=BuilderJobStatus.RUNNING, worker_id="worker-7", note="claimed", metadata={"x": 1}
            ),
        ],
        handoff_contract=ClaudeHandoffContract(
            job_id=job_id,
            workflow_id="wf-1",
            proposal_id="prop-1",
            approval_status=ApprovalStatus.AUTO_APPROVED,
            category=BuilderJobCategory.REVIEW,
            scope=ClaudeWorkflowScope(
                workflow_id="wf-1",
                proposal_id="prop-1",
                proposal_batch_index=3,
                workflow_status="approved",
                approval_mode="manual",
                files_affected=["backend/x.py"],
                context_summary={"a": "b"},
            ),
            sections=[ClaudeHandoffSection(key="task", title="Task", body="do it")],
            prompt_text="rendered prompt",
            worker_metadata={"w": True},
        ),
        result=BuilderJobResult(
            status=BuilderJobStatus.COMPLETED,
            summary="finished",
            output_ref="out/ref.txt",
            artifacts=["a.txt"],
            error=None,
            metadata={"m": 2},
        ),
        ingested_results=[record],
        next_step=record,
        metadata={"handoff": "claude_local_bridge", "nested": {"deep": [1, 2, 3]}},
    )


# ---------------------------------------------------------------------------
# construction
# ---------------------------------------------------------------------------


def test_store_creates_layout_under_root(tmp_path: Path) -> None:
    store = BuilderJobStore(root_dir=tmp_path)
    assert store.root_dir == tmp_path
    for name in ("records", "inbox", "outbox", "archive", "locks"):
        assert (tmp_path / name).is_dir()
    assert store.inbox_dir == tmp_path / "inbox"
    assert store.outbox_dir == tmp_path / "outbox"
    assert store.locks_dir == tmp_path / "locks"
    assert store.list_jobs() == []


# ---------------------------------------------------------------------------
# save / load
# ---------------------------------------------------------------------------


def test_save_then_load_round_trips_every_field(tmp_path: Path) -> None:
    store = BuilderJobStore(root_dir=tmp_path)
    job = _full_job(store, "aaaa11112222")

    saved = store.save(job)
    assert saved is job

    loaded = store.load("aaaa11112222")
    assert loaded is not None
    assert loaded is not job
    # Compare the JSON projection so tz-aware datetimes and enums are compared by value.
    assert loaded.model_dump(mode="json") == job.model_dump(mode="json")

    # Spot-check nested structures survived the trip as typed models.
    assert loaded.claimed_by is not None and loaded.claimed_by.worker_id == "worker-7"
    assert [e.status for e in loaded.lifecycle] == [BuilderJobStatus.PENDING, BuilderJobStatus.RUNNING]
    assert loaded.handoff_contract is not None
    assert loaded.handoff_contract.scope.files_affected == ["backend/x.py"]
    assert loaded.handoff_contract.sections[0].key == "task"
    assert loaded.result is not None and loaded.result.artifacts == ["a.txt"]
    assert len(loaded.ingested_results) == 1
    assert loaded.next_step is not None
    assert loaded.next_step.suggestion.action == NextStepAction.MARK_WORKFLOW_COMPLETE
    assert loaded.metadata["nested"]["deep"] == [1, 2, 3]
    assert loaded.created_at.tzinfo is not None


def test_save_writes_record_file_and_bumps_updated_at(tmp_path: Path) -> None:
    store = BuilderJobStore(root_dir=tmp_path)
    stale = datetime(2020, 1, 1, tzinfo=timezone.utc)
    job = _job(store, "bbbb11112222", created_at=stale, updated_at=stale)

    store.save(job)

    record = tmp_path / "records" / "bbbb11112222.json"
    assert record.is_file()
    assert not (tmp_path / "records" / "bbbb11112222.tmp").exists()
    assert sorted(p.name for p in (tmp_path / "records").iterdir()) == ["bbbb11112222.json"]

    assert job.updated_at > stale
    assert job.created_at == stale
    loaded = store.load("bbbb11112222")
    assert loaded is not None
    assert loaded.updated_at == job.updated_at
    assert loaded.created_at == stale

    # save does not touch inbox/outbox/archive/locks.
    for name in ("inbox", "outbox", "archive", "locks"):
        assert list((tmp_path / name).iterdir()) == []


def test_load_missing_id_returns_none(tmp_path: Path) -> None:
    store = BuilderJobStore(root_dir=tmp_path)
    assert store.load("does-not-exist") is None
    store.save(_job(store, "cccc11112222"))
    assert store.load("cccc11112223") is None
    assert store.load("") is None


# ---------------------------------------------------------------------------
# list_jobs ordering
# ---------------------------------------------------------------------------


def test_list_jobs_sorted_by_record_filename_not_insertion_or_time(tmp_path: Path) -> None:
    store = BuilderJobStore(root_dir=tmp_path)
    base = datetime(2021, 6, 1, tzinfo=timezone.utc)

    # Insert in an order that differs from filename order, and give created_at
    # timestamps that order differently again, so a wrong sort key is caught.
    store.save(_job(store, "mmm", created_at=base + timedelta(hours=1)))
    store.save(_job(store, "zzz", created_at=base))
    store.save(_job(store, "aaa", created_at=base + timedelta(hours=2)))

    jobs = store.list_jobs()
    assert [j.id for j in jobs] == ["aaa", "mmm", "zzz"]
    assert [j.id for j in jobs] == sorted(j.id for j in jobs)
    assert [j.created_at for j in jobs] != sorted(j.created_at for j in jobs)


def test_list_jobs_ignores_non_json_files_in_records(tmp_path: Path) -> None:
    store = BuilderJobStore(root_dir=tmp_path)
    store.save(_job(store, "ddd"))
    (tmp_path / "records" / "leftover.tmp").write_text("{}")
    (tmp_path / "records" / "notes.txt").write_text("not a record")

    assert [j.id for j in store.list_jobs()] == ["ddd"]


# ---------------------------------------------------------------------------
# update isolation
# ---------------------------------------------------------------------------


def test_updating_one_job_preserves_the_others(tmp_path: Path) -> None:
    store = BuilderJobStore(root_dir=tmp_path)
    store.save(_job(store, "j-a", proposal_prompt="alpha"))
    store.save(_job(store, "j-b", proposal_prompt="beta"))
    store.save(_job(store, "j-c", proposal_prompt="gamma"))
    before = {j.id: j.model_dump(mode="json") for j in store.list_jobs()}

    job_b = store.load("j-b")
    assert job_b is not None
    job_b.status = BuilderJobStatus.RUNNING
    job_b.metadata["touched"] = True
    store.save(job_b)

    after = {j.id: j.model_dump(mode="json") for j in store.list_jobs()}
    assert set(after) == {"j-a", "j-b", "j-c"}
    assert after["j-a"] == before["j-a"]
    assert after["j-c"] == before["j-c"]
    assert after["j-b"]["status"] == "running"
    assert after["j-b"]["metadata"]["touched"] is True
    assert after["j-b"]["proposal_prompt"] == "beta"
    assert after["j-b"] != before["j-b"]


# ---------------------------------------------------------------------------
# corrupt records (pins current behaviour: no skipping, validation error raised)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "corrupt_body",
    [
        "this is not json",
        "",
        '{"id": "broken"}',  # valid JSON but missing required fields
        '{"id": "broken", "workflow_id": "w", "proposal_id": "p", "proposal_prompt": "x",'
        ' "approval_status": "approved", "inbox_path": "i", "outbox_path": "o", "status": "bogus"}',
    ],
    ids=["non-json", "empty", "missing-fields", "bad-enum"],
)
def test_corrupt_record_makes_list_jobs_raise_validation_error(tmp_path: Path, corrupt_body: str) -> None:
    store = BuilderJobStore(root_dir=tmp_path)
    store.save(_job(store, "aaa-valid"))
    store.save(_job(store, "zzz-valid"))
    (tmp_path / "records" / "broken.json").write_text(corrupt_body)

    # Current behaviour: list_jobs does not skip corrupt records; it raises.
    with pytest.raises(ValidationError):
        store.list_jobs()

    # Valid records are still individually loadable; the corrupt one raises on load too.
    assert store.load("aaa-valid") is not None
    assert store.load("zzz-valid") is not None
    with pytest.raises(ValidationError):
        store.load("broken")

    # Removing the corrupt file restores listing of the valid jobs.
    (tmp_path / "records" / "broken.json").unlink()
    assert [j.id for j in store.list_jobs()] == ["aaa-valid", "zzz-valid"]


# ---------------------------------------------------------------------------
# derived queries built on list_jobs / load
# ---------------------------------------------------------------------------


def test_pending_and_workflow_filters_use_persisted_state(tmp_path: Path) -> None:
    store = BuilderJobStore(root_dir=tmp_path)
    store.save(_job(store, "p1", workflow_id="wf-1", status=BuilderJobStatus.PENDING))
    store.save(_job(store, "p2", workflow_id="wf-2", status=BuilderJobStatus.RUNNING))
    store.save(_job(store, "p3", workflow_id="wf-1", status=BuilderJobStatus.COMPLETED))
    store.save(_job(store, "p4", workflow_id="wf-1", status=BuilderJobStatus.PENDING, proposal_id="prop-2"))

    assert [j.id for j in store.list_pending_jobs()] == ["p1", "p4"]
    assert [j.id for j in store.workflow_jobs("wf-1")] == ["p1", "p3", "p4"]
    assert store.workflow_jobs("wf-none") == []

    active = store.find_active_for_proposal("wf-1", "prop-1")
    assert active is not None and active.id == "p1"
    active2 = store.find_active_for_proposal("wf-2", "prop-1")
    assert active2 is not None and active2.id == "p2"
    assert store.find_active_for_proposal("wf-1", "prop-9") is None


def test_mark_result_on_missing_job_raises_file_not_found(tmp_path: Path) -> None:
    store = BuilderJobStore(root_dir=tmp_path)
    with pytest.raises(FileNotFoundError):
        store.mark_result("ghost", BuilderJobResult(status=BuilderJobStatus.COMPLETED))
    with pytest.raises(FileNotFoundError):
        store.result_history("ghost")
    with pytest.raises(FileNotFoundError):
        store.suggested_next_step("ghost")


def test_mark_result_completed_persists_and_moves_inbox_to_archive(tmp_path: Path) -> None:
    store = BuilderJobStore(root_dir=tmp_path)
    job = _job(store, "done1")
    store.save(job)
    inbox = tmp_path / "inbox" / "done1.json"
    inbox.write_text("{}")
    lock = tmp_path / "locks" / "done1.lock"
    lock.write_text("{}")

    result = BuilderJobResult(
        status=BuilderJobStatus.COMPLETED,
        summary="ok",
        output_ref="ref",
        artifacts=["z.txt"],
        metadata={"m": 1},
    )
    updated = store.mark_result("done1", result)

    assert updated.status == BuilderJobStatus.COMPLETED
    assert updated.started_at is not None and updated.completed_at is not None
    assert updated.lifecycle[-1].status == BuilderJobStatus.COMPLETED
    assert updated.lifecycle[-1].metadata == {"m": 1}

    reloaded = store.load("done1")
    assert reloaded is not None
    assert reloaded.model_dump(mode="json") == updated.model_dump(mode="json")
    assert reloaded.result is not None and reloaded.result.summary == "ok"

    assert not inbox.exists()
    assert (tmp_path / "archive" / "done1.json").is_file()
    assert not lock.exists()
    outbox = tmp_path / "outbox" / "done1.json"
    assert outbox.is_file()
    assert BuilderJob.model_validate_json(outbox.read_text()).id == "done1"
