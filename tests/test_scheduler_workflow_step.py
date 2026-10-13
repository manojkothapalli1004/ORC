"""Scheduled run_workflow_step builds the workflow engine like the API does."""

from __future__ import annotations

import asyncio
import threading
from pathlib import Path

import pytest

import backend.storage.store as store_mod
from backend.models.scheduler import ScheduledActionType, ScheduledJob
from backend.providers import MockProvider
from backend.providers.registry import ProviderRegistry
from backend.services.scheduler import Scheduler
from backend.storage.store import StateStore


class _NullIndex:
    def upsert(self, *args, **kwargs):
        return None

    def delete(self, *args, **kwargs):
        return None


class _FakeScheduleStore:
    def __init__(self) -> None:
        self.runs = []
        self.updates = []

    def get(self, job_id):
        return None

    def append_run(self, run):
        self.runs.append(run)

    def update(self, job):
        self.updates.append(job)


@pytest.fixture(autouse=True)
def _hermetic(monkeypatch):
    monkeypatch.setattr(store_mod, "get_search_index", lambda: _NullIndex())

    def _mock_from_workflow(self, state):
        return {
            "reviewer": MockProvider(provider_name="mock:reviewer"),
            "planner": MockProvider(provider_name="mock:planner"),
            "builder": MockProvider(provider_name="mock:builder"),
        }

    monkeypatch.setattr(ProviderRegistry, "from_workflow", _mock_from_workflow)


def _make_scheduler(tmp_path: Path) -> Scheduler:
    sched = Scheduler.__new__(Scheduler)
    sched._store = _FakeScheduleStore()
    sched._state = StateStore(state_dir=tmp_path)
    sched._lock = threading.Lock()
    sched._started = False
    return sched


def _snapshot(state):
    return (state.status, state.current_stage, len(state.proposals))


def _job(workflow_id: str) -> ScheduledJob:
    return ScheduledJob(
        id="job-wf-step",
        name="step workflow",
        cron="* * * * *",
        action_type=ScheduledActionType.RUN_WORKFLOW_STEP,
        action_payload={"workflow_id": workflow_id},
    )


def _stored_workflow(sched: Scheduler):
    state = sched._state.create()
    state.context = {"title": "scheduler step test", "goal": "advance one step", "scope": "orchestrator only"}
    sched._state.save(state)
    return state


def test_run_workflow_step_ok_and_advances(tmp_path: Path) -> None:
    sched = _make_scheduler(tmp_path)
    state = _stored_workflow(sched)
    before = _snapshot(state)
    job = _job(state.id)

    run = sched._fire(job)

    assert run.status == "ok", run.output
    assert job.last_status == "ok"
    assert "TypeError" not in run.output
    after_state = sched._state.load(state.id)
    assert after_state is not None
    assert _snapshot(after_state) != before


def test_run_workflow_step_unknown_workflow_records_error(tmp_path: Path) -> None:
    sched = _make_scheduler(tmp_path)
    job = _job("0123456789ab")

    run = sched._fire(job)

    assert run.status == "error"
    assert job.last_status == "error"
    assert "not found" in run.output


def test_run_workflow_step_from_running_event_loop(tmp_path: Path) -> None:
    sched = _make_scheduler(tmp_path)
    state = _stored_workflow(sched)
    before = _snapshot(state)

    async def _inside_loop():
        return sched._fire(_job(state.id))

    run = asyncio.run(_inside_loop())

    assert run.status == "ok", run.output
    after_state = sched._state.load(state.id)
    assert after_state is not None
    assert _snapshot(after_state) != before


def test_build_engine_uses_given_store(tmp_path: Path) -> None:
    from backend.workflows import WorkflowEngine
    from backend.workflows.factory import build_engine

    store = StateStore(state_dir=tmp_path)
    state = store.create()
    engine = build_engine(state, store)
    assert isinstance(engine, WorkflowEngine)
