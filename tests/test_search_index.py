"""Tests for backend/storage/search_index.py.

Structural checks always run. The behavioural no-match check redirects every
module-level storage path constant into tmp_path first and is skipped (never
run against real state) if no such constant can be found.
"""

import ast
import asyncio
import importlib
import inspect
import sys
from pathlib import Path

import pytest

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


def _redirect_storage_paths(module, monkeypatch, tmp_path):
    redirected = []
    for name, value in list(vars(module).items()):
        if not name.isupper() or not any(tag in name for tag in ("DIR", "PATH", "FILE", "ROOT", "DB")):
            continue
        if isinstance(value, Path):
            target = tmp_path / (value.name or name.lower())
        elif isinstance(value, str) and "://" not in value and (
            "/" in value or value.endswith((".json", ".jsonl", ".db", ".sqlite"))
        ):
            target = str(tmp_path / (Path(value).name or name.lower()))
        else:
            continue
        monkeypatch.setattr(module, name, target)
        redirected.append(name)
    return redirected


def _find_callable(module, candidates, required):
    for name in candidates:
        fn = getattr(module, name, None)
        if not callable(fn) or inspect.isclass(fn) or getattr(fn, "__module__", None) != module.__name__:
            continue
        try:
            sig = inspect.signature(fn)
        except (TypeError, ValueError):
            continue
        req = [
            p
            for p in sig.parameters.values()
            if p.default is p.empty and p.kind not in (p.VAR_POSITIONAL, p.VAR_KEYWORD)
        ]
        if len(req) == required:
            return fn
    return None


def _call(fn, *args):
    result = fn(*args)
    if inspect.isawaitable(result):
        result = asyncio.run(result)
    return result


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


def test_query_on_empty_index_returns_no_matches(monkeypatch, tmp_path):
    module = importlib.import_module(MODULE_NAME)
    if not _redirect_storage_paths(module, monkeypatch, tmp_path):
        pytest.skip("no module-level storage path constant to redirect; refusing to touch real state")
    query_fn = _find_callable(module, ("search", "query", "search_index", "query_index"), 1)
    if query_fn is None:
        pytest.skip("no single-argument search/query function found in search index")
    result = _call(query_fn, "zzqx-term-that-cannot-match-anything")
    assert result is not None
    assert len(result) == 0, f"expected no hits on an empty index, got {result!r}"
