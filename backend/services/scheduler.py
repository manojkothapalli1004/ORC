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
  the mock provider, which makes no external API call. A step that leaves
  the workflow in ``error`` status is recorded as an ``error`` run.

Each run is recorded with status ``ok`` or ``error``.

Concurrency: each job has its own non-blocking lock, so a job never runs
twice at the same time (a second fire while one is in progress is recorded
as an ``error`` run with output ``job already running: <id>``). After a run,
only ``last_run_at``/``last_status``/``last_output`` are written back onto a
fresh copy of the stored job, so edits made while the job was running
(e.g. disabling it) are not overwritten.
"""

from __future__ import annotations

import logging
import threading
from datetime import datetime, timezone
from typing import Any

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from backend.models.core import WorkflowStatus
from backend.models.memory import MemoryUpdatePatch
from backend.models.scheduler import ScheduledActionType, ScheduledJob, ScheduledRun
from backend.storage.memory import MemoryStore
from backend.storage.prompts import PromptTemplateStore
from backend.storage.schedules import ScheduleStore
from backend.storage.store import StateStore


logger = logging.getLogger("orchestrator.scheduler")


# Standard cron day-of-week numbering: 0 (and 7) = Sunday, 1 = Monday, ...
_CRON_DOW_NAMES = ("sun", "mon", "tue", "wed", "thu", "fri", "sat")


def _translate_dow(dow: str) -> str:
    """Translate a standard-cron day-of-week field into APScheduler day names.

    APScheduler 3.x numbers weekdays from Monday=0, while standard cron uses
    Sunday=0 (and 7). Numeric items are expanded into explicit day names so
    the numbering can never be misread. Name items are passed through.

    Raises ValueError for values > 7, wrapping ranges (a > b), bad steps or
    empty items.
    """
    dow = dow.strip()
    if dow == "*":
        return "*"
    out: list[str] = []
    for raw_item in dow.split(","):
        item = raw_item.strip()
        if not item:
            raise ValueError(f"empty item in day-of-week field: {dow!r}")
        base, slash, step_s = item.partition("/")
        if slash:
            if not step_s.isdigit() or int(step_s) < 1:
                raise ValueError(f"invalid step in day-of-week item: {item!r}")
            step = int(step_s)
        else:
            step = 1
        if base == "*":
            lo, hi = 0, 6
        else:
            first, dash, last = base.partition("-")
            first_num = first.isdigit()
            last_num = last.isdigit() if dash else False
            if not first_num and not last_num:
                # Day names (e.g. 'mon', 'Mon-Fri'): pass through for APScheduler.
                if dash and not last:
                    raise ValueError(f"invalid day-of-week item: {item!r}")
                out_item = item.lower()
                if out_item not in out:
                    out.append(out_item)
                continue
            if not first_num or (dash and not last_num):
                raise ValueError(f"cannot mix numbers and names in day-of-week item: {item!r}")
            lo = int(first)
            if dash:
                hi = int(last)
            elif slash:
                hi = 7 if lo == 7 else 6
            else:
                hi = lo
            if lo > 7 or hi > 7:
                raise ValueError(f"day-of-week value out of range 0-7: {item!r}")
            if lo > hi:
                raise ValueError(f"wrapping day-of-week range not supported: {item!r}")
        for value in range(lo, hi + 1, step):
            name = _CRON_DOW_NAMES[value % 7]
            if name not in out:
                out.append(name)
    return ",".join(out)


def parse_cron(expr: str) -> CronTrigger:
    """5-field cron expression -> APScheduler CronTrigger (local timezone).

    The day-of-week field uses standard cron numbering (0 and 7 = Sunday,
    1 = Monday, ...) and is translated to day names before being handed to
    APScheduler, which would otherwise treat 0 as Monday.

    Raises ValueError if the expression is malformed.
    """
    parts = expr.strip().split()
    if len(parts) != 5:
        raise ValueError(
            f"cron expression must have exactly 5 fields (minute hour day month dow), got {len(parts)}"
        )
    minute, hour, day, month, dow = parts
    return CronTrigger(
        minute=minute,
        hour=hour,
        day=day,
        month=month,
        day_of_week=_translate_dow(dow),
    )


def _run_coro(coro: Any) -> Any:
    """Run a coroutine to completion from sync code.

    APScheduler and the run-now API route (via asyncio.to_thread) call us on
    a worker thread with no running loop, so asyncio.run is used. As a
    defensive fallback, if a loop is already running on this thread the
    coroutine is run on a helper thread instead.
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
        self._job_locks: dict[str, threading.Lock] = {}

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

    def _job_lock(self, job_id: str) -> threading.Lock:
        """Return the per-job lock, creating the lock dict lazily.

        Lazy creation keeps Scheduler objects built via ``__new__`` (without
        ``__init__``) working.
        """
        with self._lock:
            locks = self.__dict__.setdefault("_job_locks", {})
            lock = locks.get(job_id)
            if lock is None:
                lock = threading.Lock()
                locks[job_id] = lock
            return lock

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
        job_lock = self._job_lock(job.id)
        if not job_lock.acquire(blocking=False):
            output = f"job already running: {job.id}"
            run = ScheduledRun(
                job_id=job.id,
                fired_at=datetime.now(timezone.utc),
                status="error",
                output=output,
            )
            logger.warning("scheduled job %s skipped: already running", job.id)
            try:
                self._store.append_run(run)
            except Exception as exc:
                logger.warning("could not record skipped run for %s: %s", job.id, exc)
            return run
        try:
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
            output = output if isinstance(output, str) else str(output)
            run = ScheduledRun(job_id=latest.id, fired_at=fired_at, status=status, output=output[:4000])
            self._store.append_run(run)
            latest.last_run_at = fired_at
            latest.last_status = status
            latest.last_output = output[:1000]
            # Persist only the run-result fields onto a fresh copy so that any
            # concurrent edit (disable, rename, ...) made during the run survives.
            with self._lock:
                fresh = self._store.get(latest.id)
                if fresh is not None:
                    fresh.last_run_at = latest.last_run_at
                    fresh.last_status = latest.last_status
                    fresh.last_output = latest.last_output
                    try:
                        self._store.update(fresh)
                    except FileNotFoundError:
                        pass
            return run
        finally:
            job_lock.release()

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
            state = _run_coro(engine.step(state))
            self._state.save(state)
            if state.status == WorkflowStatus.ERROR:
                raise RuntimeError(f"workflow step failed: id={wf_id} error={state.error}")
            return f"workflow stepped: id={wf_id} status={state.status.value}"

        raise ValueError(f"unknown action_type: {job.action_type}")


_singleton: Scheduler | None = None


def get_scheduler() -> Scheduler:
    global _singleton
    if _singleton is None:
        _singleton = Scheduler()
    return _singleton
