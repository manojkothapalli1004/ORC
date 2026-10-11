"""Regression tests for StateStore.list_ids filtering.

list_ids used to glob every *.json file in the state dir, which also matched
co-located non-workflow files (project_memory.json, assistant_threads.json,
schedules, and so on). Those files produced invalid_state_count noise and ghost
workflows with empty ids. These tests protect the 12-char hex filter.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

import backend.storage.store as store_mod
from backend.storage.store import StateStore


class _NullIndex:
    def upsert(self, *args, **kwargs):
        return None

    def delete(self, *args, **kwargs):
        return None


@pytest.fixture(autouse=True)
def _no_search_index(monkeypatch):
    # Keep tests hermetic: don't touch the real search index.
    monkeypatch.setattr(store_mod, "get_search_index", lambda: _NullIndex())


_HEX12 = re.compile(r"^[0-9a-f]{12}$")


def _drop_noise(state_dir: Path) -> None:
    (state_dir / "project_memory.json").write_text(json.dumps({"facts": ["x"]}))
    (state_dir / "assistant_threads.json").write_text(json.dumps({"threads": []}))
    (state_dir / "schedules.json").write_text(json.dumps([{"cron": "* * * * *"}]))
    (state_dir / "search_index.json").write_text(json.dumps({}))
    # 12 chars but not hex: must be rejected.
    (state_dir / "projectmemor.json").write_text(json.dumps({}))
    # Uppercase hex: create() only emits lowercase.
    (state_dir / "ABCDEF012345.json").write_text(json.dumps({}))
    # Leftover atomic-write temp file.
    (state_dir / "0123456789ab.tmp").write_text("{}")
    # Hex but wrong length.
    (state_dir / "abcdef.json").write_text(json.dumps({}))


def test_list_ids_returns_only_workflow_ids(tmp_path: Path) -> None:
    store = StateStore(state_dir=tmp_path)
    created = [store.create().id for _ in range(3)]
    _drop_noise(tmp_path)

    ids = store.list_ids()

    assert ids == sorted(created)
    assert all(_HEX12.match(i) for i in ids)
    for noise in ("project_memory", "assistant_threads", "schedules", "search_index",
                  "projectmemor", "ABCDEF012345", "abcdef"):
        assert noise not in ids


def test_no_invalid_states_or_ghost_workflows(tmp_path: Path) -> None:
    store = StateStore(state_dir=tmp_path)
    created = {store.create().id for _ in range(2)}
    _drop_noise(tmp_path)

    loaded = [store.load(i) for i in store.list_ids()]
    invalid_state_count = sum(1 for s in loaded if s is None)

    assert invalid_state_count == 0
    assert all(s is not None and s.id for s in loaded)
    assert {s.id for s in loaded} == created


def test_list_ids_empty_dir_with_only_noise(tmp_path: Path) -> None:
    store = StateStore(state_dir=tmp_path)
    _drop_noise(tmp_path)
    assert store.list_ids() == []


def test_deleted_workflow_disappears_from_list(tmp_path: Path) -> None:
    store = StateStore(state_dir=tmp_path)
    a = store.create().id
    b = store.create().id
    _drop_noise(tmp_path)

    assert store.delete(a) is True
    assert store.list_ids() == [b]
