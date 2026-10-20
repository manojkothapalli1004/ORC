"""Regression tests for the reconcile service against a real StateStore.

StateReconciler walks ``StateStore.list_ids()`` and ``StateStore.load()`` to
find stale workflow state. That path is the operator's recovery tool for
malformed state, and the 2026-04-21 list_ids fix (invalid_state_count noise and
ghost workflows from co-located JSON files) was only found by hand through it.

These tests drive ``StateReconciler.run`` with a real ``StateStore`` rooted in
``tmp_path``. The job and session stores are replaced with empty in-memory
stubs so the tests never touch ``bridge/builder_jobs`` or ``data/sessions``
and only the workflow-state path is under test.
"""

from __future__ import annotations

import json
import logging
import sys
from pathlib import Path

import pytest

import backend.reconcile_state as reconcile_cli
import backend.storage.store as store_mod
from backend.models.core import WorkflowStatus
from backend.models.results import ResultIngestionRequest
from backend.planning.next_steps import build_planning_record
from backend.services.reconcile import StateReconciler
from backend.storage.store import StateStore


class _NullIndex:
    def upsert(self, *args, **kwargs):
        return None

    def delete(self, *args, **kwargs):
        return None


class _EmptyJobStore:
    """Stand-in for BuilderJobStore: no builder jobs on disk."""

    def list_jobs(self):
        return []


class _EmptySessionStore:
    """Stand-in for SessionStore: no work sessions on disk."""

    def list_sessions(self):
        return []


@pytest.fixture(autouse=True)
def _no_search_index(monkeypatch):
    # Keep tests hermetic: don't touch the real search index.
    monkeypatch.setattr(store_mod, "get_search_index", lambda: _NullIndex())


@pytest.fixture
def state_store(tmp_path: Path) -> StateStore:
    return StateStore(state_dir=tmp_path)


@pytest.fixture
def reconciler(state_store: StateStore) -> StateReconciler:
    return StateReconciler(
        state_store=state_store,
        job_store=_EmptyJobStore(),
        session_store=_EmptySessionStore(),
    )


def _state_path(tmp_path: Path, workflow_id: str) -> Path:
    return tmp_path / f"{workflow_id}.json"


def _snapshot(tmp_path: Path) -> dict[str, bytes]:
    return {p.name: p.read_bytes() for p in sorted(tmp_path.iterdir()) if p.is_file()}


def _make_stale_completed(store: StateStore) -> str:
    """A COMPLETED workflow whose latest result says follow-up is still needed."""
    state = store.create()
    state.status = WorkflowStatus.COMPLETED
    state.next_step = build_planning_record(
        ResultIngestionRequest(workflow_id=state.id, summary="Needs follow-up: tests still missing.")
    )
    store.save(state)
    return state.id


def _make_clean_completed(store: StateStore) -> str:
    """A COMPLETED workflow whose latest result agrees it is complete."""
    state = store.create()
    state.status = WorkflowStatus.COMPLETED
    state.next_step = build_planning_record(
        ResultIngestionRequest(workflow_id=state.id, summary="All done.")
    )
    store.save(state)
    return state.id


def _drop_noise(state_dir: Path) -> None:
    (state_dir / "project_memory.json").write_text(json.dumps({"facts": ["x"]}))
    (state_dir / "assistant_threads.json").write_text(json.dumps({"threads": []}))


# (1) empty state dir ---------------------------------------------------------

def test_empty_state_dir_reconciles_with_zero_changes(tmp_path: Path, reconciler: StateReconciler) -> None:
    for apply in (False, True):
        report = reconciler.run(apply=apply)
        assert report == {
            "apply": apply,
            "issues": [],
            "summary": {"total": 0, "auto_repairable": 0, "repaired": 0, "flagged_only": 0},
        }
    # Nothing was written into the state dir.
    assert _snapshot(tmp_path) == {}


# (2) valid state is untouched ------------------------------------------------

def test_valid_workflow_state_is_left_byte_identical(
    tmp_path: Path, state_store: StateStore, reconciler: StateReconciler
) -> None:
    fresh_id = state_store.create().id
    clean_id = _make_clean_completed(state_store)
    before = _snapshot(tmp_path)

    report = reconciler.run(apply=True)

    assert report["issues"] == []
    assert report["summary"]["total"] == 0
    assert _snapshot(tmp_path) == before
    assert state_store.load(fresh_id) is not None
    assert state_store.load(clean_id).status == WorkflowStatus.COMPLETED


