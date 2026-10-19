"""Regression and behaviour tests for backend/services/scheduler.py.

Structural checks (AST and import identity) guard against helpers shadowing
builder-job payload helpers. Behavioural checks exercise parse_cron: the
field mapping is checked directly, rollover is checked on a trigger with the
same fields but an explicit UTC timezone (so results never depend on the
machine timezone), parse_cron's own timezone is pinned to the local zone
APScheduler uses by default, and day-of-week numbering is checked against
standard cron (0/7 = Sunday) on parse_cron's own trigger with an anchor built
in the trigger's timezone. Fixed datetimes only: no wall clock, no sleep.
"""

import ast
import importlib
import logging
import sys
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from apscheduler.triggers.cron import CronTrigger

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

SCHEDULER_PATH = ROOT / "backend" / "services" / "scheduler.py"
SCHEDULER_MODULE = "backend.services.scheduler"


def _parse(path):
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def _duplicate_defs(tree):
    seen = {}
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            seen.setdefault(node.name, []).append(node.lineno)
    return {name: lines for name, lines in seen.items() if len(lines) > 1}


def _top_level_imports(tree):
    """Map bound name -> (source module, original name, absolute?, level)."""
    imported = {}
    for node in tree.body:
        if isinstance(node, ast.Import):
            for alias in node.names:
                bound = alias.asname or alias.name.split(".")[0]
                imported[bound] = (alias.name, None, True, 0, node.lineno)
        elif isinstance(node, ast.ImportFrom):
            for alias in node.names:
                if alias.name == "*":
                    continue
                bound = alias.asname or alias.name
                imported[bound] = (node.module or "", alias.name, node.level == 0, node.level, node.lineno)
    return imported


def _defs_shadowing_imports(tree):
    imported = _top_level_imports(tree)
    clashes = {}
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and node.name in imported:
            clashes[node.name] = {"imported_at": imported[node.name][4], "defined_at": node.lineno}
    return clashes


def _resolve_from_module(module, level, package):
    if level == 0:
        return module
    base = package.split(".")
    if level > 1:
        base = base[: -(level - 1)]
    prefix = ".".join(base)
    return f"{prefix}.{module}" if module else prefix


def test_scheduler_module_exists():
    assert SCHEDULER_PATH.is_file(), f"missing {SCHEDULER_PATH}"


def test_scheduler_has_no_duplicate_top_level_definitions():
    dupes = _duplicate_defs(_parse(SCHEDULER_PATH))
    assert not dupes, f"top-level names defined more than once (later def silently wins): {dupes}"


def test_scheduler_definitions_do_not_shadow_imported_helpers():
    clashes = _defs_shadowing_imports(_parse(SCHEDULER_PATH))
    assert not clashes, f"scheduler defines names that shadow imported helpers: {clashes}"


def test_scheduler_module_imports_cleanly():
    importlib.import_module(SCHEDULER_MODULE)


def test_imported_helpers_are_not_rebound_at_runtime():
    """Each name imported via `from X import y` must still be X.y after import.

    This catches shadowing that the AST check misses, e.g. a later
    assignment or a decorator that replaces the imported builder-job helper.
    """
    scheduler = importlib.import_module(SCHEDULER_MODULE)
    package = SCHEDULER_MODULE.rsplit(".", 1)[0]
    rebound = {}
    for bound, (module, original, _absolute, level, lineno) in _top_level_imports(_parse(SCHEDULER_PATH)).items():
        if original is None:
            continue
        source_name = _resolve_from_module(module, level, package)
        try:
            source = importlib.import_module(source_name)
        except ImportError:
            continue
        if not hasattr(source, original):
            # `from pkg import submodule` style imports
            continue
        expected = getattr(source, original)
        actual = getattr(scheduler, bound, None)
        if actual is not expected:
            rebound[bound] = f"imported from {source_name} at line {lineno} but rebound in scheduler"
    assert not rebound, rebound


# ---------------------------------------------------------------------------
# parse_cron behaviour
# ---------------------------------------------------------------------------


def _parse_cron():
    return importlib.import_module(SCHEDULER_MODULE).parse_cron


def _fields(trigger):
    return {f.name: str(f) for f in trigger.fields}


def _utc_trigger(expr):
    """parse_cron(expr) rebuilt field-for-field with an explicit UTC timezone."""
    trigger = _parse_cron()(expr)
    assert isinstance(trigger, CronTrigger)
    return CronTrigger(timezone=timezone.utc, **_fields(trigger))


