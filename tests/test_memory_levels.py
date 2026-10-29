"""Derived memory levels: parsing, read views, generator, scheduler action, MCP tool.

Every store is rooted in tmp_path; no network; the wall clock is used only
where the spec says so (apply_patches, scheduler/MCP paths seeded with notes
that are older than 30 days on every date after 2026-08-31).
"""

from __future__ import annotations

import asyncio
import base64
import json
import threading
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from backend.app import app
from backend.api import routes
from backend import mcp_server
from backend.config import settings
from backend.storage.memory import (
    MemoryLevelStore,
    MemoryStore,
    MemoryNote,
    NOTE_HEADER_RE,
    render_memory_view,
    split_section_notes,
)
from backend.services.memory_levels import MemoryLevelRun, generate_memory_levels, plan_memory_levels
from backend.models.memory import ProjectMemory, MemoryUpdatePatch
from backend.models.scheduler import ScheduledActionType, ScheduledJob
from backend.providers.base import ProviderRequest, ProviderResponse
from backend.providers.mock_provider import MockProvider
from backend.providers.registry import ProviderRegistry
from backend.services.scheduler import Scheduler


_NOW = datetime(2026, 10, 29, 12, 0, 0, tzinfo=timezone.utc)
FRESH, MID, OLD = "2026-10-27T00:00:00Z", "2026-10-10T00:00:00Z", "2026-08-01T00:00:00Z"  # 2.5 d, 19.5 d, 89.5 d


def _block(stamp, source, body):
    return f"[{stamp} · {source}]\n{body}"


FRESH_BODY = "fresh body\n[2026-04-23 · look-alike] not a header"
DECISIONS = "Preamble line kept forever.\n\n" + "\n\n".join([
    _block(OLD, "s1", "old body line 1\nold body line 2"),
    _block(MID, "s2", "mid body"),
    _block(FRESH, "s3", FRESH_BODY),
])

# Two notes > 30 days old on any date after 2026-08-31, so tests that cannot
# pass `now` (scheduler, MCP) never depend on the wall clock.
OLD_ONLY = "\n\n".join(_block(OLD, f"o{i}", f"old body {i}") for i in range(2))


def _seed(tmp_path, decisions=DECISIONS) -> MemoryStore:
    store = MemoryStore(memory_dir=tmp_path)
    mem = ProjectMemory()
    mem.decisions = decisions
    mem.vision = "no headers here"
    store.save(mem)
    return store


class _FakeProvider:
    provider_name = "fake"
    is_available = True

    def __init__(self):
        self.calls = []

    async def complete(self, request):
        self.calls.append(request)
        lvl = request.metadata["level"]
        if lvl == "l1":
            text = "- a\n- b\n- c"
        else:
            text = f"One line for {request.metadata['stamp']}.\nsecond line dropped"
        return ProviderResponse(role=request.role, provider_name="fake", model_used="fake-1", content=text)


class _FailingProvider:
    provider_name = "fail"
    is_available = True

    def __init__(self):
        self.calls = 0

    async def complete(self, request):
        self.calls += 1
        raise RuntimeError("boom")


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


def _make_scheduler(tmp_path):
    sched = Scheduler.__new__(Scheduler)
    sched._store = _FakeScheduleStore()
    sched._memory = MemoryStore(memory_dir=tmp_path)
    sched._lock = threading.Lock()
    sched._started = False
    return sched


def _job(payload=None):
    return ScheduledJob(
        id="job-ml",
        name="levels",
        cron="* * * * *",
        action_type=ScheduledActionType.SUMMARIZE_MEMORY_LEVELS,
        action_payload=payload or {},
    )


def _canonical_bytes(store):
    return store.memory_file.read_bytes()


def _boom(*args, **kwargs):
    raise AssertionError("must not be called on the default path")


def _client(monkeypatch, store) -> TestClient:
    monkeypatch.setattr(settings, "local_network_only", False)
    monkeypatch.setattr(settings, "local_access_username", "ml-op")
    monkeypatch.setattr(settings, "local_access_password", "ml-pass")
    monkeypatch.setattr(routes, "memory_store", store)
    token = base64.b64encode(b"ml-op:ml-pass").decode("ascii")
    return TestClient(app, headers={"Authorization": f"Basic {token}"})


