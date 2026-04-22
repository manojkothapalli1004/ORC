"""Provider-agnostic local builder worker runtime."""

from __future__ import annotations

import asyncio
import socket
from collections.abc import Callable
from pathlib import Path
from typing import Protocol

from backend.models import (
    BuilderExecutionRequest,
    BuilderExecutionResult,
    BuilderJob,
    BuilderJobTerminalStatus,
    ProviderRole,
    WorkerAdapterKind,
)
from backend.providers import ProviderRegistry
from backend.storage import BuilderJobStore, StateStore
from backend.storage.builder_jobs import execution_request_metadata

CONTRACT_VERSION = "claude_handoff_v1"
HANDOFF_CONSUMER = "local_claude_antigravity_handoff"
EXPECTED_RETURN_FORMAT = "structured_text_contract_v1"


class BuilderExecutionAdapter(Protocol):
    kind: WorkerAdapterKind

    def execute(self, request: BuilderExecutionRequest) -> BuilderExecutionResult:
        ...


class ManualSafeAdapter:
    kind = WorkerAdapterKind.MANUAL_SAFE

    def execute(self, request: BuilderExecutionRequest) -> BuilderExecutionResult:
        if request.handoff_contract is None:
            raise ValueError("Builder execution request is missing Claude handoff contract")
        return BuilderExecutionResult(
            status=BuilderJobTerminalStatus.COMPLETED,
            summary=f"Manual-safe adapter recorded the approved {request.category.value} job without external execution.",
            output_ref=f"manual://builder-jobs/{request.job_id}",
            metadata={
                "adapter_kind": self.kind.value,
                "mode": "manual_safe",
                "category": request.category.value,
                "contract_version": CONTRACT_VERSION,
                "consumer": HANDOFF_CONSUMER,
                "expected_return_format": EXPECTED_RETURN_FORMAT,
                "prompt_preview": request.handoff_contract.prompt_text,
            },
        )


class MockExecutionAdapter:
    kind = WorkerAdapterKind.MOCK

    def execute(self, request: BuilderExecutionRequest) -> BuilderExecutionResult:
        if request.handoff_contract is None:
            raise ValueError("Builder execution request is missing Claude handoff contract")
        return BuilderExecutionResult(
            status=BuilderJobTerminalStatus.COMPLETED,
            summary=f"Mock adapter completed the local {request.category.value} builder job.",
            output_ref=f"mock://builder-jobs/{request.job_id}",
            artifacts=[],
            metadata={
                "adapter_kind": self.kind.value,
                "mode": "mock",
                "category": request.category.value,
                "contract_version": CONTRACT_VERSION,
                "consumer": HANDOFF_CONSUMER,
                "expected_return_format": EXPECTED_RETURN_FORMAT,
                "prompt_preview": request.handoff_contract.prompt_text,
            },
        )


class ProviderExecutionAdapter:
    kind = WorkerAdapterKind.PROVIDER

    def __init__(
        self,
        *,
        registry: ProviderRegistry | None = None,
        state_store: StateStore | None = None,
    ) -> None:
        self.registry = registry or ProviderRegistry()
        self.state_store = state_store or StateStore()

    def execute(self, request: BuilderExecutionRequest) -> BuilderExecutionResult:
        if request.handoff_contract is None:
            raise ValueError("Builder execution request is missing Claude handoff contract")

        provider_config = self._resolve_provider_config(request)
        provider = self.registry.get_provider("builder", [provider_config])
        provider_response = asyncio.run(
            provider.complete(
                request=self._provider_request(request, provider_config)
            )
        )
        terminal_status = (
            BuilderJobTerminalStatus.COMPLETED
            if provider_response.content or provider_response.is_mock
            else BuilderJobTerminalStatus.FAILED
        )
        return BuilderExecutionResult(
            status=terminal_status,
            summary=provider_response.content or "Provider returned no content.",
            output_ref=f"provider://{provider_response.provider_name}/{request.job_id}",
            artifacts=[],
            error=None if terminal_status == BuilderJobTerminalStatus.COMPLETED else "Empty provider response",
            metadata={
                "adapter_kind": self.kind.value,
                "provider_name": provider_response.provider_name,
                "provider_model": provider_response.model_used,
                "provider_request_id": provider_response.request_id,
                "token_usage": provider_response.token_usage.model_dump(),
                "latency_ms": provider_response.latency_ms,
                "is_mock": provider_response.is_mock,
                "configured_provider": provider_config.provider,
                "configured_mode": provider_config.mode,
                "configured_base_url": provider_config.base_url,
                "category": request.category.value,
                "contract_version": CONTRACT_VERSION,
                "consumer": HANDOFF_CONSUMER,
                "expected_return_format": EXPECTED_RETURN_FORMAT,
            },
        )

    def _resolve_provider_config(self, request: BuilderExecutionRequest) -> ProviderRole:
        state = self.state_store.load(request.workflow_id)
        if state is not None:
            for item in state.providers:
                if item.role == "builder":
                    return item
        return self.registry.role_map()["builder"]

    def _provider_request(self, request: BuilderExecutionRequest, provider_config: ProviderRole):
        from backend.providers.base import ProviderRequest

        prompt = request.handoff_contract.prompt_text if request.handoff_contract else request.proposal_prompt
        return ProviderRequest(
            role="builder",
            prompt=prompt,
            system_prompt=(
                "You are the local builder worker for the orchestrator. "
                "Follow the provided handoff contract exactly. "
                "Stay within orchestrator scope. Do not execute shell commands or control live systems."
            ),
            model=provider_config.model,
            metadata={
                **request.metadata,
                "provider_mode": provider_config.mode,
                "provider_name": provider_config.provider,
                "provider_base_url": provider_config.base_url,
            },
        )