def _at(hour, minute, second=30):
    return datetime(2026, 1, 15, hour, minute, second, tzinfo=timezone.utc)


@pytest.mark.parametrize("expr", ["not a cron", "* * *", "61 * * * *", "* 25 * * *"])
def test_parse_cron_invalid_raises_value_error(expr):
    with pytest.raises(ValueError):
        _parse_cron()(expr)


def test_parse_cron_maps_five_fields_in_order():
    fields = _fields(_parse_cron()("5 4 3 2 1"))
    assert fields["minute"] == "5"
    assert fields["hour"] == "4"
    assert fields["day"] == "3"
    assert fields["month"] == "2"
    # cron dow 1 is Monday; translated to an explicit day name.
    assert fields["day_of_week"] == "mon"
    assert fields["second"] == "0"


def test_parse_cron_strips_whitespace():
    fields = _fields(_parse_cron()("  30 2 * * *  "))
    assert (fields["minute"], fields["hour"]) == ("30", "2")


def test_parse_cron_uses_apscheduler_default_local_timezone():
    # APScheduler 3.x CronTrigger falls back to tzlocal.get_localzone() when no
    # timezone is given; parse_cron must keep that local-time behaviour.
    from tzlocal import get_localzone

    trigger = _parse_cron()("0 9 * * *")
    assert str(trigger.timezone) == str(get_localzone())
    assert str(trigger.timezone) == str(CronTrigger(minute="0").timezone)


def test_next_fire_minute_rollover():
    trigger = _utc_trigger("* * * * *")
    now = _at(10, 41)
    nxt = trigger.get_next_fire_time(None, now)
    assert nxt == datetime(2026, 1, 15, 10, 42, 0, tzinfo=timezone.utc)
    assert nxt - now == timedelta(seconds=30)


def test_next_fire_hour_rollover():
    trigger = _utc_trigger("* * * * *")
    nxt = trigger.get_next_fire_time(None, _at(10, 59))
    assert nxt == datetime(2026, 1, 15, 11, 0, 0, tzinfo=timezone.utc)


def test_next_fire_top_of_hour():
    trigger = _utc_trigger("0 * * * *")
    nxt = trigger.get_next_fire_time(None, _at(10, 30))
    assert nxt == datetime(2026, 1, 15, 11, 0, 0, tzinfo=timezone.utc)


def test_next_fire_day_rollover():
    trigger = _utc_trigger("30 2 * * *")
    nxt = trigger.get_next_fire_time(None, _at(23, 0))
    assert nxt == datetime(2026, 1, 16, 2, 30, 0, tzinfo=timezone.utc)