def _notes_by_stamp(store):
    _, notes = split_section_notes("decisions", store.load().decisions)
    return {n.stamp: n for n in notes}


def _records_c(store):
    by_stamp = _notes_by_stamp(store)
    records = {}
    for stamp, label in ((OLD, "old"), (MID, "mid"), (FRESH, "fresh")):
        n = by_stamp[stamp]
        records[n.key()] = {
            "section": n.section,
            "stamp": n.stamp,
            "source": n.source,
            "l1": {"text": f"L1-{label}"},
            "l2": {"text": f"L2-{label}"},
        }
    return records


# a)
def test_split_section_notes_matches_only_real_headers():
    preamble, notes = split_section_notes("decisions", DECISIONS)
    assert preamble == "Preamble line kept forever."
    assert len(notes) == 3
    assert [n.stamp for n in notes] == [OLD, MID, FRESH]
    assert [n.source for n in notes] == ["s1", "s2", "s3"]
    assert notes[2].body == "fresh body\n[2026-04-23 · look-alike] not a header"
    assert notes[0].header == f"[{OLD} · s1]"
    assert notes[0].key().startswith("decisions|2026-08-01T00:00:00Z|")
    same_a = MemoryNote("decisions", OLD, "x", f"[{OLD} · x]", "body one")
    same_b = MemoryNote("decisions", OLD, "x", f"[{OLD} · x]", "body two")
    assert same_a.key() != same_b.key()
    assert split_section_notes("vision", "no headers here") == ("no headers here", [])
    assert NOTE_HEADER_RE.search("[2026-04-23T · session_2026-04-23] JobAssist") is None
    assert split_section_notes("x", "[2026-13-45T00:00:00Z · bad]\nbody") == (
        "[2026-13-45T00:00:00Z · bad]\nbody",
        [],
    )


# b) NEGATIVE
def test_default_read_is_byte_identical_with_levels_present(tmp_path, monkeypatch):
    store = _seed(tmp_path)
    levels = MemoryLevelStore(memory_dir=tmp_path)
    fake = _FakeProvider()
    run = asyncio.run(generate_memory_levels(store, levels, fake, now=_NOW))
    assert run.generated == 3
    assert len(fake.calls) == 3
    assert levels.levels_file.exists()

    client = _client(monkeypatch, store)
    monkeypatch.setattr(routes, "render_memory_view", _boom)
    r = client.get("/api/memory")
    assert r.status_code == 200
    assert r.json()["decisions"] == DECISIONS
    assert r.json()["vision"] == "no headers here"
    r2 = client.get("/api/memory")
    assert r2.status_code == 200
    assert r.content == r2.content
    assert json.loads(store.memory_file.read_text())["decisions"] == DECISIONS

    monkeypatch.setattr(mcp_server, "_memory_store", store)
    monkeypatch.setattr(mcp_server, "_level_store", MemoryLevelStore(memory_dir=tmp_path))
    monkeypatch.setattr(mcp_server, "render_memory_view", _boom)
    out = mcp_server.get_canonical_memory()
    assert set(out) == {"sections", "updated_at"}
    assert out["sections"]["decisions"] == DECISIONS
    assert out["sections"]["vision"] == "no headers here"


# c)
def test_auto_view_picks_level_by_age_with_frozen_clock(tmp_path):
    store = _seed(tmp_path)
    levels = MemoryLevelStore(memory_dir=tmp_path)
    levels.save(_records_c(store))
    view = render_memory_view(store.load(), levels.load(), level="auto", now=_NOW)
    assert view["decisions"] == (
        "Preamble line kept forever.\n\n"
        + f"[{OLD} · s1] (L2)\nL2-old\n\n"
        + f"[{MID} · s2] (L1)\nL1-mid\n\n"
        + _block(FRESH, "s3", "fresh body\n[2026-04-23 · look-alike] not a header")
    )
    assert view["vision"] == "no headers here"
    for name in ("systems", "status", "preferences", "known_failures", "roadmap"):
        assert view[name] == ""
    later = render_memory_view(store.load(), levels.load(), level="auto", now=_NOW + timedelta(days=30))
    assert f"[{MID} · s2] (L2)\nL2-mid" in later["decisions"]
    assert "(L1)\nL1-mid" not in later["decisions"]


