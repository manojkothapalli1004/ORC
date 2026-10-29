from __future__ import annotations

import hashlib
import json
import re
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, List

from backend.config import settings
from backend.storage.atomic import atomic_write_text
from backend.models.memory import (
    ProjectMemory, AssistantThread, ThreadListItem,
    MemoryUpdatePatch, MEMORY_SECTION_NAMES,
)


class MemoryStore:
    """Store for canonical project memory in a JSON file."""

    def __init__(self, memory_dir: Path | None = None) -> None:
        self.memory_dir = memory_dir or settings.state_dir
        self.memory_file = self.memory_dir / "project_memory.json"
        self.memory_dir.mkdir(parents=True, exist_ok=True)

    def load(self) -> ProjectMemory:
        if not self.memory_file.exists():
            return ProjectMemory()
        try:
            raw = json.loads(self.memory_file.read_text())
            return ProjectMemory(**raw)
        except (ValueError, KeyError, TypeError):
            return ProjectMemory()

    def save(self, memory: ProjectMemory) -> None:
        tmp = self.memory_file.with_suffix(".tmp")
        tmp.write_text(memory.model_dump_json(indent=2))
        tmp.replace(self.memory_file)

    def apply_patches(self, patches: list[MemoryUpdatePatch]) -> tuple[ProjectMemory, list[str], list[str]]:
        """Apply distilled updates to canonical memory.

        Each valid patch either replaces the section body (`replace=True`) or
        appends a dated block under the existing body. Returns the updated
        memory along with the list of applied and skipped section names.
        Invalid section names are skipped rather than raising so a partial
        batch still persists cleanly.
        """
        memory = self.load()
        stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        applied: list[str] = []
        skipped: list[str] = []
        for patch in patches:
            name = patch.section.strip().lower()
            note = patch.note.strip()
            if name not in MEMORY_SECTION_NAMES or not note:
                skipped.append(patch.section)
                continue
            source = (patch.source or "assistant_distill").strip() or "assistant_distill"
            header = f"[{stamp} · {source}]"
            block = f"{header}\n{note}"
            current = (getattr(memory, name) or "").strip()
            if patch.replace or not current:
                new_body = block
            else:
                new_body = f"{current}\n\n{block}"
            setattr(memory, name, new_body)
            applied.append(name)
        memory.updated_at = datetime.now(timezone.utc)
        self.save(memory)
        return memory, applied, skipped


