"""Scheduler job runs can't overlap or overwrite concurrent job edits."""

from __future__ import annotations

import ast
import json
import os
import stat
import threading
from datetime import datetime, timezone
from pathlib import Path

import pytest

import backend.storage.atomic as atomic_mod
import backend.storage.store as store_mod
from backend.models.scheduler import ScheduledActionType, ScheduledJob, ScheduledRun
from backend.services.scheduler import Scheduler
from backend.storage.schedules import _MAX_RUNS_PER_JOB, ScheduleStore
from backend.storage.store import StateStore

ROOT = Path(__file__).resolve().parents[1]


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


@pytest.fixture
def umask_022(monkeypatch):
    monkeypatch.setattr(atomic_mod, "_UMASK", 0o022)


def _mode(p: Path) -> int:
    return stat.S_IMODE(os.stat(p).st_mode)


def _make_scheduler(tmp_path: Path) -> Scheduler:
    sched = Scheduler.__new__(Scheduler)
    sched._store = ScheduleStore(root_dir=tmp_path / "sched")
    sched._state = StateStore(state_dir=tmp_path / "state")
    sched._sched = _FakeAps()
    sched._lock = threading.Lock()
    sched._started = False
    return sched


def _job(name: str = "concurrency") -> ScheduledJob:
    return ScheduledJob(
        name=name,
        cron="* * * * *",
        action_type=ScheduledActionType.FIRE_SAVED_PROMPT,
        action_payload={"prompt_id": "unused"},
    )


def _create_job(sched: Scheduler) -> ScheduledJob:
    return sched._store.create(_job())


def _run(job_id: str = "job-x") -> ScheduledRun:
    return ScheduledRun(job_id=job_id, fired_at=datetime.now(timezone.utc), status="ok", output="x")


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


# -------- file modes --------


def _write_all_new(tmp_path: Path) -> list[Path]:
    sched = ScheduleStore(root_dir=tmp_path / "sched")
    sched.create(_job())
    sched.append_run(_run())
    state_store = StateStore(state_dir=tmp_path / "state")
    state = state_store.create()
    return [
        tmp_path / "sched" / "schedules.json",
        tmp_path / "sched" / "scheduler_runs" / "job-x.json",
        tmp_path / "state" / f"{state.id}.json",
    ]


def test_new_files_use_umask_022(tmp_path: Path, umask_022) -> None:
    for p in _write_all_new(tmp_path):
        assert _mode(p) == 0o644, p


def test_new_files_use_umask_027(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(atomic_mod, "_UMASK", 0o027)
    for p in _write_all_new(tmp_path):
        assert _mode(p) == 0o640, p


def test_existing_mode_is_kept(tmp_path: Path, umask_022) -> None:
    sched = ScheduleStore(root_dir=tmp_path / "sched")
    sched.create(_job("a"))
    jobs_file = tmp_path / "sched" / "schedules.json"
    os.chmod(jobs_file, 0o640)
    sched.create(_job("b"))
    assert _mode(jobs_file) == 0o640
    assert len(sched.list()) == 2

    sched.append_run(_run())
    runs_file = tmp_path / "sched" / "scheduler_runs" / "job-x.json"
    os.chmod(runs_file, 0o640)
    sched.append_run(_run())
    assert _mode(runs_file) == 0o640
    assert len(sched.list_runs("job-x")) == 2

    state_store = StateStore(state_dir=tmp_path / "state")
    state = state_store.create()
    state_file = tmp_path / "state" / f"{state.id}.json"
    os.chmod(state_file, 0o640)
    state_store.save(state)
    assert _mode(state_file) == 0o640


def test_special_bits_are_masked(tmp_path: Path, umask_022) -> None:
    sched = ScheduleStore(root_dir=tmp_path / "sched")
    sched.create(_job("a"))
    jobs_file = tmp_path / "sched" / "schedules.json"
    os.chmod(jobs_file, 0o4755)
    sched.create(_job("b"))
    assert _mode(jobs_file) == 0o755

    state_store = StateStore(state_dir=tmp_path / "state")
    state = state_store.create()
    state_file = tmp_path / "state" / f"{state.id}.json"
    os.chmod(state_file, 0o4755)
    state_store.save(state)
    assert _mode(state_file) == 0o755


def _is_umask_call(node: ast.AST) -> bool:
    if not isinstance(node, ast.Call):
        return False
    func = node.func
    if isinstance(func, ast.Attribute) and func.attr == "umask":
        return True
    if isinstance(func, ast.Name) and func.id == "umask":
        return True
    return False


def _umask_calls(tree: ast.AST) -> list[tuple[int, bool]]:
    """Return (lineno, inside_function) for every umask call."""
    found: list[tuple[int, bool]] = []

    def _walk(node: ast.AST, in_func: bool) -> None:
        if _is_umask_call(node):
            found.append((node.lineno, in_func))
        child_in_func = in_func or isinstance(
            node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)
        )
        for child in ast.iter_child_nodes(node):
            _walk(child, child_in_func)

    _walk(tree, False)
    return found


def test_umask_only_called_at_import_in_atomic() -> None:
    atomic_path = (ROOT / "backend" / "storage" / "atomic.py").resolve()
    offenders = []
    atomic_calls: list[tuple[int, bool]] = []
    for py in sorted((ROOT / "backend").rglob("*.py")):
        tree = ast.parse(py.read_text(encoding="utf-8"), filename=str(py))
        calls = _umask_calls(tree)
        if py.resolve() == atomic_path:
            atomic_calls = calls
            offenders.extend(f"{py}:{ln} (inside function)" for ln, inf in calls if inf)
        else:
            offenders.extend(f"{py}:{ln}" for ln, _ in calls)
    assert not offenders, f"os.umask called outside atomic.py module level: {offenders}"
    assert atomic_calls, "atomic.py should read the umask at import"