def test_next_fire_same_day_when_time_not_yet_passed():
    trigger = _utc_trigger("30 2 * * *")
    nxt = trigger.get_next_fire_time(None, _at(1, 0))
    assert nxt == datetime(2026, 1, 15, 2, 30, 0, tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# Day-of-week numbering (standard cron: 0/7 = Sunday, 1 = Monday)
# ---------------------------------------------------------------------------

_WEEKDAY = {0: "mon", 1: "tue", 2: "wed", 3: "thu", 4: "fri", 5: "sat", 6: "sun"}
_ALL_DAYS = {"sun", "mon", "tue", "wed", "thu", "fri", "sat"}


def _localize(tz, naive):
    if hasattr(tz, "localize"):  # pytz-style zone
        return tz.localize(naive)
    return naive.replace(tzinfo=tz)


def _dow_trigger_and_anchor(dow):
    """parse_cron('0 9 * * <dow>') and Thursday 2026-01-01 12:00 in its timezone."""
    trigger = _parse_cron()(f"0 9 * * {dow}")
    anchor = _localize(trigger.timezone, datetime(2026, 1, 1, 12, 0, 0))
    assert anchor.weekday() == 3  # Thursday
    return trigger, anchor


def _fires(dow, count):
    trigger, anchor = _dow_trigger_and_anchor(dow)
    out = []
    prev = None
    now = anchor
    for _ in range(count):
        nxt = trigger.get_next_fire_time(prev, now)
        assert nxt is not None
        out.append(nxt)
        prev = nxt
        now = nxt + timedelta(seconds=1)
    return out


@pytest.mark.parametrize(
    "dow, expected",
    [
        ("0", "sun"),
        ("7", "sun"),
        ("1", "mon"),
        ("6", "sat"),
        ("mon", "mon"),
        ("MON", "mon"),
    ],
)
def test_dow_single_value_next_fire_weekday(dow, expected):
    nxt = _fires(dow, 1)[0]
    assert _WEEKDAY[nxt.weekday()] == expected
    assert (nxt.hour, nxt.minute) == (9, 0)


def test_dow_sunday_fires_on_first_sunday_after_anchor():
    nxt = _fires("0", 1)[0]
    assert nxt.date() == datetime(2026, 1, 4).date()


def test_dow_weekday_range_fires_friday_then_monday():
    first, second = _fires("1-5", 2)
    assert first.date() == datetime(2026, 1, 2).date()
    assert _WEEKDAY[first.weekday()] == "fri"
    assert second.date() == datetime(2026, 1, 5).date()
    assert _WEEKDAY[second.weekday()] == "mon"


@pytest.mark.parametrize(
    "dow, expected",
    [
        ("*/2", {"sun", "tue", "thu", "sat"}),
        ("0,3", {"sun", "wed"}),
        ("mon-fri/2", {"mon", "wed", "fri"}),
        ("sun-sat", _ALL_DAYS),
        ("sun-wed", {"sun", "mon", "tue", "wed"}),
        ("MON-Fri", {"mon", "tue", "wed", "thu", "fri"}),
        ("0-7", _ALL_DAYS),
        ("5-7", {"fri", "sat", "sun"}),
        ("1-5/2", {"mon", "wed", "fri"}),
        ("mon/2", {"mon", "wed", "fri"}),
        ("mon-5", {"mon", "tue", "wed", "thu", "fri"}),
        ("sat,sun", {"sat", "sun"}),
    ],
)
def test_dow_sets_of_days(dow, expected):
    weekdays = {_WEEKDAY[f.weekday()] for f in _fires(dow, 12)}
    assert weekdays == expected


def test_dow_star_is_unchanged():
    assert _fields(_parse_cron()("0 9 * * *"))["day_of_week"] == "*"


@pytest.mark.parametrize(
    "dow, expected",
    [
        ("mon-fri/2", "mon,wed,fri"),
        ("sun-wed", "sun,mon,tue,wed"),
        ("MON-Fri", "mon,tue,wed,thu,fri"),
        ("5-7", "fri,sat,sun"),
        ("0-7", "sun,mon,tue,wed,thu,fri,sat"),
    ],
)
def test_dow_translated_field(dow, expected):
    assert _fields(_parse_cron()(f"0 9 * * {dow}"))["day_of_week"] == expected


@pytest.mark.parametrize(
    "dow",
    ["5-1", "8", "0-8", "*/0", "fri-sun", "sat-sun", "fri#1", "L", "5L", "xyz", "monday", "mon-", "mon/x"],
)
def test_dow_invalid_raises_value_error(dow):
    with pytest.raises(ValueError):
        _parse_cron()(f"0 9 * * {dow}")


@pytest.mark.parametrize("dow", ["FRI#1", "Xyz", "Fri-Sun", "5L"])
def test_dow_error_names_item_as_written(dow):
    with pytest.raises(ValueError) as excinfo:
        _parse_cron()(f"0 9 * * {dow}")
    assert repr(dow) in str(excinfo.value)


# ---------------------------------------------------------------------------
# Scheduler._fire: action and storage failures never escape the scheduler
# ---------------------------------------------------------------------------

SCHEDULER_LOGGER = "orchestrator.scheduler"


class _InMemoryScheduleStore:
    """In-memory stand-in for ScheduleStore with one-shot failure switches.

    Mirrors ScheduleStore's method names (create/get/update/list/delete/
    append_run/list_runs). list_runs is newest-first, like the real store.
    Each ``fail_*_once`` switch makes the next call raise OSError('disk full')
    and then resets itself.
    """

    def __init__(self):
        self.jobs = {}
        self.runs = []
        self.fail_get_once = False
        self.fail_append_once = False
        self.fail_update_once = False

    def _maybe_fail(self, flag):
        if getattr(self, flag):
            setattr(self, flag, False)
            raise OSError("disk full")

    def create(self, job):
        self.jobs[job.id] = job.model_copy(deep=True)
        return job.model_copy(deep=True)

    def get(self, job_id):
        self._maybe_fail("fail_get_once")
        job = self.jobs.get(job_id)
        return job.model_copy(deep=True) if job is not None else None

    def update(self, job):
        self._maybe_fail("fail_update_once")
        if job.id not in self.jobs:
            raise FileNotFoundError(job.id)
        self.jobs[job.id] = job.model_copy(deep=True)
        return job.model_copy(deep=True)

    def list(self):
        return [j.model_copy(deep=True) for j in self.jobs.values()]

    def delete(self, job_id):
        return self.jobs.pop(job_id, None) is not None

    def append_run(self, run):
        self._maybe_fail("fail_append_once")
        self.runs.append(run)

    def list_runs(self, job_id):
        return [r for r in reversed(self.runs) if r.job_id == job_id]


def _make_scheduler(store):
    """Real Scheduler built without __init__ (no APScheduler, no disk).

    Every attribute _fire/run_now/_job_lock touch is set explicitly.
    """
    scheduler_mod = importlib.import_module(SCHEDULER_MODULE)
    sched = scheduler_mod.Scheduler.__new__(scheduler_mod.Scheduler)
    sched._sched = None
    sched._store = store
    sched._prompts = None
    sched._memory = None
    sched._state = None
    sched._lock = threading.Lock()
    sched._started = False
    sched._job_locks = {}
    return sched


def _raising_job(store, job_id="job-fire-1"):
    """A fire_saved_prompt job with an empty payload: _dispatch_action raises ValueError."""
    from backend.models.scheduler import ScheduledActionType, ScheduledJob

    job = ScheduledJob(
        id=job_id,
        name="raises",
        cron="* * * * *",
        action_type=ScheduledActionType.FIRE_SAVED_PROMPT,
        action_payload={},
    )
    return store.create(job)


def _lock_is_free(sched, job_id):
    lock = sched._job_lock(job_id)
    acquired = lock.acquire(blocking=False)
    if acquired:
        lock.release()
    return acquired


def _warnings(caplog):
    return [rec.getMessage() for rec in caplog.records if rec.name == SCHEDULER_LOGGER]


@pytest.fixture
def fire_env(caplog):
    caplog.set_level(logging.WARNING, logger=SCHEDULER_LOGGER)
    store = _InMemoryScheduleStore()
    sched = _make_scheduler(store)
    job = _raising_job(store)
    return store, sched, job


def test_raising_job_is_recorded_as_error_run(fire_env, caplog):
    store, sched, job = fire_env
    run = sched.run_now(job.id)
    assert run is not None
    assert run.status == "error"
    assert run.output.startswith("ValueError: fire_saved_prompt requires action_payload.prompt_id")
    assert [r.status for r in store.list_runs(job.id)] == ["error"]
    assert store.get(job.id).last_status == "error"
    assert store.get(job.id).last_output.startswith("ValueError:")
    assert any("failed" in msg for msg in _warnings(caplog))
    assert _lock_is_free(sched, job.id)


def test_raising_job_releases_overlap_lock_and_next_run_succeeds(fire_env, monkeypatch):
    store, sched, job = fire_env
    first = sched.run_now(job.id)
    assert first.status == "error"
    assert _lock_is_free(sched, job.id)

    monkeypatch.setattr(sched, "_dispatch_action", lambda latest: "done")
    second = sched.run_now(job.id)
    assert second.status == "ok"
    assert second.output == "done"
    # newest-first
    assert [r.status for r in store.list_runs(job.id)] == ["ok", "error"]
    assert store.get(job.id).last_status == "ok"
    assert _lock_is_free(sched, job.id)


def test_store_get_failure_becomes_error_run_without_dispatch(fire_env, monkeypatch, caplog):
    store, sched, job = fire_env
    calls = []
    monkeypatch.setattr(sched, "_dispatch_action", lambda latest: calls.append(latest.id) or "done")
    store.fail_get_once = True

    # Call _fire directly (run_now would consume the one-shot get failure first).
    run = sched._fire(job)

    assert run.status == "error"
    assert run.output.startswith("OSError: disk full")
    assert calls == []
    assert [r.status for r in store.list_runs(job.id)] == ["error"]
    assert store.list_runs(job.id)[0].output.startswith("OSError: disk full")
    assert store.get(job.id).last_status == "error"
    assert any("failed" in msg and "OSError: disk full" in msg for msg in _warnings(caplog))
    assert _lock_is_free(sched, job.id)


def test_append_run_failure_does_not_propagate(fire_env, monkeypatch, caplog):
    store, sched, job = fire_env
    monkeypatch.setattr(sched, "_dispatch_action", lambda latest: "done")
    store.fail_append_once = True

    run = sched.run_now(job.id)

    assert run is not None
    assert run.status == "ok"
    assert store.list_runs(job.id) == []
    assert any("could not record run" in msg and "disk full" in msg for msg in _warnings(caplog))
    assert store.get(job.id).last_status == "ok"
    assert _lock_is_free(sched, job.id)

    again = sched.run_now(job.id)
    assert again.status == "ok"
    assert [r.status for r in store.list_runs(job.id)] == ["ok"]


def test_update_failure_does_not_propagate(fire_env, monkeypatch, caplog):
    store, sched, job = fire_env
    monkeypatch.setattr(sched, "_dispatch_action", lambda latest: "done")
    store.fail_update_once = True

    run = sched.run_now(job.id)

    assert run is not None
    assert run.status == "ok"
    assert [r.status for r in store.list_runs(job.id)] == ["ok"]
    assert any("could not update last-run fields" in msg and "disk full" in msg for msg in _warnings(caplog))
    # The update never landed, so the stored job is untouched.
    assert store.get(job.id).last_status is None
    assert _lock_is_free(sched, job.id)

    again = sched.run_now(job.id)
    assert again.status == "ok"
    assert store.get(job.id).last_status == "ok"


def test_concurrent_fire_is_still_recorded_as_skipped(fire_env, monkeypatch, caplog):
    store, sched, job = fire_env
    calls = []
    monkeypatch.setattr(sched, "_dispatch_action", lambda latest: calls.append(latest.id) or "done")

    lock = sched._job_lock(job.id)
    assert lock.acquire(blocking=False)
    try:
        run = sched._fire(job)
    finally:
        lock.release()

    assert run.status == "error"
    assert run.output == f"job already running: {job.id}"
    assert calls == []
    assert [r.output for r in store.list_runs(job.id)] == [f"job already running: {job.id}"]
    assert store.get(job.id).last_status is None
    assert any("skipped: already running" in msg for msg in _warnings(caplog))
    assert _lock_is_free(sched, job.id)


# ---------------------------------------------------------------------------
# Real on-disk ScheduleStore: a guarded append_run failure leaves the schedule
# record consistent and later runs still list newest-first.
# ---------------------------------------------------------------------------


def _real_schedule_store(tmp_path):
    """The ScheduleStore the scheduler itself uses, rooted in a temp dir."""
    scheduler_mod = importlib.import_module(SCHEDULER_MODULE)
    cls = getattr(scheduler_mod, "ScheduleStore", None)
    if cls is None:
        cls = importlib.import_module("backend.storage.schedules").ScheduleStore
    return cls(tmp_path)


def test_real_store_append_run_failure_keeps_schedule_and_newest_first_order(tmp_path, monkeypatch, caplog):
    caplog.set_level(logging.WARNING, logger=SCHEDULER_LOGGER)
    store = _real_schedule_store(tmp_path)
    sched = _make_scheduler(store)
    job = _raising_job(store, job_id="job-real-store-1")
    job_id = job.id

    outputs = iter(["first", "second", "third"])
    monkeypatch.setattr(sched, "_dispatch_action", lambda latest: next(outputs))

    original_append = store.append_run
    failed = []

    def flaky_append(run):
        if not failed:
            failed.append(run)
            raise OSError("disk full")
        return original_append(run)

    monkeypatch.setattr(store, "append_run", flaky_append)

    # 1) append_run raises once: _fire must not propagate and the job stays intact.
    first = sched._fire(job)
    assert first is not None
    assert first.status == "ok"
    assert first.output == "first"
    assert len(failed) == 1
    assert store.list_runs(job_id) == []
    after = store.get(job_id)
    assert after is not None
    assert after.id == job_id
    assert after.name == job.name
    assert after.cron == job.cron
    assert after.action_type == job.action_type
    assert after.enabled == job.enabled
    assert after.last_status == "ok"
    assert after.last_output == "first"
    assert after.last_run_at is not None
    assert any("could not record run" in msg and "disk full" in msg for msg in _warnings(caplog))
    assert _lock_is_free(sched, job_id)

    # 2) Following successful runs are recorded and listed newest-first.
    second = sched._fire(store.get(job_id))
    assert second.status == "ok"
    assert second.output == "second"
    assert [r.output for r in store.list_runs(job_id)] == ["second"]

    third = sched._fire(store.get(job_id))
    assert third.status == "ok"
    assert third.output == "third"

    runs = store.list_runs(job_id)
    assert [r.output for r in runs] == ["third", "second"]
    assert all(r.job_id == job_id for r in runs)
    assert all(r.status == "ok" for r in runs)
    assert runs[0].fired_at >= runs[1].fired_at
    assert "first" not in [r.output for r in runs]

    final = store.get(job_id)
    assert final.last_status == "ok"
    assert final.last_output == "third"
    assert final.cron == job.cron
    assert _lock_is_free(sched, job_id)