class ThreadStore:
    """Multi-thread assistant chat storage.

    Each thread is a separate JSON file in data/assistant_threads/.
    An ``active_thread`` pointer file tracks the current thread ID.
    Backward compatible: migrates the legacy single-file format on first use.
    """

    def __init__(self, memory_dir: Path | None = None) -> None:
        self.memory_dir = memory_dir or settings.state_dir
        self.thread_dir = self.memory_dir / "assistant_threads"
        self.active_file = self.thread_dir / "_active.json"
        self.legacy_file = self.memory_dir / "assistant_threads.json"
        self.thread_dir.mkdir(parents=True, exist_ok=True)
        self._migrate_legacy()

    # ── Migration ───────────────────────────────────────────────────────────

    def _migrate_legacy(self) -> None:
        """One-time migration from the single-file format."""
        if not self.legacy_file.exists():
            return
        # Skip if already migrated (threads directory has real thread files)
        existing = [f for f in self.thread_dir.glob("*.json") if f.name != "_active.json"]
        if existing:
            return
        try:
            raw = json.loads(self.legacy_file.read_text())
            messages = raw.get("messages", [])
            if not messages:
                return
            thread_id = self._new_id()
            thread = AssistantThread(
                thread_id=thread_id,
                title="Migrated conversation",
                messages=[],
                created_at=datetime.now(timezone.utc),
                updated_at=datetime.now(timezone.utc),
            )
            # Re-parse messages to populate with proper model instances
            from backend.models.memory import AssistantMessage
            for m in messages:
                thread.messages.append(AssistantMessage(**m))
            self._save_thread_file(thread)
            self._set_active(thread_id)
        except Exception:
            pass  # If migration fails, start fresh — legacy file stays untouched

    # ── ID helpers ──────────────────────────────────────────────────────────

    @staticmethod
    def _new_id() -> str:
        return uuid.uuid4().hex[:12]

    def _thread_path(self, thread_id: str) -> Path:
        return self.thread_dir / f"{thread_id}.json"

    # ── Active thread pointer ───────────────────────────────────────────────

    def _get_active_id(self) -> str:
        if self.active_file.exists():
            try:
                raw = json.loads(self.active_file.read_text())
                return raw.get("active_thread_id", "")
            except Exception:
                pass
        return ""

    def _set_active(self, thread_id: str) -> None:
        tmp = self.active_file.with_suffix(".tmp")
        tmp.write_text(json.dumps({"active_thread_id": thread_id}))
        tmp.replace(self.active_file)

    # ── Thread file I/O ─────────────────────────────────────────────────────

    def _save_thread_file(self, thread: AssistantThread) -> None:
        path = self._thread_path(thread.thread_id)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(thread.model_dump_json(indent=2))
        tmp.replace(path)

    def _load_thread_file(self, thread_id: str) -> AssistantThread | None:
        path = self._thread_path(thread_id)
        if not path.exists():
            return None
        try:
            raw = json.loads(path.read_text())
            return AssistantThread(**raw)
        except (ValueError, KeyError, TypeError):
            return None

    # ── Public API: backward-compatible load/save ───────────────────────────

    def load(self) -> AssistantThread:
        """Load the active thread. Creates one if none exists."""
        active_id = self._get_active_id()
        if active_id:
            thread = self._load_thread_file(active_id)
            if thread:
                return thread
        # No active thread — create a default one
        thread = self.create()
        return thread

    def save(self, thread: AssistantThread) -> None:
        """Save a thread (updates timestamp)."""
        thread.updated_at = datetime.now(timezone.utc)
        self._save_thread_file(thread)

    # ── Multi-thread operations ─────────────────────────────────────────────

    def create(self, title: str = "") -> AssistantThread:
        """Create a new thread and make it active."""
        thread_id = self._new_id()
        now = datetime.now(timezone.utc)
        thread = AssistantThread(
            thread_id=thread_id,
            title=title or f"Thread {now.strftime('%b %d %H:%M')}",
            messages=[],
            created_at=now,
            updated_at=now,
        )
        self._save_thread_file(thread)
        self._set_active(thread_id)
        return thread

    def list_threads(self, include_archived: bool = False) -> List[ThreadListItem]:
        """List all threads as compact summaries, newest first."""
        items: List[ThreadListItem] = []
        for path in self.thread_dir.glob("*.json"):
            if path.name.startswith("_"):
                continue
            try:
                raw = json.loads(path.read_text())
                thread = AssistantThread(**raw)
                if not include_archived and thread.archived:
                    continue
                # Build preview from first user message
                preview = ""
                for m in thread.messages:
                    if m.role == "user":
                        preview = m.content[:80]
                        break
                items.append(ThreadListItem(
                    thread_id=thread.thread_id,
                    title=thread.title,
                    message_count=len(thread.messages),
                    archived=thread.archived,
                    created_at=thread.created_at,
                    updated_at=thread.updated_at,
                    preview=preview,
                ))
            except Exception:
                continue
        items.sort(key=lambda t: t.updated_at, reverse=True)
        return items

    def get(self, thread_id: str) -> AssistantThread | None:
        """Load a specific thread by ID."""
        return self._load_thread_file(thread_id)

    def switch(self, thread_id: str) -> AssistantThread | None:
        """Switch active thread. Returns the thread or None if not found."""
        thread = self._load_thread_file(thread_id)
        if thread:
            self._set_active(thread_id)
        return thread

    def rename(self, thread_id: str, title: str) -> AssistantThread | None:
        """Rename a thread."""
        thread = self._load_thread_file(thread_id)
        if not thread:
            return None
        thread.title = title
        thread.updated_at = datetime.now(timezone.utc)
        self._save_thread_file(thread)
        return thread

    def archive(self, thread_id: str) -> AssistantThread | None:
        """Soft-archive a thread. If it was active, switch to another."""
        thread = self._load_thread_file(thread_id)
        if not thread:
            return None
        thread.archived = True
        thread.updated_at = datetime.now(timezone.utc)
        self._save_thread_file(thread)
        # If this was the active thread, switch to most recent non-archived
        if self._get_active_id() == thread_id:
            others = self.list_threads(include_archived=False)
            if others:
                self._set_active(others[0].thread_id)
            else:
                # Create a fresh thread
                self.create()
        return thread

    def delete(self, thread_id: str) -> bool:
        """Permanently delete a thread file."""
        path = self._thread_path(thread_id)
        if not path.exists():
            return False
        path.unlink()
        if self._get_active_id() == thread_id:
            others = self.list_threads(include_archived=False)
            if others:
                self._set_active(others[0].thread_id)
            else:
                self.create()
        return True

    def get_active_id(self) -> str:
        """Public accessor for the active thread ID."""
        return self._get_active_id()


# ---------------------------------------------------------------------------
# Derived memory levels (L1/L2) — read-side helpers and the sibling store.
# Canonical text is never written by anything below this line.
# ---------------------------------------------------------------------------

NOTE_STAMP_FORMAT = "%Y-%m-%dT%H:%M:%SZ"
NOTE_HEADER_RE = re.compile(
    r"^\[(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z) · ([^\]\n]*)\]$",
    re.MULTILINE,
)
MEMORY_LEVELS = ("full", "auto")
LEVELS_FILE_NAME = "project_memory_levels.json"


@dataclass(frozen=True)
class MemoryNote:
    """One dated block of a canonical memory section, as written by apply_patches."""

    section: str
    stamp: str
    source: str
    header: str
    body: str

    def key(self) -> str:
        digest = hashlib.sha256(self.body.encode("utf-8")).hexdigest()[:16]
        return f"{self.section}|{self.stamp}|{digest}"

    def when(self) -> datetime:
        return datetime.strptime(self.stamp, NOTE_STAMP_FORMAT).replace(tzinfo=timezone.utc)


