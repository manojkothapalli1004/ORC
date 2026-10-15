"""Persistence for scheduled-job definitions and per-fire run records.

Job definitions live in ``data/schedules.json``, a single file written
atomically. Per-fire run records live in ``data/scheduler_runs/<job_id>.json``
as a bounded ring buffer of the most recent N runs.

Each write goes to a uniquely named temp file in the same directory and is
then moved into place with ``os.replace``. Within this process, a
process-wide lock serialises read-modify-write sequences. Across processes,
files are never torn, but concurrent writers can lose updates.
"""

from __future__ import annotations

import json
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from backend.config import settings
from backend.models.scheduler import ScheduledJob, ScheduledRun
from backend.storage.atomic import atomic_write_text


_JOBS_FILE = "schedules.json"
_RUNS_DIR = "scheduler_runs"
_MAX_RUNS_PER_JOB = 50

# Serialises read-modify-write cycles on schedule files within this process.
_IO_LOCK = threading.RLock()


def _atomic_write_text(path: Path, text: str) -> None:
    atomic_write_text(path, text)


class ScheduleStore:
    def __init__(self, root_dir: Path | None = None) -> None:
        self._root = root_dir or settings.state_dir
        self._root.mkdir(parents=True, exist_ok=True)
        self._jobs_file = self._root / _JOBS_FILE
        self._runs_dir = self._root / _RUNS_DIR
        self._runs_dir.mkdir(parents=True, exist_ok=True)

    def _new_id(self) -> str:
        return uuid.uuid4().hex[:12]

    def _read_all(self) -> dict[str, dict[str, Any]]:
        if not self._jobs_file.exists():
            return {}
        try:
            raw = json.loads(self._jobs_file.read_text())
            return raw if isinstance(raw, dict) else {}
        except Exception:
            return {}

    def _write_all(self, data: dict[str, dict[str, Any]]) -> None:
        _atomic_write_text(self._jobs_file, json.dumps(data, indent=2, default=str))

    def list(self) -> list[ScheduledJob]:
        out: list[ScheduledJob] = []
        for raw in self._read_all().values():
            try:
                out.append(ScheduledJob(**raw))
            except Exception:
                continue
        out.sort(key=lambda j: j.created_at)
        return out

    def get(self, job_id: str) -> ScheduledJob | None:
        raw = self._read_all().get(job_id)
        if raw is None:
            return None
        try:
            return ScheduledJob(**raw)
        except Exception:
            return None

    def create(self, job: ScheduledJob) -> ScheduledJob:
        if not job.id:
            job.id = self._new_id()
        job.created_at = job.created_at or datetime.now(timezone.utc)
        job.updated_at = datetime.now(timezone.utc)
        with _IO_LOCK:
            data = self._read_all()
            data[job.id] = json.loads(job.model_dump_json())
            self._write_all(data)
        return job

    def update(self, job: ScheduledJob) -> ScheduledJob:
        job.updated_at = datetime.now(timezone.utc)
        with _IO_LOCK:
            data = self._read_all()
            if job.id not in data:
                raise FileNotFoundError(job.id)
            data[job.id] = json.loads(job.model_dump_json())
            self._write_all(data)
        return job

    def delete(self, job_id: str) -> bool:
        with _IO_LOCK:
            data = self._read_all()
            if job_id not in data:
                return False
            del data[job_id]
            self._write_all(data)
            run_path = self._run_path(job_id)
            if run_path.exists():
                try:
                    run_path.unlink()
                except FileNotFoundError:
                    pass
        return True

    # -------- runs --------

    def _run_path(self, job_id: str) -> Path:
        return self._runs_dir / f"{job_id}.json"

    def append_run(self, run: ScheduledRun) -> None:
        path = self._run_path(run.job_id)
        with _IO_LOCK:
            existing: list[dict[str, Any]] = []
            if path.exists():
                try:
                    existing = json.loads(path.read_text()) or []
                except Exception:
                    existing = []
            if not isinstance(existing, list):
                existing = []
            existing.append(json.loads(run.model_dump_json()))
            if len(existing) > _MAX_RUNS_PER_JOB:
                existing = existing[-_MAX_RUNS_PER_JOB:]
            _atomic_write_text(path, json.dumps(existing, indent=2, default=str))

    def list_runs(self, job_id: str) -> list[ScheduledRun]:
        path = self._run_path(job_id)
        if not path.exists():
            return []
        try:
            raw = json.loads(path.read_text()) or []
        except Exception:
            return []
        out: list[ScheduledRun] = []
        for r in raw:
            try:
                out.append(ScheduledRun(**r))
            except Exception:
                continue
        out.sort(key=lambda r: r.fired_at, reverse=True)
        return out
