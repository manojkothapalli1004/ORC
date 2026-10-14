"""Regression tests for PromptTemplateStore (backend/storage/prompts.py).

Every store is rooted in tmp_path so the real data dir is never touched.
"""

from __future__ import annotations

from pathlib import Path

import pytest

import backend.storage.prompts as prompts_mod
from backend.models.prompt import PromptRole, PromptTemplate, PromptTemplateCategory
from backend.storage.prompts import PromptTemplateStore


class _NullIndex:
    def upsert(self, *args, **kwargs):
        return None

    def delete(self, *args, **kwargs):
        return None


@pytest.fixture(autouse=True)
def _no_search_index(monkeypatch):
    monkeypatch.setattr(prompts_mod, "get_search_index", lambda: _NullIndex(), raising=False)


def _key(t: PromptTemplate) -> tuple:
    return (
        t.id,
        t.name,
        PromptTemplateCategory(t.category),
        PromptRole(t.role) if t.role is not None else None,
        t.body,
        list(t.variables),
        t.variant,
        t.composition_order,
        list(t.tags),
    )


def _make_template() -> PromptTemplate:
    return PromptTemplate(
        id="c1b_regression_template",
        name="C1b regression template",
        category=PromptTemplateCategory.ASSISTANT_SAVED,
        role=PromptRole.VERIFIER,
        body="Verify {thing} carefully.",
        variables=["thing"],
        tags=["c1b", "regression"],
    )


def test_seeds_defaults_on_init(tmp_path: Path):
    store = PromptTemplateStore(root_dir=tmp_path)
    templates = store.list()
    assert len(templates) > 0
    assert all(isinstance(t, PromptTemplate) for t in templates)
    ids = [t.id for t in templates]
    assert len(ids) == len(set(ids))
    # Seeding is idempotent across instances on the same dir.
    again = PromptTemplateStore(root_dir=tmp_path).list()
    assert sorted(t.id for t in again) == sorted(ids)


def test_save_then_get_equal_and_list_grows_by_one(tmp_path: Path):
    store = PromptTemplateStore(root_dir=tmp_path)
    before_ids = {t.id for t in store.list()}
    template = _make_template()
    assert template.id not in before_ids

    store.save(template)

    got = store.get(template.id)
    assert got is not None
    assert _key(got) == _key(template)

    after = store.list()
    after_ids = {t.id for t in after}
    assert len(after) == len(before_ids) + 1
    assert after_ids == before_ids | {template.id}

    # Persisted: a fresh store instance on the same dir returns it too.
    fresh = PromptTemplateStore(root_dir=tmp_path)
    reloaded = fresh.get(template.id)
    assert reloaded is not None
    assert _key(reloaded) == _key(template)
    if hasattr(fresh, "load"):
        loaded = fresh.load(template.id)
        assert loaded is not None
        assert _key(loaded) == _key(template)


def test_get_missing_is_none(tmp_path: Path):
    store = PromptTemplateStore(root_dir=tmp_path)
    assert store.get("missing") is None


def test_by_category_filters_exactly(tmp_path: Path):
    store = PromptTemplateStore(root_dir=tmp_path)
    template = _make_template()
    store.save(template)
    all_templates = store.list()

    for category in PromptTemplateCategory:
        result = store.by_category(category)
        assert all(PromptTemplateCategory(t.category) == category for t in result)
        expected = {t.id for t in all_templates if PromptTemplateCategory(t.category) == category}
        assert {t.id for t in result} == expected

    saved_ids = {t.id for t in store.by_category(PromptTemplateCategory.ASSISTANT_SAVED)}
    assert template.id in saved_ids
    other = [c for c in PromptTemplateCategory if c != PromptTemplateCategory.ASSISTANT_SAVED]
    for category in other:
        assert template.id not in {t.id for t in store.by_category(category)}


def test_by_role_filters_exactly(tmp_path: Path):
    store = PromptTemplateStore(root_dir=tmp_path)
    template = _make_template()
    store.save(template)
    all_templates = store.list()

    for role in PromptRole:
        result = store.by_role(role)
        assert all(t.role is not None and PromptRole(t.role) == role for t in result)
        expected = {
            t.id for t in all_templates if t.role is not None and PromptRole(t.role) == role
        }
        assert {t.id for t in result} == expected

    assert template.id in {t.id for t in store.by_role(PromptRole.VERIFIER)}
    for role in PromptRole:
        if role != PromptRole.VERIFIER:
            assert template.id not in {t.id for t in store.by_role(role)}
