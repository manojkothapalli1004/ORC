"""Local cron scheduler.

Wraps APScheduler's BackgroundScheduler with a small action registry.
Actions are restricted to a whitelist. No scheduled action runs a shell
command.

Action types:
- ``fire_saved_prompt`` — render a saved prompt template's body and append
  it to the per-job runs log. Useful as a manual hand-off cue ("at 9am every
  day, queue up this prompt for me to copy into a Claude session").
- ``record_memory_note`` — append a dated note to a canonical memory section
  via the existing MemoryStore.apply_patches path.
- ``run_workflow_step`` — advance the named workflow one step through the
  workflow engine and the configured LLM providers. It can make network
  calls when any provider is configured, including a router base URL with
  no key. A role using ``claude_code_local`` starts a local ``claude`` CLI
  process, which talks to Anthropic. With no provider configured it uses
  the mock provider, which makes no external API call.

Each run is recorded with status ``ok`` or ``error``.
"""

from __future__ import annotations

import logging
import threading
from datetime import datetime, timezone
from typing import Any

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from backend.models.memory import MemoryUpdatePatch
from backend.models.scheduler import ScheduledActionType, ScheduledJob, ScheduledRun
from backend.storage.memory import MemoryStore
from backend.storage.prompts import PromptTemplateStore
from backend.storage.schedules import ScheduleStore
from backend.storage.store import StateStore


logger = logging.getLogger("orchestrator.scheduler")


def parse_cron(expr: str) -> CronTrigger:
    """5-field cron expression -> APScheduler CronTrigger (local timezone).

    Raises ValueError if the expression is malformed.
    """
    parts = expr.strip().split()
    if len(parts) != 5:
        raise ValueError(
            f"cron expression must have exactly 5 fields (minute hour day month dow), got {len(parts)}"
        )
    minute, hour, day, month, dow = parts
    return CronTrigger(minute=minute, hour=hour, day=day, month=month, day_of_week=dow)


def _run_coro(coro: Any) -> Any:
    """Run a coroutine to completion from sync code.

    APScheduler fires us on a worker thread (no loop), so asyncio.run works.
    If called from inside a running loop (e.g. run-now via an async API
    route), run it on a fresh loop in a helper thread instead.
    """
    import asyncio

    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)

    result: dict[str, Any] = {}

    def _target() -> None:
        try:
            result["value"] = asyncio.run(coro)
        except BaseException as exc:  # pragma: no cover - re-raised below
            result["error"] = exc

    t = threading.Thread(target=_target, name="scheduler-workflow-step")
    t.start()
    t.join()
    if "error" in result:
        raise result["error"]
    return result.get("value")


