"""Tests for backend/storage/schedules.py.

Structural checks plus behavioural checks against ScheduleStore(tmp_path).
Note: ScheduleStore.list_runs returns runs newest-first (fired_at descending)
and append_run keeps only the last 50 runs.
"""

import ast
import importlib
import inspect
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.models.scheduler import (  # noqa: E402
    CreateScheduledJobRequest,
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


def _get(obj, key):
    if isinstance(obj, dict):
        return obj.get(key)
    return getattr(obj, key, None)


def _as_dt(value):
    if isinstance(value, str):
        value = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value


def _params(fn):
    return [p for p in inspect.signature(fn).parameters.values() if p.kind not in (p.VAR_POSITIONAL, p.VAR_KEYWORD)]


def _create(store, name, cron="* * * * *"):
    fields = dict(
        name=name,
        cron=cron,
        action_type=ScheduledActionType.RECORD_MEMORY_NOTE,
        action_payload={"note": "hello"},
    )
    params = _params(store.create)
    if len(params) == 1:
        ann = str(params[0].annotation)
        if "Create" in ann or params[0].annotation is inspect.Parameter.empty and "req" in params[0].name:
            result = store.create(CreateScheduledJobRequest(**fields))
        else:
            result = store.create(ScheduledJob(**fields))
    else:
        result = store.create(**fields)
    if isinstance(result, str):
        return result
    job_id = _get(result, "id")
    assert job_id, f"create returned no id: {result!r}"
    return job_id


def _append(store, job_id, fired_at, output=""):
    run = ScheduledRun(job_id=job_id, fired_at=fired_at, status="ok", output=output)
    params = _params(store.append_run)
    if len(params) == 1:
        store.append_run(run)
    elif len(params) == 2:
        store.append_run(job_id, run)
    else:
        store.append_run(job_id=job_id, fired_at=fired_at, status="ok", output=output)


def _list_runs(store, job_id):
    names = {p.name: p for p in _params(store.list_runs)}
    kwargs = {}
    if "limit" in names:
        kwargs["limit"] = 1000
    required = [p for p in names.values() if p.default is p.empty and p.name != "limit"]
    if required or "job_id" in names:
        return list(store.list_runs(job_id, **kwargs))
    return [r for r in store.list_runs(**kwargs) if _get(r, "job_id") == job_id]


def _fired(runs):
    return [_as_dt(_get(r, "fired_at")) for r in runs]


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
    assert list(ScheduleStore(tmp_path).list()) == []


def test_create_round_trips_through_fresh_store(tmp_path):
    job_id = _create(ScheduleStore(tmp_path), "nightly", "30 2 * * *")
    jobs = list(ScheduleStore(tmp_path).list())
    matching = [j for j in jobs if _get(j, "id") == job_id]
    assert len(matching) == 1, jobs
    assert _get(matching[0], "name") == "nightly"
    assert _get(matching[0], "cron") == "30 2 * * *"


def test_two_creates_give_distinct_ids(tmp_path):
    store = ScheduleStore(tmp_path)
    a = _create(store, "a")
    b = _create(store, "b")
    assert a != b
    ids = {_get(j, "id") for j in store.list()}
    assert {a, b} <= ids


def test_delete_missing_returns_false(tmp_path):
    assert ScheduleStore(tmp_path).delete("does-not-exist") is False


def _overwrite_json_files(root, text):
    files = [p for p in root.rglob("*") if p.is_file() and p.suffix in (".json", ".jsonl")]
    assert files, "store wrote no json files under tmp_path"
    for p in files:
        p.write_text(text, encoding="utf-8")


@pytest.mark.parametrize("garbage", ["{not valid json", "[1, 2, 3]", '"a string"'])
def test_corrupt_or_non_dict_jobs_json_lists_empty(tmp_path, garbage):
    _create(ScheduleStore(tmp_path), "x")
    _overwrite_json_files(tmp_path, garbage)
    assert list(ScheduleStore(tmp_path).list()) == []


def test_list_runs_is_newest_first(tmp_path):
    store = ScheduleStore(tmp_path)
    job_id = _create(store, "runs")
    times = [BASE + timedelta(minutes=i) for i in range(3)]
    for i, t in enumerate(times):
        _append(store, job_id, t, output=f"run{i}")
    assert _fired(_list_runs(store, job_id)) == list(reversed(times))


def test_append_run_keeps_last_50(tmp_path):
    store = ScheduleStore(tmp_path)
    job_id = _create(store, "many")
    times = [BASE + timedelta(minutes=i) for i in range(52)]
    for t in times:
        _append(store, job_id, t)
    fired = _fired(_list_runs(store, job_id))
    assert len(fired) == 50
    assert fired == list(reversed(times[2:]))
    assert times[0] not in fired and times[1] not in fired
