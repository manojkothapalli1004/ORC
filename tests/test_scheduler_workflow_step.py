"""Scheduled run_workflow_step builds the workflow engine like the API does."""

from __future__ import annotations

import asyncio
import threading
from datetime import datetime, timezone
from pathlib import Path

import pytest

import backend.storage.store as store_mod
from backend.models.core import WorkflowStatus
from backend.models.scheduler import ScheduledActionType, ScheduledJob, ScheduledRun
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


def _assert_advanced_ok(after_state, before_proposals: int) -> None:
    assert after_state is not None
    assert after_state.status != WorkflowStatus.ERROR, after_state.error
    assert not after_state.error
    assert len(after_state.proposals) == before_proposals + 1


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
    _assert_advanced_ok(after_state, before[2])


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
    _assert_advanced_ok(after_state, before[2])


def test_run_workflow_step_engine_error_recorded_as_error(tmp_path: Path, monkeypatch) -> None:
    import backend.workflows.factory as factory_mod

    class _FailingEngine:
        async def step(self, state):
            state.status = WorkflowStatus.ERROR
            state.error = "boom from engine"
            return state

    monkeypatch.setattr(factory_mod, "build_engine", lambda state, store, registry=None: _FailingEngine())

    sched = _make_scheduler(tmp_path)
    state = _stored_workflow(sched)
    job = _job(state.id)

    run = sched._fire(job)

    assert run.status == "error", run.output
    assert job.last_status == "error"
    assert "workflow step failed" in run.output
    assert state.id in run.output
    assert "boom from engine" in run.output
    saved = sched._state.load(state.id)
    assert saved is not None
    assert saved.status == WorkflowStatus.ERROR
    assert saved.error == "boom from engine"


def test_build_engine_uses_given_store(tmp_path: Path) -> None:
    from backend.workflows import WorkflowEngine
    from backend.workflows.factory import build_engine

    store = StateStore(state_dir=tmp_path)
    state = store.create()
    engine = build_engine(state, store)
    assert isinstance(engine, WorkflowEngine)


def test_routes_engine_for_uses_factory(tmp_path: Path, monkeypatch) -> None:
    import backend.api.routes as routes

    calls = []
    sentinel = object()

    def _fake_build_engine(state, store, registry=None):
        calls.append((state, store, registry))
        return sentinel

    # raising=True (default): fails if routes does not use build_engine.
    monkeypatch.setattr(routes, "build_engine", _fake_build_engine)

    state = StateStore(state_dir=tmp_path).create()
    result = routes._engine_for(state)

    assert result is sentinel
    assert len(calls) == 1
    assert calls[0][0] is state
    assert calls[0][1] is routes.store
    assert calls[0][2] is routes.registry


def test_run_now_route_runs_off_event_loop(monkeypatch) -> None:
    import backend.api.routes as routes

    seen = {}

    class _FakeScheduler:
        def run_now(self, job_id):
            seen["thread"] = threading.current_thread()
            seen["job_id"] = job_id
            return ScheduledRun(
                job_id=job_id,
                fired_at=datetime.now(timezone.utc),
                status="ok",
                output="done",
            )

    monkeypatch.setattr(routes, "get_scheduler", lambda: _FakeScheduler())

    async def _call():
        seen["loop_thread"] = threading.current_thread()
        return await routes.run_scheduled_job_now("job-x")

    body = asyncio.run(_call())

    assert seen["job_id"] == "job-x"
    assert seen["thread"] is not seen["loop_thread"]
    assert body["job_id"] == "job-x"
    assert body["status"] == "ok"
    assert body["output"] == "done"


def test_run_now_route_404_when_missing(monkeypatch) -> None:
    from fastapi import HTTPException

    import backend.api.routes as routes

    class _FakeScheduler:
        def run_now(self, job_id):
            return None

    monkeypatch.setattr(routes, "get_scheduler", lambda: _FakeScheduler())

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(routes.run_scheduled_job_now("missing"))
    assert exc_info.value.status_code == 404
