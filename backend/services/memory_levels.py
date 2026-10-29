"""Generator for derived L1/L2 summaries of canonical memory notes.

Reads canonical memory through MemoryStore, plans which (note, level) pairs
are missing, asks the given provider for each one and persists the result in
the sibling MemoryLevelStore after every success. This module never writes
the canonical memory file and imports neither the scheduler nor FastAPI.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

from backend.models.memory import MEMORY_SECTION_NAMES, ProjectMemory
from backend.providers.base import ProviderRequest
from backend.storage.memory import MemoryLevelStore, MemoryNote, MemoryStore, split_section_notes

logger = logging.getLogger("orchestrator.memory_levels")

# ProviderRequest.role label used for logs; the provider instance itself is
# whatever the caller passes (the scheduler passes the reviewer role's provider).
MEMORY_SUMMARIZER_ROLE = "memory_summarizer"

L1_SYSTEM_PROMPT = (
    "You condense one dated project-memory note into 3 to 5 key-point bullets. Keep every "
    "concrete identifier (file names, IDs, numbers, dates, decisions). Output only the bullets, one per line, "
    "each starting with '- '. No preamble, no heading."
)
L2_SYSTEM_PROMPT = (
    "You condense one dated project-memory note into exactly one sentence of at most 25 words "
    "that keeps the single most important fact or decision. Output only that sentence."
)


def build_prompt(note: MemoryNote) -> str:
    return (
        f"Section: {note.section}\nNote dated {note.stamp} (source: "
        f"{note.source})\n\n{note.body}"
    )


@dataclass
class MemoryLevelRun:
    generated: int = 0
    errors: int = 0
    mock: int = 0
    pending: int = 0
    details: list[str] = field(default_factory=list)

    def summary(self) -> str:
        return (
            f"memory levels: "
            f"generated={self.generated} errors={self.errors} mock={self.mock} pending={self.pending}"
        )


def plan_memory_levels(
    memory: ProjectMemory,
    records: dict[str, dict[str, Any]],
    *,
    now: datetime,
    l1_after_days: int = 7,
    l2_after_days: int = 30,
) -> list[tuple[MemoryNote, str]]:
    """Return the missing (note, level) pairs in deterministic order.

    Section order, then note order, l1 before l2. A note whose text changed
    has a new key and is planned again; stale records are left in place.
    """
    plan: list[tuple[MemoryNote, str]] = []
    for name in MEMORY_SECTION_NAMES:
        body = getattr(memory, name, "") or ""
        _, notes = split_section_notes(name, body)
        for note in notes:
            if not note.body.strip():
                continue
            age = now - note.when()
            key = note.key()
            if age >= timedelta(days=l1_after_days) and MemoryLevelStore.level_text(records, key, "l1") is None:
                plan.append((note, "l1"))
            if age >= timedelta(days=l2_after_days) and MemoryLevelStore.level_text(records, key, "l2") is None:
                plan.append((note, "l2"))
    return plan


async def generate_memory_levels(
    memory_store: MemoryStore,
    level_store: MemoryLevelStore,
    provider: Any,
    *,
    now: datetime | None = None,
    max_notes: int = 50,
    l1_after_days: int = 7,
    l2_after_days: int = 30,
    max_consecutive_errors: int = 3,
) -> MemoryLevelRun:
    """Generate missing L1/L2 texts, one provider call per (note, level).

    The levels file is saved atomically after every success, so a crash
    mid-run keeps completed items. A mock provider response stops the run
    without writing anything. The canonical memory file is never written.
    """
    now = now or datetime.now(timezone.utc)
    memory = memory_store.load()
    records = level_store.load()
    plan = plan_memory_levels(
        memory, records, now=now, l1_after_days=l1_after_days, l2_after_days=l2_after_days
    )
    items = plan[:max_notes]
    run = MemoryLevelRun()
    consecutive = 0

    for index, (note, level) in enumerate(items):
        key = note.key()
        request = ProviderRequest(
            role=MEMORY_SUMMARIZER_ROLE,
            prompt=build_prompt(note),
            system_prompt=L1_SYSTEM_PROMPT if level == "l1" else L2_SYSTEM_PROMPT,
            max_tokens=400 if level == "l1" else 120,
            metadata={"section": note.section, "stamp": note.stamp, "level": level, "key": key},
        )
        try:
            response = await provider.complete(request)
        except Exception as exc:
            run.errors += 1
            consecutive += 1
            run.details.append(f"{key} {level}: {type(exc).__name__}: {exc}")
            logger.warning("memory level %s for %s failed: %s", level, key, exc)
            if consecutive >= max_consecutive_errors:
                break
            continue

        if getattr(response, "is_mock", False):
            run.mock += len(items) - index
            run.details.append("provider is mock; nothing written")
            break

        text = (getattr(response, "content", "") or "").strip()
        if level == "l2":
            lines = [line.strip() for line in text.splitlines() if line.strip()]
            text = lines[0] if lines else ""
        if not text:
            run.errors += 1
            run.details.append(f"{key} {level}: empty provider output")
            logger.warning("memory level %s for %s: empty provider output", level, key)
            continue

        entry = records.setdefault(
            key, {"section": note.section, "stamp": note.stamp, "source": note.source}
        )
        entry[level] = {
            "text": text,
            "generated_at": now.isoformat(),
            "provider": getattr(response, "provider_name", ""),
            "model": getattr(response, "model_used", ""),
        }
        level_store.save(records)
        run.generated += 1
        consecutive = 0

    run.pending = len(plan) - run.generated
    return run