# d)
def test_auto_view_falls_back_to_l0_when_no_derived_level(tmp_path):
    store = _seed(tmp_path)
    mem = store.load()
    auto = render_memory_view(mem, {}, level="auto", now=_NOW)
    full = render_memory_view(mem, {}, level="full")
    assert auto == full
    assert auto["decisions"] == DECISIONS
    assert auto["vision"] == "no headers here"
    for name in ("systems", "status", "preferences", "known_failures", "roadmap"):
        assert auto[name] == ""

    old_note = _notes_by_stamp(store)[OLD]
    records = {old_note.key(): {"section": "decisions", "stamp": OLD, "source": "s1", "l1": {"text": "L1-old"}}}
    view = render_memory_view(mem, records, level="auto", now=_NOW)
    assert view["decisions"] == (
        "Preamble line kept forever.\n\n"
        + f"[{OLD} · s1] (L1)\nL1-old\n\n"
        + _block(MID, "s2", "mid body")
        + "\n\n"
        + _block(FRESH, "s3", FRESH_BODY)
    )


# e)
def test_auto_render_of_fresh_notes_equals_canonical(tmp_path):
    store = MemoryStore(memory_dir=tmp_path)
    for note in ("one", "two\nthree", "four"):
        store.apply_patches([MemoryUpdatePatch(section="status", note=note, source="t")])
    body = store.load().status
    assert body
    assert render_memory_view(store.load(), {}, level="auto")["status"] == body
    assert render_memory_view(store.load(), {}, level="full", since="2000-01-01")["status"] == body


# f)
def test_since_filters_notes_and_keeps_preamble(tmp_path, monkeypatch):
    store = _seed(tmp_path)
    mem = store.load()
    view = render_memory_view(mem, {}, level="full", since="2026-10-01")
    assert view["decisions"] == (
        "Preamble line kept forever.\n\n"
        + _block(MID, "s2", "mid body")
        + "\n\n"
        + _block(FRESH, "s3", FRESH_BODY)
    )
    assert render_memory_view(mem, {}, level="full", since="2030-01-01")["decisions"] == "Preamble line kept forever."
    boundary = render_memory_view(mem, {}, level="full", since="2026-10-27")
    assert boundary["decisions"] == "Preamble line kept forever.\n\n" + _block(FRESH, "s3", FRESH_BODY)
    with pytest.raises(ValueError) as since_err:
        render_memory_view(mem, {}, level="full", since="yesterday")
    assert "invalid since" in str(since_err.value)
    with pytest.raises(ValueError) as level_err:
        render_memory_view(mem, {}, level="l9")
    assert "invalid level" in str(level_err.value)

    client = _client(monkeypatch, store)
    assert client.get("/api/memory?since=yesterday").status_code == 400
    assert client.get("/api/memory?level=l9").status_code == 400
    r = client.get("/api/memory?level=auto")
    assert r.status_code == 200
    assert r.json()["decisions"] == DECISIONS
    assert r.json()["vision"] == "no headers here"


# g)
def test_job_generates_l1_l2_by_age_and_is_idempotent(tmp_path):
    store = _seed(tmp_path)
    levels = MemoryLevelStore(memory_dir=tmp_path)
    before = _canonical_bytes(store)
    fake = _FakeProvider()
    run = asyncio.run(generate_memory_levels(store, levels, fake, now=_NOW))
    assert [(c.metadata["stamp"], c.metadata["level"]) for c in fake.calls] == [
        (OLD, "l1"),
        (OLD, "l2"),
        (MID, "l1"),
    ]
    by_stamp = _notes_by_stamp(store)
    for call in fake.calls:
        assert call.role == "memory_summarizer"
        assert by_stamp[call.metadata["stamp"]].body in call.prompt
    assert (run.generated, run.errors, run.mock, run.pending) == (3, 0, 0, 0)
    records = levels.load()
    old_key, mid_key, fresh_key = by_stamp[OLD].key(), by_stamp[MID].key(), by_stamp[FRESH].key()
    assert records[old_key]["l1"]["text"] == "- a\n- b\n- c"
    assert records[old_key]["l2"]["text"] == f"One line for {OLD}."
    assert records[mid_key]["l1"]["text"] == "- a\n- b\n- c"
    assert "l2" not in records[mid_key]
    assert fresh_key not in records
    levels_bytes = levels.levels_file.read_bytes()

    fake2 = _FakeProvider()
    run2 = asyncio.run(generate_memory_levels(store, levels, fake2, now=_NOW))
    assert fake2.calls == []
    assert run2.generated == 0
    assert levels.levels_file.read_bytes() == levels_bytes
    assert _canonical_bytes(store) == before