class Scheduler:
    def __init__(self) -> None:
        self._sched = BackgroundScheduler()
        self._store = ScheduleStore()
        self._prompts = PromptTemplateStore()
        self._memory = MemoryStore()
        self._state = StateStore()
        self._lock = threading.Lock()
        self._started = False

    # -------- lifecycle --------

    def start(self) -> None:
        if self._started:
            return
        self._sched.start()
        self._started = True
        for job in self._store.list():
            if job.enabled:
                try:
                    self._add_to_scheduler(job)
                except Exception as exc:
                    logger.warning("could not schedule job %s: %s", job.id, exc)

    def shutdown(self) -> None:
        if not self._started:
            return
        self._sched.shutdown(wait=False)
        self._started = False

    # -------- public CRUD (also keeps APScheduler in sync) --------

    def create(self, job: ScheduledJob) -> ScheduledJob:
        # Validate cron up front so the operator gets immediate feedback.
        parse_cron(job.cron)
        with self._lock:
            saved = self._store.create(job)
            if saved.enabled and self._started:
                self._add_to_scheduler(saved)
            saved.next_run_at = self._next_fire_time(saved.id)
            if saved.next_run_at:
                self._store.update(saved)
        return saved

    def update(self, job_id: str, **fields: Any) -> ScheduledJob | None:
        with self._lock:
            job = self._store.get(job_id)
            if job is None:
                return None
            if "cron" in fields and fields["cron"] is not None:
                parse_cron(fields["cron"])
                job.cron = fields["cron"]
            if "name" in fields and fields["name"] is not None:
                job.name = fields["name"]
            if "enabled" in fields and fields["enabled"] is not None:
                job.enabled = bool(fields["enabled"])
            if "action_payload" in fields and fields["action_payload"] is not None:
                job.action_payload = fields["action_payload"]
            self._store.update(job)
            # Resync the scheduler.
            self._remove_from_scheduler(job.id)
            if job.enabled and self._started:
                self._add_to_scheduler(job)
            job.next_run_at = self._next_fire_time(job.id)
            self._store.update(job)
            return job

    def delete(self, job_id: str) -> bool:
        with self._lock:
            self._remove_from_scheduler(job_id)
            return self._store.delete(job_id)

    def list_jobs(self) -> list[ScheduledJob]:
        jobs = self._store.list()
        for j in jobs:
            j.next_run_at = self._next_fire_time(j.id)
        return jobs

    def get(self, job_id: str) -> ScheduledJob | None:
        job = self._store.get(job_id)
        if job is not None:
            job.next_run_at = self._next_fire_time(job.id)
        return job

    def list_runs(self, job_id: str) -> list[ScheduledRun]:
        return self._store.list_runs(job_id)

    def run_now(self, job_id: str) -> ScheduledRun | None:
        job = self._store.get(job_id)
        if job is None:
            return None
        return self._fire(job)

    # -------- internals --------

    def _add_to_scheduler(self, job: ScheduledJob) -> None:
        trigger = parse_cron(job.cron)
        self._sched.add_job(
            self._fire,
            trigger=trigger,
            args=[job],
            id=job.id,
            replace_existing=True,
            misfire_grace_time=60,
            coalesce=True,
        )

    def _remove_from_scheduler(self, job_id: str) -> None:
        try:
            self._sched.remove_job(job_id)
        except Exception:
            pass

    def _next_fire_time(self, job_id: str) -> datetime | None:
        try:
            sj = self._sched.get_job(job_id)
            return sj.next_run_time if sj else None
        except Exception:
            return None

    def _fire(self, job: ScheduledJob) -> ScheduledRun:
        # Re-load latest definition in case it was updated since the trigger fired.
        latest = self._store.get(job.id) or job
        fired_at = datetime.now(timezone.utc)
        try:
            output = self._dispatch_action(latest)
            status = "ok"
        except Exception as exc:
            output = f"{type(exc).__name__}: {exc}"
            status = "error"
            logger.warning("scheduled job %s failed: %s", latest.id, output)
        run = ScheduledRun(job_id=latest.id, fired_at=fired_at, status=status, output=output[:4000])
        self._store.append_run(run)
        latest.last_run_at = fired_at
        latest.last_status = status
        latest.last_output = output[:1000]
        try:
            self._store.update(latest)
        except FileNotFoundError:
            pass
        return run

    def _dispatch_action(self, job: ScheduledJob) -> str:
        payload = job.action_payload or {}
        if job.action_type == ScheduledActionType.FIRE_SAVED_PROMPT:
            prompt_id = payload.get("prompt_id", "")
            if not prompt_id:
                raise ValueError("fire_saved_prompt requires action_payload.prompt_id")
            template = self._prompts.load(prompt_id)
            if template is None:
                raise ValueError(f"saved prompt not found: {prompt_id}")
            body = template.body or ""
            return f"prompt fired: id={prompt_id} name={template.name!r}\n--- body ---\n{body}"

        if job.action_type == ScheduledActionType.RECORD_MEMORY_NOTE:
            section = (payload.get("section") or "").strip().lower()
            note = (payload.get("note") or "").strip()
            if not section or not note:
                raise ValueError("record_memory_note requires section and note")
            patch = MemoryUpdatePatch(
                section=section,
                note=note,
                replace=False,
                source=f"scheduler:{job.id}",
            )
            _, applied, skipped = self._memory.apply_patches([patch])
            if applied:
                return f"memory updated: applied={applied}"
            return f"memory update skipped: skipped={skipped}"

        if job.action_type == ScheduledActionType.RUN_WORKFLOW_STEP:
            wf_id = payload.get("workflow_id", "")
            if not wf_id:
                raise ValueError("run_workflow_step requires workflow_id")
            state = self._state.load(wf_id)
            if state is None:
                raise ValueError(f"workflow not found: {wf_id}")
            from backend.workflows.factory import build_engine
            engine = build_engine(state, self._state)
            # WorkflowEngine.step is async; APScheduler fires us on a worker
            # thread, so spin up a fresh loop here.
            state = _run_coro(engine.step(state))
            self._state.save(state)
            return f"workflow stepped: id={wf_id} status={state.status.value}"

        raise ValueError(f"unknown action_type: {job.action_type}")


_singleton: Scheduler | None = None


def get_scheduler() -> Scheduler:
    global _singleton
    if _singleton is None:
        _singleton = Scheduler()
    return _singleton
