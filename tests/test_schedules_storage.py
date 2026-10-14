"""Tests for backend/storage/schedules.py.

Structural checks plus behavioural checks against ScheduleStore(tmp_path).
The real API is called directly (no signature sniffing):

- create(ScheduledJob) -> ScheduledJob (id assigned)
- get(job_id) -> ScheduledJob | None
- update(ScheduledJob) -> ScheduledJob, FileNotFoundError for a missing id
- delete(job_id) -> bool, also removes the per-job run file
- append_run(ScheduledRun) -> None, keeps only the last 50 runs
- list_runs(job_id) -> list[ScheduledRun], newest-first by fired_at
"""

import ast
import importlib
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.models.scheduler import (  # noqa: E402
    ScheduledActionType,
    ScheduledJob,
    ScheduledRun,
)
from backend.storage.schedules import ScheduleStore  # noqa: E402

MODULE_PATH = ROOT / "backend" / "storage" / "schedules.py"
MODULE_NAME = "backend.storage.schedules"

BASE = datetime(2026, 1, 1, 0, 0, 0, tzinfo=timezone.utc)


def _parse(path):
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def _duplicate_defs(tree):
    seen = {}
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            seen.setdefault(node.name, []).append(node.lineno)
    return {name: lines for name, lines in seen.items() if len(lines) > 1}


def _defs_shadowing_imports(tree):
    imported = {}
    for node in tree.body:
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            for alias in node.names:
                if alias.name == "*":
                    continue
                imported[alias.asname or alias.name.split(".")[0]] = node.lineno
    return {
        node.name: (imported[node.name], node.lineno)
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and node.name in imported
    }


def _job(name, cron="* * * * *"):
    return ScheduledJob(
        name=name,
        cron=cron,
        action_type=ScheduledActionType.RECORD_MEMORY_NOTE,
        action_payload={"section": "notes", "note": "hello"},
    )


def _create(store, name, cron="* * * * *"):
    result = store.create(_job(name, cron))
    assert isinstance(result, ScheduledJob)
    assert result.id
    return result


def _run(job_id, fired_at, output=""):
    return ScheduledRun(job_id=job_id, fired_at=fired_at, status="ok", output=output)


def _run_file(tmp_path, job_id):
    return tmp_path / "scheduler_runs" / f"{job_id}.json"


def test_schedules_module_exists():
    assert MODULE_PATH.is_file(), f"missing {MODULE_PATH}"


def test_schedules_has_no_duplicate_top_level_definitions():
    dupes = _duplicate_defs(_parse(MODULE_PATH))
    assert not dupes, f"top-level names defined more than once: {dupes}"


def test_schedules_definitions_do_not_shadow_imports():
    clashes = _defs_shadowing_imports(_parse(MODULE_PATH))
    assert not clashes, f"definitions shadow imported names: {clashes}"


def test_schedules_module_imports_cleanly():
    importlib.import_module(MODULE_NAME)


def test_list_schedules_is_empty_for_fresh_state_dir(tmp_path):
    assert ScheduleStore(tmp_path).list() == []


def test_create_returns_job_with_id(tmp_path):
    job = _create(ScheduleStore(tmp_path), "nightly", "30 2 * * *")
    assert job.name == "nightly"
    assert job.cron == "30 2 * * *"


def test_create_round_trips_through_fresh_store(tmp_path):
    job = _create(ScheduleStore(tmp_path), "nightly", "30 2 * * *")
    jobs = ScheduleStore(tmp_path).list()
    matching = [j for j in jobs if j.id == job.id]
    assert len(matching) == 1, jobs
    assert isinstance(matching[0], ScheduledJob)
    assert matching[0].name == "nightly"
    assert matching[0].cron == "30 2 * * *"
    assert matching[0].action_type == ScheduledActionType.RECORD_MEMORY_NOTE
    assert matching[0].action_payload == {"section": "notes", "note": "hello"}


def test_two_creates_give_distinct_ids(tmp_path):
    store = ScheduleStore(tmp_path)
    a = _create(store, "a").id
    b = _create(store, "b").id
    assert a != b
    assert {j.id for j in store.list()} == {a, b}


def test_get_returns_created_job(tmp_path):
    job = _create(ScheduleStore(tmp_path), "fetch", "0 9 * * 1")
    got = ScheduleStore(tmp_path).get(job.id)
    assert isinstance(got, ScheduledJob)
    assert got.id == job.id
    assert got.name == "fetch"
    assert got.cron == "0 9 * * 1"