def test_stale_completed_workflow_is_flagged_then_repaired(
    tmp_path: Path, state_store: StateStore, reconciler: StateReconciler
) -> None:
    stale_id = _make_stale_completed(state_store)
    before = _snapshot(tmp_path)

    report = reconciler.run(apply=False)

    assert report["summary"] == {"total": 1, "auto_repairable": 1, "repaired": 0, "flagged_only": 1}
    (issue,) = report["issues"]
    assert issue == {
        "kind": "workflow_stale_completed",
        "severity": "high",
        "target_id": stale_id,
        "message": "Workflow is completed but latest linked result requires nonterminal follow-up.",
        "details": {
            "status": "completed",
            "latest_action": "create_followup_job",
            "latest_outcome": "needs_followup",
        },
        "auto_repairable": True,
        "repaired": False,
    }
    # Diagnose-only never writes.
    assert _snapshot(tmp_path) == before

    applied = reconciler.run(apply=True)

    assert applied["summary"] == {"total": 1, "auto_repairable": 1, "repaired": 1, "flagged_only": 0}
    assert applied["issues"][0]["repaired"] is True
    repaired = state_store.load(stale_id)
    assert repaired is not None
    assert repaired.status == WorkflowStatus.APPROVED
    assert repaired.current_stage == "result_needs_followup"


# (3) malformed 12-hex-id workflow file --------------------------------------

def test_malformed_workflow_file_is_skipped_and_others_still_processed(
    tmp_path: Path, state_store: StateStore, reconciler: StateReconciler, caplog: pytest.LogCaptureFixture
) -> None:
    bad_id = "deadbeef0123"
    bad_path = _state_path(tmp_path, bad_id)
    bad_path.write_text("{not valid json")
    stale_id = _make_stale_completed(state_store)
    bad_bytes = bad_path.read_bytes()

    assert bad_id in state_store.list_ids()

    with caplog.at_level(logging.WARNING, logger="orchestrator.storage"):
        report = reconciler.run(apply=True)

    # The malformed file is logged and skipped, not raised and not repaired.
    assert any(
        "skipping invalid state file" in rec.getMessage() and bad_id in rec.getMessage()
        for rec in caplog.records
    )
    assert bad_path.read_bytes() == bad_bytes
    assert [i["target_id"] for i in report["issues"]] == [stale_id]
    assert report["summary"]["repaired"] == 1
    assert state_store.load(stale_id).status == WorkflowStatus.APPROVED


# (4) co-located non-workflow files ------------------------------------------

def test_colocated_non_workflow_files_are_ignored_and_untouched(
    tmp_path: Path, state_store: StateStore, reconciler: StateReconciler, caplog: pytest.LogCaptureFixture
) -> None:
    state_store.create()
    _drop_noise(tmp_path)
    before = _snapshot(tmp_path)

    with caplog.at_level(logging.WARNING, logger="orchestrator.storage"):
        report = reconciler.run(apply=True)

    assert report["issues"] == []
    assert report["summary"]["total"] == 0
    # Not counted as invalid state: no "skipping invalid state file" warning.
    assert not any("skipping invalid state file" in rec.getMessage() for rec in caplog.records)
    assert _snapshot(tmp_path) == before
    assert "project_memory" not in state_store.list_ids()
    assert "assistant_threads" not in state_store.list_ids()


# (5) idempotency --------------------------------------------------------------

def test_reconcile_is_idempotent(tmp_path: Path, state_store: StateStore, reconciler: StateReconciler) -> None:
    _make_stale_completed(state_store)
    _make_clean_completed(state_store)
    state_store.create()
    _drop_noise(tmp_path)

    first = reconciler.run(apply=False)
    second = reconciler.run(apply=False)
    assert first == second
    assert first["summary"]["total"] == 1

    applied = reconciler.run(apply=True)
    assert applied["summary"]["repaired"] == 1
    after_repair = _snapshot(tmp_path)

    again = reconciler.run(apply=True)
    assert again["issues"] == []
    assert again["summary"] == {"total": 0, "auto_repairable": 0, "repaired": 0, "flagged_only": 0}
    assert _snapshot(tmp_path) == after_repair


# CLI wrapper ------------------------------------------------------------------

def test_cli_prints_report_json(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    state_store: StateStore,
    reconciler: StateReconciler,
) -> None:
    stale_id = _make_stale_completed(state_store)
    # Never let the CLI build default stores against real backend state.
    monkeypatch.setattr(reconcile_cli, "StateReconciler", lambda: reconciler)
    monkeypatch.setattr(sys, "argv", ["reconcile_state", "--apply"])

    reconcile_cli.main()

    report = json.loads(capsys.readouterr().out)
    assert report["apply"] is True
    assert [i["target_id"] for i in report["issues"]] == [stale_id]
    assert report["summary"]["repaired"] == 1
    assert state_store.load(stale_id).status == WorkflowStatus.APPROVED