class LocalBuilderWorkerRuntime:
    def __init__(
        self,
        store: BuilderJobStore | None = None,
        adapter: BuilderExecutionAdapter | None = None,
        worker_id: str | None = None,
        state_store: StateStore | None = None,
    ) -> None:
        self.store = store or BuilderJobStore()
        self.state_store = state_store or StateStore()
        self.adapter = adapter or ProviderExecutionAdapter(state_store=self.state_store)
        self.worker_id = worker_id or f"local-worker-{socket.gethostname()}"

    def watch_once(self) -> BuilderJob | None:
        job = self.store.claim_next_pending(self.worker_id)
        if job is None:
            return None
        execution = self._run(job)
        return self.store.finalize_execution(job.id, execution)

    def watch_batch(self, limit: int = 1) -> list[BuilderJob]:
        processed: list[BuilderJob] = []
        for _ in range(max(limit, 0)):
            job = self.watch_once()
            if job is None:
                break
            processed.append(job)
        return processed

    def _run(self, job: BuilderJob) -> BuilderExecutionResult:
        request = BuilderExecutionRequest(
            job_id=job.id,
            workflow_id=job.workflow_id,
            proposal_id=job.proposal_id,
            proposal_batch_index=job.proposal_batch_index,
            proposal_prompt=job.proposal_prompt,
            category=job.category,
            handoff_contract=job.handoff_contract,
            metadata=execution_request_metadata(job, self.worker_id),
        )
        try:
            result = self.adapter.execute(request)
        except Exception as exc:
            return BuilderExecutionResult(
                status=BuilderJobTerminalStatus.FAILED,
                summary="Local builder worker runtime failed during adapter execution.",
                error=str(exc),
                metadata={
                    "adapter_kind": self.adapter.kind.value,
                    "worker_id": self.worker_id,
                    "category": job.category.value,
                    "contract_version": CONTRACT_VERSION,
                    "consumer": HANDOFF_CONSUMER,
                    "expected_return_format": EXPECTED_RETURN_FORMAT,
                },
            )
        result.metadata = {
            **result.metadata,
            "adapter_kind": self.adapter.kind.value,
            "worker_id": self.worker_id,
            "category": job.category.value,
            "contract_version": CONTRACT_VERSION,
            "consumer": HANDOFF_CONSUMER,
            "expected_return_format": EXPECTED_RETURN_FORMAT,
        }
        return result


def run_local_builder_worker(
    *,
    adapter: BuilderExecutionAdapter | None = None,
    store: BuilderJobStore | None = None,
    state_store: StateStore | None = None,
    worker_id: str | None = None,
    limit: int = 1,
) -> list[BuilderJob]:
    runtime = LocalBuilderWorkerRuntime(
        store=store,
        adapter=adapter,
        state_store=state_store,
        worker_id=worker_id,
    )
    return runtime.watch_batch(limit=limit)


def builder_worker_entrypoint(
    *,
    root_dir: Path | None = None,
    adapter_factory: Callable[[], BuilderExecutionAdapter] | None = None,
    worker_id: str | None = None,
    limit: int = 1,
) -> list[BuilderJob]:
    store = BuilderJobStore(root_dir=root_dir)
    state_store = StateStore()
    adapter = adapter_factory() if adapter_factory else ProviderExecutionAdapter(state_store=state_store)
    return run_local_builder_worker(
        adapter=adapter,
        store=store,
        state_store=state_store,
        worker_id=worker_id,
        limit=limit,
    )