def test_get_missing_returns_none(tmp_path):
    assert ScheduleStore(tmp_path).get("does-not-exist") is None


def test_update_persists_changed_field(tmp_path):
    store = ScheduleStore(tmp_path)
    job = _create(store, "before")
    job.name = "after"
    job.enabled = False
    returned = store.update(job)
    assert isinstance(returned, ScheduledJob)
    reloaded = ScheduleStore(tmp_path).get(job.id)
    assert reloaded is not None
    assert reloaded.name == "after"
    assert reloaded.enabled is False


def test_update_missing_id_raises_file_not_found(tmp_path):
    store = ScheduleStore(tmp_path)
    ghost = _job("ghost")
    ghost.id = "missing00000"
    with pytest.raises(FileNotFoundError):
        store.update(ghost)
    assert store.get("missing00000") is None


def test_delete_missing_returns_false(tmp_path):
    assert ScheduleStore(tmp_path).delete("does-not-exist") is False


def test_delete_existing_removes_job_and_run_file(tmp_path):
    store = ScheduleStore(tmp_path)
    keep = _create(store, "keep")
    gone = _create(store, "gone")
    store.append_run(_run(gone.id, BASE))
    assert _run_file(tmp_path, gone.id).is_file()

    assert store.delete(gone.id) is True

    assert store.get(gone.id) is None
    assert [j.id for j in ScheduleStore(tmp_path).list()] == [keep.id]
    assert not _run_file(tmp_path, gone.id).exists()
    assert store.list_runs(gone.id) == []


def _overwrite_json_files(root, text):
    files = [p for p in root.rglob("*") if p.is_file() and p.suffix in (".json", ".jsonl")]
    assert files, "store wrote no json files under tmp_path"
    for p in files:
        p.write_text(text, encoding="utf-8")


@pytest.mark.parametrize("garbage", ["{not valid json", "[1, 2, 3]", '"a string"'])
def test_corrupt_or_non_dict_jobs_json_lists_empty(tmp_path, garbage):
    _create(ScheduleStore(tmp_path), "x")
    _overwrite_json_files(tmp_path, garbage)
    assert ScheduleStore(tmp_path).list() == []


def test_list_runs_empty_for_job_without_runs(tmp_path):
    store = ScheduleStore(tmp_path)
    job = _create(store, "quiet")
    assert store.list_runs(job.id) == []


def test_list_runs_is_newest_first(tmp_path):
    store = ScheduleStore(tmp_path)
    job = _create(store, "runs")
    times = [BASE + timedelta(minutes=i) for i in range(3)]
    for i, t in enumerate(times):
        store.append_run(_run(job.id, t, output=f"run{i}"))
    runs = store.list_runs(job.id)
    assert all(isinstance(r, ScheduledRun) for r in runs)
    assert [r.fired_at for r in runs] == list(reversed(times))
    assert [r.output for r in runs] == ["run2", "run1", "run0"]
    assert all(r.job_id == job.id for r in runs)


def test_list_runs_sorts_by_fired_at_not_append_order(tmp_path):
    store = ScheduleStore(tmp_path)
    job = _create(store, "out-of-order")
    newer = BASE + timedelta(hours=2)
    older = BASE + timedelta(hours=1)
    store.append_run(_run(job.id, newer, output="newer"))
    store.append_run(_run(job.id, older, output="older"))
    runs = ScheduleStore(tmp_path).list_runs(job.id)
    assert [r.fired_at for r in runs] == [newer, older]
    assert [r.output for r in runs] == ["newer", "older"]


def test_list_runs_is_scoped_to_job(tmp_path):
    store = ScheduleStore(tmp_path)
    a = _create(store, "a")
    b = _create(store, "b")
    store.append_run(_run(a.id, BASE, output="a"))
    store.append_run(_run(b.id, BASE + timedelta(minutes=1), output="b"))
    assert [r.output for r in store.list_runs(a.id)] == ["a"]
    assert [r.output for r in store.list_runs(b.id)] == ["b"]


def test_append_run_keeps_last_50(tmp_path):
    store = ScheduleStore(tmp_path)
    job = _create(store, "many")
    times = [BASE + timedelta(minutes=i) for i in range(52)]
    for t in times:
        store.append_run(_run(job.id, t))
    fired = [r.fired_at for r in store.list_runs(job.id)]
    assert len(fired) == 50
    assert fired == list(reversed(times[2:]))
    assert times[0] not in fired and times[1] not in fired