# h)
def test_job_is_bounded_per_run(tmp_path):
    decisions = "\n\n".join(_block(OLD, f"s{i}", f"body {i}") for i in range(10))
    store = _seed(tmp_path, decisions=decisions)
    levels = MemoryLevelStore(memory_dir=tmp_path)
    _, notes = split_section_notes("decisions", store.load().decisions)
    assert len(plan_memory_levels(store.load(), {}, now=_NOW)) == 20

    fake = _FakeProvider()
    run = asyncio.run(generate_memory_levels(store, levels, fake, now=_NOW, max_notes=5))
    assert len(fake.calls) == 5
    assert run.generated == 5
    assert run.pending == 15
    assert [(c.metadata["key"], c.metadata["level"]) for c in fake.calls] == [
        (notes[0].key(), "l1"),
        (notes[0].key(), "l2"),
        (notes[1].key(), "l1"),
        (notes[1].key(), "l2"),
        (notes[2].key(), "l1"),
    ]

    fake2 = _FakeProvider()
    run2 = asyncio.run(generate_memory_levels(store, levels, fake2, now=_NOW, max_notes=5))
    assert len(fake2.calls) == 5
    assert run2.generated == 5
    assert run2.pending == 10


# i)
def test_provider_error_leaves_data_untouched(tmp_path):
    store = _seed(tmp_path)
    levels = MemoryLevelStore(memory_dir=tmp_path)
    mid_note = _notes_by_stamp(store)[MID]
    levels.save({mid_note.key(): {"section": "decisions", "stamp": MID, "source": "s2", "l1": {"text": "keep-me"}}})
    before_levels = levels.levels_file.read_bytes()
    before_canon = _canonical_bytes(store)

    failing = _FailingProvider()
    run = asyncio.run(generate_memory_levels(store, levels, failing, now=_NOW))
    assert failing.calls == 2
    assert (run.errors, run.generated, run.pending) == (2, 0, 2)
    assert levels.levels_file.read_bytes() == before_levels
    assert _canonical_bytes(store) == before_canon
    assert MemoryLevelStore.level_text(levels.load(), mid_note.key(), "l1") == "keep-me"

    many_dir = tmp_path / "many"
    many_store = _seed(many_dir, decisions="\n\n".join(_block(OLD, f"s{i}", f"body {i}") for i in range(10)))
    many_levels = MemoryLevelStore(memory_dir=many_dir)
    failing2 = _FailingProvider()
    run2 = asyncio.run(generate_memory_levels(many_store, many_levels, failing2, now=_NOW))
    assert failing2.calls == 3
    assert run2.errors == 3
    assert run2.generated == 0
    assert not many_levels.levels_file.exists()


# j)
def test_mock_provider_writes_nothing_and_run_is_ok(tmp_path, monkeypatch):
    store = _seed(tmp_path, decisions=OLD_ONLY)
    levels = MemoryLevelStore(memory_dir=tmp_path)
    run = asyncio.run(generate_memory_levels(store, levels, MockProvider(), now=_NOW))
    assert isinstance(run, MemoryLevelRun)
    assert (run.generated, run.errors, run.mock, run.pending) == (0, 0, 4, 4)
    assert not levels.levels_file.exists()

    monkeypatch.setattr(ProviderRegistry, "get_provider", lambda self, role, provider_roles=None: MockProvider())
    sched = _make_scheduler(tmp_path)
    sched_run = sched._fire(_job())
    assert sched_run.status == "ok"
    assert sched_run.output == "memory levels: generated=0 errors=0 mock=4 pending=4"
    assert not levels.levels_file.exists()


