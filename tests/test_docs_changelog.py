"""Accuracy checks for CHANGELOG, ROADMAP and the scheduler docstrings."""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

from backend.models.scheduler import ScheduledActionType

ROOT = Path(__file__).resolve().parent.parent
CHANGELOG = ROOT / "CHANGELOG.md"
ROADMAP = ROOT / "ROADMAP.md"
SERVICE_PY = ROOT / "backend" / "services" / "scheduler.py"
MODELS_PY = ROOT / "backend" / "models" / "scheduler.py"

ALL_FILES = [CHANGELOG, ROADMAP, SERVICE_PY, MODELS_PY]

_EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _norm(text: str) -> str:
    return " ".join(text.split()).lower()


def _unreleased() -> str:
    match = re.search(
        r"^## \[Unreleased\][^\n]*\n(.*?)(?=^## \[)",
        _read(CHANGELOG),
        re.MULTILINE | re.DOTALL,
    )
    assert match, "CHANGELOG.md must have an Unreleased section followed by a release"
    return match.group(1)


def _class_docstring(path: Path, name: str) -> str:
    tree = ast.parse(_read(path))
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == name:
            return ast.get_docstring(node) or ""
    raise AssertionError(f"class {name} not found in {path}")


def test_unreleased_section_is_not_empty() -> None:
    assert _unreleased().strip()


def test_every_action_type_in_unreleased() -> None:
    section = _unreleased()
    for action in ScheduledActionType:
        assert action.value in section, action.value


@pytest.mark.parametrize("keyword", ["shell", "network", "claude", "mock"])
def test_required_keywords_in_unreleased(keyword: str) -> None:
    assert keyword in _norm(_unreleased())


def test_unreleased_network_claims_are_specific() -> None:
    text = _norm(_unreleased())
    assert "no scheduled action runs a shell command" in text
    assert "router base url with no key" in text
    assert "claude_code_local" in text
    assert "cli process" in text
    assert "mock provider" in text


def test_unreleased_mentions_search_and_lan_auth() -> None:
    text = _norm(_unreleased())
    assert "fts5" in text
    assert "basic auth" in text
    assert "local_access_password" in text


def test_job_status_is_ok_or_error_only() -> None:
    section = _unreleased()
    assert "`ok`" in section
    assert "`error`" in section
    assert "skipped" not in section.lower()
    assert "skipped" not in _read(ROADMAP).lower()
    assert "skipped" not in _read(MODELS_PY).lower()


@pytest.mark.parametrize("path", ALL_FILES, ids=lambda p: p.name)
def test_no_stale_safety_phrases(path: Path) -> None:
    text = _norm(_read(path))
    assert "no external network" not in text
    assert "mock-safe" not in text


@pytest.mark.parametrize("path", ALL_FILES, ids=lambda p: p.name)
def test_no_emails_or_local_paths(path: Path) -> None:
    text = _read(path)
    assert not _EMAIL.search(text)
    for fragment in ("/Users/", "/home/", "/tmp/"):
        assert fragment not in text


def test_roadmap_mentions_new_features_and_network() -> None:
    text = _norm(_read(ROADMAP))
    assert "scheduled jobs" in text
    assert "fts5" in text
    assert "lan auth hardening" in text
    assert "run_workflow_step" in text
    assert "network" in text
    assert "router base url with no key" in text
    assert "mock provider" in text


def test_service_module_docstring_is_accurate() -> None:
    doc = ast.get_docstring(ast.parse(_read(SERVICE_PY))) or ""
    lowered = _norm(doc)
    assert "shell" in lowered
    assert "run_workflow_step" in doc
    assert "network" in lowered
    assert "claude_code_local" in doc
    assert "mock provider" in lowered
    assert "router base url" in lowered
    assert "skipped" not in lowered
    assert "``ok``" in doc and "``error``" in doc


def test_action_type_docstring_is_accurate() -> None:
    doc = _class_docstring(MODELS_PY, "ScheduledActionType")
    lowered = _norm(doc)
    assert "shell" in lowered
    assert "run_workflow_step" in doc
    assert "network" in lowered
    assert "claude_code_local" in doc
    assert "router base url" in lowered
    assert "mock provider" in lowered


def test_scheduler_only_assigns_ok_or_error_status() -> None:
    source = _read(SERVICE_PY)
    assigned = set(re.findall(r'\bstatus\s*=\s*"([a-z_]+)"', source))
    assert assigned == {"ok", "error"}
