"""Scheduler job runs can't overlap or overwrite concurrent job edits."""

from __future__ import annotations

import json
import threading
from datetime import datetime, timezone
from pathlib import Path

import pytest

import backend.storage.store as store_mod
from backend.models.scheduler import ScheduledActionType, ScheduledJob, ScheduledRun
from backend.services.scheduler import Scheduler
from backend.storage.schedules import _MAX_RUNS_PER_JOB, ScheduleStore
from backend.storage.store import StateStore


class _NullIndex:
    def upsert(self, *args, **kwargs):
        return None

    def delete(self, *args, **kwargs):
        return None


class _FakeAps:
    def remove_job(self, job_id):
        return None

    def get_job(self, job_id):
        return None

    def add_job(self, *args, **kwargs):
        return None


@pytest.fixture(autouse=True)
def _hermetic(monkeypatch):
    monkeypatch.setattr(store_mod, "get_search_index", lambda: _NullIndex())


def _make_scheduler(tmp_path: Path) -> Scheduler:
    sched = Scheduler.__new__(Scheduler)
    sched._store = ScheduleStore(root_dir=tmp_path / "sched")
    sched._state = StateStore(state_dir=tmp_path / "state")
    sched._sched = _FakeAps()
    sched._lock = threading.Lock()
    sched._started = False
    return sched


def _create_job(sched: Scheduler) -> ScheduledJob:
    return sched._store.create(
        ScheduledJob(
            name="concurrency",
            cron="* * * * *",
            action_type=ScheduledActionType.FIRE_SAVED_PROMPT,
            action_payload={"prompt_id": "unused"},
        )
    )


def test_second_fire_while_running_is_rejected(tmp_path: Path) -> None:
    sched = _make_scheduler(tmp_path)
    job = _create_job(sched)
    started = threading.Event()
    release = threading.Event()
    calls = []

    def _blocking_dispatch(j):
        calls.append(j.id)
        started.set()
        release.wait(2)
        return "done"

    sched._dispatch_action = _blocking_dispatch
    results = {}
    t = threading.Thread(target=lambda: results.__setitem__("first", sched._fire(job)))
    t.start()
    try:
        assert started.wait(2)
        second = sched._fire(job)
    finally:
        release.set()
        t.join(5)
    assert not t.is_alive()

    assert second.status == "error"
    assert second.output == f"job already running: {job.id}"
    assert len(calls) == 1
    assert results["first"].status == "ok"
    assert results["first"].output == "done"

    stored = sched._store.get(job.id)
    assert stored is not None
    assert stored.last_status == "ok"
    runs = sched._store.list_runs(job.id)
    assert sorted(r.status for r in runs) == ["error", "ok"]

    # Lock is released afterwards: a later fire runs normally.
    third = sched._fire(job)
    assert third.status == "ok"
    assert len(calls) == 2


def test_disable_during_run_survives(tmp_path: Path) -> None:
    sched = _make_scheduler(tmp_path)
    job = _create_job(sched)

    def _dispatch_that_disables(j):
        updated = sched.update(job.id, enabled=False, name="renamed")
        assert updated is not None
        return "done"

    sched._dispatch_action = _dispatch_that_disables
    run = sched._fire(job)

    assert run.status == "ok"
    stored = sched._store.get(job.id)
    assert stored is not None
    assert stored.enabled is False
    assert stored.name == "renamed"
    assert stored.last_status == "ok"
    assert stored.last_output == "done"
    assert stored.last_run_at is not None


def test_delete_during_run_does_not_resurrect(tmp_path: Path) -> None:
    sched = _make_scheduler(tmp_path)
    job = _create_job(sched)

    def _dispatch_that_deletes(j):
        assert sched.delete(job.id) is True
        return "done"

    sched._dispatch_action = _dispatch_that_deletes
    run = sched._fire(job)

    assert run.status == "ok"
    assert sched._store.get(job.id) is None


def test_concurrent_state_saves(tmp_path: Path) -> None:
    store = StateStore(state_dir=tmp_path)
    state = store.create()
    errors = []

    def _worker():
        local = state.model_copy(deep=True)
        try:
            for _ in range(50):
                store.save(local)
        except Exception as exc:  # pragma: no cover - reported below
            errors.append(exc)

    threads = [threading.Thread(target=_worker) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(10)

    assert not errors
    raw = (tmp_path / f"{state.id}.json").read_text()
    json.loads(raw)
    loaded = store.load(state.id)
    assert loaded is not None and loaded.id == state.id
    assert not list(tmp_path.glob("*.tmp"))
    assert store.list_ids() == [state.id]


def test_concurrent_append_run(tmp_path: Path) -> None:
    store = ScheduleStore(root_dir=tmp_path)
    errors = []

    def _worker(tag: str):
        try:
            for i in range(50):
                store.append_run(
                    ScheduledRun(
                        job_id="job-x",
                        fired_at=datetime.now(timezone.utc),
                        status="ok",
                        output=f"{tag}-{i}",
                    )
                )
        except Exception as exc:  # pragma: no cover - reported below
            errors.append(exc)

    threads = [threading.Thread(target=_worker, args=(f"t{n}",)) for n in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(10)

    assert not errors
    path = tmp_path / "scheduler_runs" / "job-x.json"
    data = json.loads(path.read_text())
    assert isinstance(data, list)
    assert len(data) == _MAX_RUNS_PER_JOB
    assert len(store.list_runs("job-x")) == _MAX_RUNS_PER_JOB
    assert not list((tmp_path / "scheduler_runs").glob("*.tmp"))
