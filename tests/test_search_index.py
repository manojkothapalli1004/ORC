"""Tests for backend/storage/search_index.py."""

import ast
import importlib
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

MODULE_PATH = ROOT / "backend" / "storage" / "search_index.py"
MODULE_NAME = "backend.storage.search_index"


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


def test_query_on_empty_index_returns_no_matches(tmp_path):
    from backend.storage.search_index import SearchIndex

    index = SearchIndex(tmp_path / "s.sqlite")
    assert list(index.search("anything")) == []
