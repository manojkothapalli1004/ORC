"""SQLite FTS5 search index over orchestrator JSON state.

The JSON files under ``data/`` remain the source of truth. This index is a
derived view that the stores upsert into on every save, and that we rebuild
from disk on startup if the SQLite file is missing or empty.

Schema is intentionally tiny:

    documents(kind, doc_id, title, body, updated_at UNINDEXED)

``kind`` is one of: session, idea, prompt_template, workflow, memory_section.
``doc_id`` uniquely identifies the document inside its kind. Re-indexing the
same (kind, doc_id) replaces the prior row.
"""

from __future__ import annotations

import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

from backend.config import settings


KINDS = {"session", "idea", "prompt_template", "workflow", "memory_section"}


def _quote_match(query: str) -> str:
    """Make a user query safe for FTS5 MATCH.

    FTS5 treats characters like ``.``, ``-``, ``"``, and operators specially.
    We wrap each whitespace-separated token in double quotes (escaped) so the
    query is a conjunction of phrase matches. A trailing ``*`` on the last
    token enables prefix search for the term-being-typed feel.
    """
    tokens = [tok for tok in query.split() if tok.strip()]
    if not tokens:
        return ""
    quoted: list[str] = []
    for i, tok in enumerate(tokens):
        clean = tok.replace('"', '""')
        if i == len(tokens) - 1 and clean and clean[-1].isalnum():
            quoted.append(f'"{clean}"*')
        else:
            quoted.append(f'"{clean}"')
    return " ".join(quoted)


class SearchIndex:
    def __init__(self, db_path: Path | None = None) -> None:
        self._path = db_path or (settings.state_dir / "search.sqlite")
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(str(self._path), check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._init_schema()

    def _init_schema(self) -> None:
        with self._lock:
            self._conn.execute(
                """
                CREATE VIRTUAL TABLE IF NOT EXISTS documents
                USING fts5(
                    kind UNINDEXED,
                    doc_id UNINDEXED,
                    title,
                    body,
                    updated_at UNINDEXED,
                    tokenize='unicode61 remove_diacritics 2'
                )
                """
            )
            self._conn.commit()

    def upsert(
        self,
        kind: str,
        doc_id: str,
        title: str,
        body: str,
        updated_at: datetime | str | None = None,
    ) -> None:
        if kind not in KINDS:
            return
        if not doc_id:
            return
        ts = _to_iso(updated_at)
        with self._lock:
            self._conn.execute(
                "DELETE FROM documents WHERE kind = ? AND doc_id = ?",
                (kind, doc_id),
            )
            self._conn.execute(
                "INSERT INTO documents(kind, doc_id, title, body, updated_at) VALUES (?, ?, ?, ?, ?)",
                (kind, doc_id, title or "", body or "", ts),
            )
            self._conn.commit()

    def delete(self, kind: str, doc_id: str) -> None:
        with self._lock:
            self._conn.execute(
                "DELETE FROM documents WHERE kind = ? AND doc_id = ?",
                (kind, doc_id),
            )
            self._conn.commit()

    def clear(self) -> None:
        with self._lock:
            self._conn.execute("DELETE FROM documents")
            self._conn.commit()

    def count(self) -> int:
        with self._lock:
            cur = self._conn.execute("SELECT COUNT(*) FROM documents")
            return int(cur.fetchone()[0])

    def search(
        self,
        query: str,
        *,
        kinds: Iterable[str] | None = None,
        limit: int = 20,
    ) -> list[dict]:
        match = _quote_match(query)
        if not match:
            return []
        params: list = [match]
        sql = (
            "SELECT kind, doc_id, title, snippet(documents, 3, '<mark>', '</mark>', '…', 12) AS excerpt, "
            "updated_at, bm25(documents) AS score "
            "FROM documents WHERE documents MATCH ?"
        )
        if kinds:
            kind_list = [k for k in kinds if k in KINDS]
            if kind_list:
                placeholders = ",".join(["?"] * len(kind_list))
                sql += f" AND kind IN ({placeholders})"
                params.extend(kind_list)
        sql += " ORDER BY score LIMIT ?"
        params.append(int(limit))
        with self._lock:
            cur = self._conn.execute(sql, params)
            rows = cur.fetchall()
        return [
            {
                "kind": row[0],
                "doc_id": row[1],
                "title": row[2],
                "excerpt": row[3],
                "updated_at": row[4],
                "score": row[5],
            }
            for row in rows
        ]


def _to_iso(value: datetime | str | None) -> str:
    if value is None:
        return datetime.now(timezone.utc).isoformat()
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.isoformat()
    return str(value)


_singleton: SearchIndex | None = None


def get_search_index() -> SearchIndex:
    global _singleton
    if _singleton is None:
        _singleton = SearchIndex()
    return _singleton


def rebuild_from_disk() -> dict[str, int]:
    """Walk the JSON state on disk and reindex every record.

    Idempotent: clears the FTS table first, then re-upserts every known
    document. Safe to call on startup; no-op for kinds whose stores are
    empty. Returns per-kind counts for visibility.
    """
    # Local imports avoid circular import at module load time.
    from backend.storage.ideas import IdeaStore, _index_idea
    from backend.storage.prompts import PromptTemplateStore, _index_template
    from backend.storage.sessions import SessionStore, _index_session
    from backend.storage.store import StateStore, _index_workflow

    index = get_search_index()
    index.clear()
    counts = {"session": 0, "idea": 0, "prompt_template": 0, "workflow": 0}

    for session in SessionStore().list_sessions():
        _index_session(session)
        counts["session"] += 1

    for thread in IdeaStore().list():
        _index_idea(thread)
        counts["idea"] += 1

    for template in PromptTemplateStore().list():
        _index_template(template)
        counts["prompt_template"] += 1

    state_store = StateStore()
    for wf_id in state_store.list_ids():
        wf = state_store.load(wf_id)
        if wf is not None:
            _index_workflow(wf)
            counts["workflow"] += 1

    return counts
