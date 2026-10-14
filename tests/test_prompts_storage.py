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
    monkeypatch.setattr(prompts_mod, "get_search_index", lambda: _NullIndex())


DEFAULT_IDS = {
    "startup-default",
    "role-builder",
    "role-reviewer",
    "role-verifier",
    "role-planner",
    "parallel-default",
    "safety-default",
    "return-format-default",
}


def _sort_key(t: PromptTemplate) -> tuple:
    return (t.composition_order, t.name, t.id)


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


def _make_variant_template() -> PromptTemplate:
    return PromptTemplate(
        id="c1c_variant_template",
        name="C1c variant template",
        category=PromptTemplateCategory.ROLE,
        role=PromptRole.BUILDER,
        body="Variant body.",
        variant="experimental",
        composition_order=5,
    )


def test_seeds_exact_defaults_on_init(tmp_path: Path):
    store = PromptTemplateStore(root_dir=tmp_path)
    templates = store.list()
    assert all(isinstance(t, PromptTemplate) for t in templates)
    ids = [t.id for t in templates]
    assert len(ids) == len(set(ids))
    assert set(ids) == DEFAULT_IDS
    # Seeding is idempotent across instances on the same dir.
    again = PromptTemplateStore(root_dir=tmp_path).list()
    assert {t.id for t in again} == DEFAULT_IDS


def test_reseed_keeps_user_name_and_tags_but_resets_body(tmp_path: Path):
    store = PromptTemplateStore(root_dir=tmp_path)
    original = store.get("startup-default")
    assert original is not None
    default_body = original.body
    default_order = original.composition_order

    edited = original.model_copy(
        update={
            "name": "My edited startup",
            "tags": ["mine", "edited"],
            "body": "user-edited body",
            "composition_order": 999,
        }
    )
    store.save(edited)
    assert store.get("startup-default").body == "user-edited body"

    reseeded = PromptTemplateStore(root_dir=tmp_path).get("startup-default")
    assert reseeded is not None
    # _seed_defaults preserves name and tags...
    assert reseeded.name == "My edited startup"
    assert reseeded.tags == ["mine", "edited"]
    # ...but forces body / composition_order back to the shipped default.
    assert reseeded.body == default_body
    assert reseeded.composition_order == default_order
    assert PromptTemplateCategory(reseeded.category) == PromptTemplateCategory.STARTUP


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
    loaded = fresh.load(template.id)
    assert loaded is not None
    assert _key(loaded) == _key(template)


def test_get_missing_is_none(tmp_path: Path):
    store = PromptTemplateStore(root_dir=tmp_path)
    assert store.get("missing") is None
    assert store.load("missing") is None


def test_by_category_filters_exactly_and_sorted(tmp_path: Path):
    store = PromptTemplateStore(root_dir=tmp_path)
    template = _make_template()
    store.save(template)
    all_templates = store.list()

    for category in PromptTemplateCategory:
        result = store.by_category(category)
        assert all(PromptTemplateCategory(t.category) == category for t in result)
        expected = {t.id for t in all_templates if PromptTemplateCategory(t.category) == category}
        assert {t.id for t in result} == expected
        assert [t.id for t in result] == [t.id for t in sorted(result, key=_sort_key)]

    assert [t.id for t in store.by_category(PromptTemplateCategory.ROLE)] == [
        "role-builder",
        "role-planner",
        "role-reviewer",
        "role-verifier",
    ]

    saved_ids = {t.id for t in store.by_category(PromptTemplateCategory.ASSISTANT_SAVED)}
    assert template.id in saved_ids
    other = [c for c in PromptTemplateCategory if c != PromptTemplateCategory.ASSISTANT_SAVED]
    for category in other:
        assert template.id not in {t.id for t in store.by_category(category)}


def test_by_role_filters_exactly_and_sorted(tmp_path: Path):
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
        assert [t.id for t in result] == [t.id for t in sorted(result, key=_sort_key)]

    # role-verifier (order 20) sorts before the saved template (order 100).
    assert [t.id for t in store.by_role(PromptRole.VERIFIER)] == ["role-verifier", template.id]
    for role in PromptRole:
        if role != PromptRole.VERIFIER:
            assert template.id not in {t.id for t in store.by_role(role)}


def test_filter_no_args_returns_all_sorted(tmp_path: Path):
    store = PromptTemplateStore(root_dir=tmp_path)
    result = store.filter()
    assert {t.id for t in result} == DEFAULT_IDS
    assert [t.id for t in result] == [
        "startup-default",
        "role-builder",
        "role-planner",
        "role-reviewer",
        "role-verifier",
        "parallel-default",
        "safety-default",
        "return-format-default",
    ]


def test_filter_by_category(tmp_path: Path):
    store = PromptTemplateStore(root_dir=tmp_path)
    store.save(_make_template())
    store.save(_make_variant_template())

    assert [t.id for t in store.filter(category=PromptTemplateCategory.ROLE)] == [
        "c1c_variant_template",
        "role-builder",
        "role-planner",
        "role-reviewer",
        "role-verifier",
    ]
    assert [t.id for t in store.filter(category=PromptTemplateCategory.STARTUP)] == ["startup-default"]
    assert [t.id for t in store.filter(category=PromptTemplateCategory.ASSISTANT_SAVED)] == [
        "c1b_regression_template"
    ]


def test_filter_by_role(tmp_path: Path):
    store = PromptTemplateStore(root_dir=tmp_path)
    store.save(_make_template())
    store.save(_make_variant_template())

    assert [t.id for t in store.filter(role=PromptRole.VERIFIER)] == [
        "role-verifier",
        "c1b_regression_template",
    ]
    assert [t.id for t in store.filter(role=PromptRole.BUILDER)] == [
        "c1c_variant_template",
        "role-builder",
    ]
    assert [t.id for t in store.filter(role=PromptRole.PLANNER)] == ["role-planner"]


def test_filter_by_variant(tmp_path: Path):
    store = PromptTemplateStore(root_dir=tmp_path)
    store.save(_make_variant_template())

    assert [t.id for t in store.filter(variant="experimental")] == ["c1c_variant_template"]
    default_variant = store.filter(variant="default")
    assert {t.id for t in default_variant} == DEFAULT_IDS
    assert [t.id for t in default_variant] == [t.id for t in sorted(default_variant, key=_sort_key)]
    assert store.filter(variant="nonexistent") == []


def test_filter_combined_category_and_role(tmp_path: Path):
    store = PromptTemplateStore(root_dir=tmp_path)
    store.save(_make_template())
    store.save(_make_variant_template())

    assert [
        t.id for t in store.filter(category=PromptTemplateCategory.ROLE, role=PromptRole.VERIFIER)
    ] == ["role-verifier"]
    assert [
        t.id
        for t in store.filter(category=PromptTemplateCategory.ASSISTANT_SAVED, role=PromptRole.VERIFIER)
    ] == ["c1b_regression_template"]
    assert [
        t.id for t in store.filter(category=PromptTemplateCategory.ROLE, role=PromptRole.BUILDER)
    ] == ["c1c_variant_template", "role-builder"]
    assert [
        t.id
        for t in store.filter(
            category=PromptTemplateCategory.ROLE, role=PromptRole.BUILDER, variant="default"
        )
    ] == ["role-builder"]
    assert store.filter(category=PromptTemplateCategory.SAFETY, role=PromptRole.BUILDER) == []
