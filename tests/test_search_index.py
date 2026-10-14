"""Tests for backend/storage/search_index.py."""

import ast
import importlib
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

MODULE_PATH = ROOT / "backend" / "storage" / "search_index.py"
MODULE_NAME = "backend.storage.search_index"

FIXED_TS = "2026-01-01T00:00:00+00:00"


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


@pytest.fixture
def index(tmp_path):
    from backend.storage.search_index import SearchIndex

    idx = SearchIndex(tmp_path / "s.sqlite")
    try:
        yield idx
    finally:
        idx._conn.close()


def test_search_index_module_exists():
    assert MODULE_PATH.is_file(), f"missing {MODULE_PATH}"


def test_search_index_has_no_duplicate_top_level_definitions():
    dupes = _duplicate_defs(_parse(MODULE_PATH))
    assert not dupes, f"top-level names defined more than once: {dupes}"


def test_search_index_definitions_do_not_shadow_imports():
    clashes = _defs_shadowing_imports(_parse(MODULE_PATH))
    assert not clashes, f"definitions shadow imported names: {clashes}"


def test_search_index_module_imports_cleanly():
    importlib.import_module(MODULE_NAME)


def test_query_on_empty_index_returns_no_matches(index):
    assert index.search("anything") == []
    assert index.count() == 0


def test_upserted_document_is_found_by_search(index):
    index.upsert("prompt_template", "p1", "Deploy checklist", "rotate the widgets nightly", FIXED_TS)
    index.upsert("idea", "i1", "Unrelated", "something else entirely", FIXED_TS)
    assert index.count() == 2

    results = index.search("widgets")
    assert len(results) == 1
    hit = results[0]
    assert hit["kind"] == "prompt_template"
    assert hit["doc_id"] == "p1"
    assert hit["title"] == "Deploy checklist"
    assert hit["updated_at"] == FIXED_TS
    assert "<mark>widgets</mark>" in hit["excerpt"]


def test_upsert_same_doc_replaces_previous_row(index):
    index.upsert("idea", "i1", "First", "alpha body", FIXED_TS)
    index.upsert("idea", "i1", "Second", "beta body", FIXED_TS)
    assert index.count() == 1
    assert index.search("alpha") == []
    assert [r["title"] for r in index.search("beta")] == ["Second"]


def test_search_kinds_filter(index):
    index.upsert("idea", "i1", "Idea", "shared term", FIXED_TS)
    index.upsert("workflow", "w1", "Workflow", "shared term", FIXED_TS)
    results = index.search("shared", kinds=["workflow"])
    assert [(r["kind"], r["doc_id"]) for r in results] == [("workflow", "w1")]


def test_upsert_unknown_kind_is_ignored(index):
    index.upsert("not_a_kind", "x1", "Title", "zebra", FIXED_TS)
    assert index.count() == 0
    assert index.search("zebra") == []
