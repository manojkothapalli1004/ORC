"""Regression and behaviour tests for backend/services/scheduler.py.

Structural checks (AST and import identity) guard against helpers shadowing
builder-job payload helpers. Behavioural checks exercise parse_cron and the
returned trigger with fixed aware datetimes: no wall clock, no sleep.
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


@pytest.mark.parametrize("expr", ["not a cron", "* * *", "61 * * * *", "* 25 * * *"])
def test_parse_cron_invalid_raises_value_error(expr):
    with pytest.raises(ValueError):
        _parse_cron()(expr)


def _local_base(trigger, hour, minute, second=30):
    """A fixed aware datetime on 2026-01-15 at the given wall time in the trigger's tz."""
    tz = getattr(trigger, "timezone", None) or timezone.utc
    anchor = datetime(2026, 1, 15, 12, 0, 0, tzinfo=timezone.utc).astimezone(tz)
    return anchor.replace(hour=hour, minute=minute, second=second, microsecond=0), tz


def test_next_fire_minute_rollover():
    trigger = _parse_cron()("* * * * *")
    now, tz = _local_base(trigger, 10, 41)
    nxt = trigger.get_next_fire_time(None, now)
    assert nxt is not None
    local = nxt.astimezone(tz)
    assert (local.hour, local.minute, local.second) == (10, 42, 0)
    assert nxt > now
    assert nxt - now == timedelta(seconds=30)


def test_next_fire_hour_rollover():
    trigger = _parse_cron()("* * * * *")
    now, tz = _local_base(trigger, 10, 59)
    nxt = trigger.get_next_fire_time(None, now)
    local = nxt.astimezone(tz)
    assert (local.day, local.hour, local.minute, local.second) == (15, 11, 0, 0)


def test_next_fire_top_of_hour():
    trigger = _parse_cron()("0 * * * *")
    now, tz = _local_base(trigger, 10, 30)
    nxt = trigger.get_next_fire_time(None, now)
    local = nxt.astimezone(tz)
    assert (local.day, local.hour, local.minute) == (15, 11, 0)


def test_next_fire_day_rollover():
    trigger = _parse_cron()("30 2 * * *")
    now, tz = _local_base(trigger, 23, 0)
    nxt = trigger.get_next_fire_time(None, now)
    local = nxt.astimezone(tz)
    assert (local.month, local.day, local.hour, local.minute) == (1, 16, 2, 30)