def _stamp_ok(stamp: str) -> bool:
    try:
        datetime.strptime(stamp, NOTE_STAMP_FORMAT)
    except ValueError:
        return False
    return True


def split_section_notes(section: str, body: str) -> tuple[str, list[MemoryNote]]:
    """Split a section body into (undated preamble, dated notes). Pure; no I/O."""
    body = body or ""
    matches = [m for m in NOTE_HEADER_RE.finditer(body) if _stamp_ok(m.group(1))]
    if not matches:
        return body, []
    preamble = body[: matches[0].start()].rstrip("\n")
    notes: list[MemoryNote] = []
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(body)
        note_body = body[match.end():end].strip("\n")
        notes.append(
            MemoryNote(
                section=section,
                stamp=match.group(1),
                source=match.group(2),
                header=match.group(0),
                body=note_body,
            )
        )
    return preamble, notes


def render_section(preamble: str, blocks: list[str]) -> str:
    parts = ([preamble] if preamble.strip() else []) + list(blocks)
    return "\n\n".join(parts)


class MemoryLevelStore:
    """Derived L1/L2 texts stored beside the canonical memory file.

    Holds derived text only; canonical text never goes in this file.
    """

    def __init__(self, memory_dir: Path | None = None) -> None:
        self.memory_dir = memory_dir or settings.state_dir
        self.levels_file = self.memory_dir / LEVELS_FILE_NAME
        self.memory_dir.mkdir(parents=True, exist_ok=True)

    def load(self) -> dict[str, dict[str, Any]]:
        if not self.levels_file.exists():
            return {}
        try:
            raw = json.loads(self.levels_file.read_text(encoding="utf-8"))
            if isinstance(raw, dict) and isinstance(raw.get("notes"), dict):
                return raw["notes"]
            return {}
        except (ValueError, TypeError, KeyError, OSError):
            return {}

    def save(self, records: dict[str, dict[str, Any]]) -> None:
        atomic_write_text(
            self.levels_file,
            json.dumps({"version": 1, "notes": records}, indent=2, sort_keys=True, ensure_ascii=False),
        )

    @staticmethod
    def level_text(records: dict[str, dict[str, Any]], key: str, level: str) -> str | None:
        entry = records.get(key) if isinstance(records, dict) else None
        if not isinstance(entry, dict):
            return None
        derived = entry.get(level)
        if not isinstance(derived, dict):
            return None
        text = derived.get("text")
        if isinstance(text, str) and text:
            return text
        return None


def parse_since(since: str) -> datetime | None:
    since = (since or "").strip()
    if not since:
        return None
    try:
        value = datetime.fromisoformat(since)
    except ValueError as exc:
        raise ValueError(f"invalid since value: {since!r}") from exc
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value


def pick_level(
    age: timedelta,
    records: dict[str, dict[str, Any]],
    key: str,
    *,
    l1_after_days: int = 7,
    l2_after_days: int = 30,
) -> tuple[str, str | None]:
    if age >= timedelta(days=l2_after_days):
        text = MemoryLevelStore.level_text(records, key, "l2")
        if text:
            return "l2", text
        text = MemoryLevelStore.level_text(records, key, "l1")
        if text:
            return "l1", text
        return "l0", None
    if age >= timedelta(days=l1_after_days):
        text = MemoryLevelStore.level_text(records, key, "l1")
        if text:
            return "l1", text
        return "l0", None
    return "l0", None


def render_memory_view(
    memory: ProjectMemory,
    records: dict[str, dict[str, Any]],
    *,
    level: str = "full",
    since: str = "",
    now: datetime | None = None,
    l1_after_days: int = 7,
    l2_after_days: int = 30,
) -> dict[str, str]:
    """Render every canonical section at the requested level / since filter.

    The default path (level="full", no since) returns the stored text without
    parsing. Headers are emitted verbatim; " (L1)" / " (L2)" follows the
    closing bracket on the same line when a derived level is used.
    """
    if level not in MEMORY_LEVELS:
        raise ValueError(f"invalid level: {level!r} (expected one of {MEMORY_LEVELS})")
    since_dt = parse_since(since)
    if level == "full" and since_dt is None:
        return {name: getattr(memory, name, "") or "" for name in MEMORY_SECTION_NAMES}
    now = now or datetime.now(timezone.utc)
    out: dict[str, str] = {}
    for name in MEMORY_SECTION_NAMES:
        body = getattr(memory, name, "") or ""
        preamble, notes = split_section_notes(name, body)
        blocks: list[str] = []
        for note in notes:
            when = note.when()
            if since_dt is not None and when < since_dt:
                continue
            if level == "full":
                text, tag = note.body, ""
            else:
                chosen, derived = pick_level(
                    now - when,
                    records,
                    note.key(),
                    l1_after_days=l1_after_days,
                    l2_after_days=l2_after_days,
                )
                if chosen == "l0":
                    text, tag = note.body, ""
                else:
                    text, tag = derived, f" ({chosen.upper()})"
            blocks.append(f"{note.header}{tag}\n{text}" if text else f"{note.header}{tag}")
        out[name] = render_section(preamble, blocks)
    return out
