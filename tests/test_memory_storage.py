"""Regression tests for MemoryStore (backend/storage/memory.py).

Every store is rooted in tmp_path so the real data dir is never touched.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import backend.storage.memory as memory_mod
from backend.models.memory import MEMORY_SECTION_NAMES, MemoryUpdatePatch, ProjectMemory
from backend.storage.memory import MemoryStore


class _NullIndex:
    def upsert(self, *args, **kwargs):
        return None

    def delete(self, *args, **kwargs):
        return None


@pytest.fixture(autouse=True)
def _no_search_index(monkeypatch):
    monkeypatch.setattr(memory_mod, "get_search_index", lambda: _NullIndex(), raising=False)


SECTIONS = sorted(MEMORY_SECTION_NAMES)
INVALID_SECTION = "definitely_not_a_memory_section"


def _sections(mem: ProjectMemory) -> dict:
    return {name: getattr(mem, name) for name in SECTIONS}


def test_section_names_are_real_fields():
    assert len(SECTIONS) >= 1
    assert INVALID_SECTION not in MEMORY_SECTION_NAMES
    mem = ProjectMemory()
    for name in SECTIONS:
        assert hasattr(mem, name)


def test_init_creates_dir_and_uses_tmp_path(tmp_path: Path):
    target = tmp_path / "nested" / "state"
    store = MemoryStore(memory_dir=target)
    assert target.is_dir()
    assert store.memory_file == target / "project_memory.json"
    assert not store.memory_file.exists()


def test_load_missing_returns_default(tmp_path: Path):
    store = MemoryStore(memory_dir=tmp_path)
    assert _sections(store.load()) == _sections(ProjectMemory())


def test_load_corrupt_file_returns_default(tmp_path: Path):
    store = MemoryStore(memory_dir=tmp_path)
    store.memory_file.write_text("{not json")
    assert _sections(store.load()) == _sections(ProjectMemory())


def test_save_load_round_trip(tmp_path: Path):
    store = MemoryStore(memory_dir=tmp_path)
    mem = ProjectMemory()
    for i, name in enumerate(SECTIONS):
        setattr(mem, name, f"body for {name} #{i}")
    store.save(mem)

    assert store.memory_file.exists()
    assert not store.memory_file.with_suffix(".tmp").exists()
    on_disk = json.loads(store.memory_file.read_text())
    for name in SECTIONS:
        assert on_disk[name] == getattr(mem, name)

    loaded = store.load()
    assert loaded.model_dump(mode="json") == mem.model_dump(mode="json")
    # A fresh store instance on the same dir sees the same data.
    again = MemoryStore(memory_dir=tmp_path).load()
    assert again.model_dump(mode="json") == mem.model_dump(mode="json")


def test_apply_patches_valid_and_invalid(tmp_path: Path):
    store = MemoryStore(memory_dir=tmp_path)
    seeded = ProjectMemory()
    for name in SECTIONS:
        setattr(seeded, name, f"original {name}")
    store.save(seeded)
    before = _sections(store.load())

    target = SECTIONS[0]
    patches = [
        MemoryUpdatePatch(section=target, note="new fact", source="unit_test", replace=True),
        MemoryUpdatePatch(section=INVALID_SECTION, note="ignored"),
    ]
    memory, applied, skipped = store.apply_patches(patches)

    assert applied == [target]
    assert skipped == [INVALID_SECTION]

    body = getattr(memory, target)
    assert body.endswith("new fact")
    assert "unit_test]" in body
    assert body.startswith("[")
    assert "original" not in body  # replace=True

    # NEGATIVE: every other section is untouched.
    for name in SECTIONS[1:]:
        assert getattr(memory, name) == before[name]

    # Changes are persisted.
    reloaded = store.load()
    assert _sections(reloaded) == _sections(memory)
    for name in SECTIONS[1:]:
        assert getattr(reloaded, name) == before[name]


def test_apply_patches_appends_without_replace(tmp_path: Path):
    store = MemoryStore(memory_dir=tmp_path)
    target = SECTIONS[-1]
    seeded = ProjectMemory()
    setattr(seeded, target, "existing body")
    store.save(seeded)

    memory, applied, skipped = store.apply_patches(
        [MemoryUpdatePatch(section=target, note="appended note", source="unit_test", replace=False)]
    )
    assert applied == [target]
    assert skipped == []
    body = getattr(memory, target)
    assert body.startswith("existing body\n\n[")
    assert body.endswith("appended note")


def test_apply_patches_empty_note_and_case_normalisation(tmp_path: Path):
    store = MemoryStore(memory_dir=tmp_path)
    target = SECTIONS[0]
    patches = [
        MemoryUpdatePatch(section=target, note="   "),
        MemoryUpdatePatch(section=f"  {target.upper()}  ", note="normalised"),
    ]
    memory, applied, skipped = store.apply_patches(patches)
    assert skipped == [target]
    assert applied == [target]
    assert getattr(memory, target).endswith("normalised")
    assert "assistant_distill]" in getattr(memory, target)


def test_apply_patches_all_invalid_leaves_sections_unchanged(tmp_path: Path):
    store = MemoryStore(memory_dir=tmp_path)
    seeded = ProjectMemory()
    for name in SECTIONS:
        setattr(seeded, name, f"keep {name}")
    store.save(seeded)
    before = _sections(store.load())

    memory, applied, skipped = store.apply_patches(
        [MemoryUpdatePatch(section=INVALID_SECTION, note="x")]
    )
    assert applied == []
    assert skipped == [INVALID_SECTION]
    assert _sections(memory) == before
    assert _sections(store.load()) == before