# k)
def test_scheduler_dispatch_summarize_memory_levels(tmp_path, monkeypatch):
    roles = []

    ok_dir = tmp_path / "ok"
    ok_store = _seed(ok_dir, decisions=OLD_ONLY)
    before = _canonical_bytes(ok_store)

    def _fake_get_provider(self, role, provider_roles=None):
        roles.append(role)
        return _FakeProvider()

    monkeypatch.setattr(ProviderRegistry, "get_provider", _fake_get_provider)
    sched = _make_scheduler(ok_dir)
    job = _job({"max_notes": 2})
    run = sched._fire(job)
    assert roles == ["reviewer"]
    assert run.status == "ok", run.output
    assert run.output == "memory levels: generated=2 errors=0 mock=0 pending=2"
    assert (ok_dir / "project_memory_levels.json").exists()
    assert job.last_status == "ok"
    assert _canonical_bytes(ok_store) == before

    roles.clear()
    err_dir = tmp_path / "err"
    _seed(err_dir, decisions=OLD_ONLY)

    def _failing_get_provider(self, role, provider_roles=None):
        roles.append(role)
        return _FailingProvider()

    monkeypatch.setattr(ProviderRegistry, "get_provider", _failing_get_provider)
    err_sched = _make_scheduler(err_dir)
    err_run = err_sched._fire(_job())
    assert roles == ["reviewer"]
    assert err_run.status == "error"
    assert err_run.output.startswith("RuntimeError: memory levels: generated=0 errors=3 mock=0 pending=4")
    assert not (err_dir / "project_memory_levels.json").exists()

    roles.clear()
    bad_dir = tmp_path / "bad"
    _seed(bad_dir, decisions=OLD_ONLY)
    bad_sched = _make_scheduler(bad_dir)
    bad_run = bad_sched._fire(_job({"max_notes": 0}))
    assert bad_run.status == "error"
    assert "max_notes" in bad_run.output
    assert roles == []
    assert not (bad_dir / "project_memory_levels.json").exists()


# l)
def test_mcp_get_canonical_memory_params_plumbed(tmp_path, monkeypatch):
    tools = asyncio.run(mcp_server.mcp.list_tools())
    schema = next(t for t in tools if t.name == "get_canonical_memory").inputSchema
    assert schema["properties"]["level"]["default"] == "full"
    assert schema["properties"]["since"]["default"] == ""

    store = _seed(tmp_path)
    levels = MemoryLevelStore(memory_dir=tmp_path)
    levels.save(_records_c(store))
    monkeypatch.setattr(mcp_server, "_memory_store", store)
    monkeypatch.setattr(mcp_server, "_level_store", MemoryLevelStore(memory_dir=tmp_path))

    auto = mcp_server.get_canonical_memory(level="auto")
    assert set(auto) == {"sections", "updated_at"}
    assert f"[{OLD} · s1] (L2)\nL2-old" in auto["sections"]["decisions"]
    assert auto["sections"]["decisions"].startswith("Preamble line kept forever.\n\n")
    assert auto["sections"]["vision"] == "no headers here"

    since = mcp_server.get_canonical_memory(since="2030-01-01")
    assert set(since) == {"sections", "updated_at"}
    assert since["sections"]["decisions"] == "Preamble line kept forever."

    assert mcp_server.get_canonical_memory(level="l9") == {
        "error": "invalid level: 'l9' (expected one of ('full', 'auto'))"
    }
    assert json.loads(store.memory_file.read_text())["decisions"] == DECISIONS


# m)
def test_level_store_round_trip_and_corrupt_file(tmp_path):
    levels = MemoryLevelStore(memory_dir=tmp_path)
    levels.save({"k": {"l1": {"text": "t"}}})
    assert levels.load() == {"k": {"l1": {"text": "t"}}}
    raw = json.loads(levels.levels_file.read_text(encoding="utf-8"))
    assert set(raw) == {"version", "notes"}
    assert raw["version"] == 1
    assert raw["notes"] == {"k": {"l1": {"text": "t"}}}

    levels.levels_file.write_text("{not json", encoding="utf-8")
    assert levels.load() == {}
    levels.levels_file.write_text('{"version": 1, "notes": []}', encoding="utf-8")
    assert levels.load() == {}
    assert MemoryLevelStore.level_text({"k": {"l1": {"text": ""}}}, "k", "l1") is None
    assert MemoryLevelStore.level_text({"k": {"l1": {"text": "x"}}}, "k", "l1") == "x"
    assert MemoryLevelStore.level_text({}, "k", "l1") is None
