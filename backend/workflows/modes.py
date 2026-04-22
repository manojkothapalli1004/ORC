"""Workflow execution mode resolution."""

from __future__ import annotations

from backend.config import settings
from backend.models.core import (
    ProviderRole,
    WorkflowExecutionMode,
    WorkflowModeOverride,
    WorkflowModeOverrides,
    WorkflowModePolicy,
    WorkflowModeRolePolicy,
)

MODE_LABELS = {
    WorkflowExecutionMode.COMPACT: "Compact",
    WorkflowExecutionMode.NORMAL: "Normal",
    WorkflowExecutionMode.RICH: "Rich",
    WorkflowExecutionMode.GO_WILD: "Go Wild",
}

MODE_SUMMARIES = {
    WorkflowExecutionMode.COMPACT: "Lowest cost mode with tight context, strong compression, and minimal review depth.",
    WorkflowExecutionMode.NORMAL: "Balanced default mode for routine workflow execution.",
    WorkflowExecutionMode.RICH: "Higher quality mode with broader context, stronger review, and lighter compression.",
    WorkflowExecutionMode.GO_WILD: "Best-results-first mode with deeper context and review while still avoiding pointless waste.",
}


class WorkflowModeResolver:
    def __init__(self, app_settings=None) -> None:
        self._settings = app_settings or settings

    def default_mode(self) -> WorkflowExecutionMode:
        return WorkflowExecutionMode(self._settings.default_workflow_mode)

    def available_modes(self) -> list[dict[str, str]]:
        return [
            {
                "value": mode.value,
                "label": MODE_LABELS[mode],
                "summary": MODE_SUMMARIES[mode],
            }
            for mode in WorkflowExecutionMode
        ]

    def resolve(
        self,
        mode: WorkflowExecutionMode | str | None = None,
        *,
        overrides: WorkflowModeOverrides | None = None,
    ) -> WorkflowModePolicy:
        resolved_mode = WorkflowExecutionMode(mode or self.default_mode())
        policy = getattr(self, f"_policy_{resolved_mode.value}")()
        policy.mode = resolved_mode
        policy.label = MODE_LABELS[resolved_mode]
        policy.summary = MODE_SUMMARIES[resolved_mode]
        policy.roles = self._apply_role_overrides(policy.roles, overrides or WorkflowModeOverrides())
        return policy

    def provider_roles_for(
        self,
        policy: WorkflowModePolicy,
        *,
        overrides: WorkflowModeOverrides | None = None,
    ) -> list[ProviderRole]:
        role_overrides = (overrides or WorkflowModeOverrides()).role_overrides
        result: list[ProviderRole] = []
        for role in policy.roles:
            override = role_overrides.get(role.role)
            result.append(
                ProviderRole(
                    role=role.role,
                    provider=override.provider if override and override.provider else role.provider,
                    model=override.model if override and override.model else role.model,
                    mode=override.mode if override and override.mode else role.mode,
                    base_url=role.base_url,
                    metadata={
                        "workflow_mode": policy.mode.value,
                        "mode_label": policy.label,
                        "max_tokens": override.max_tokens if override and override.max_tokens else role.max_tokens,
                    },
                )
            )
        return result

    def summary_payload(self, policy: WorkflowModePolicy) -> dict:
        return {
            "mode": policy.mode.value,
            "label": policy.label,
            "summary": policy.summary,
            "roles": [role.model_dump(mode="json") for role in policy.roles],
            "budgets": policy.budgets.model_dump(mode="json"),
            "prompts": policy.prompts.model_dump(mode="json"),
            "context": policy.context.model_dump(mode="json"),
            "compression": policy.compression.model_dump(mode="json"),
            "parallelism": policy.parallelism.model_dump(mode="json"),
            "review": policy.review.model_dump(mode="json"),
        }

    def _apply_role_overrides(
        self,
        roles: list[WorkflowModeRolePolicy],
        overrides: WorkflowModeOverrides,
    ) -> list[WorkflowModeRolePolicy]:
        result: list[WorkflowModeRolePolicy] = []
        for role in roles:
            override = overrides.role_overrides.get(role.role)
            if not override:
                result.append(role)
                continue
            result.append(
                role.model_copy(
                    update={
                        "provider": override.provider or role.provider,
                        "model": override.model or role.model,
                        "mode": override.mode or role.mode,
                        "max_tokens": override.max_tokens or role.max_tokens,
                    }
                )
            )
        return result

    def _base_roles(self) -> list[WorkflowModeRolePolicy]:
        return [
            WorkflowModeRolePolicy(
                role="reviewer",
                provider=self._settings.reviewer_provider,
                model=self._settings.reviewer_model,
                mode=self._settings.reviewer_mode,
                base_url=self._settings.router_base_url if self._settings.reviewer_mode == "router" else "",
                max_tokens=self._settings.max_tokens_per_response,
            ),
            WorkflowModeRolePolicy(
                role="planner",
                provider=self._settings.planner_provider,
                model=self._settings.planner_model,
                mode=self._settings.planner_mode,
                base_url=self._settings.router_base_url if self._settings.planner_mode == "router" else "",
                max_tokens=self._settings.max_tokens_per_response,
            ),
            WorkflowModeRolePolicy(
                role="builder",
                provider=self._settings.builder_provider,
                model=self._settings.builder_model,
                mode=self._settings.builder_mode,
                base_url=self._settings.router_base_url if self._settings.builder_mode == "router" else "",
                max_tokens=self._settings.max_tokens_per_response,
            ),
        ]

    def _policy_normal(self) -> WorkflowModePolicy:
        roles = self._base_roles()
        return WorkflowModePolicy(
            roles=roles,
            budgets={
                "max_files_per_batch": self._settings.max_files_per_batch,
                "reviewer_max_tokens": self._settings.max_tokens_per_response,
                "planner_max_tokens": self._settings.max_tokens_per_response,
                "builder_max_tokens": self._settings.max_tokens_per_response,
                "total_token_target": self._settings.max_tokens_per_response * 3,
                "cost_intensity": "balanced",
            },
            prompts={
                "template_compactness": "balanced",
                "reviewer_system_style": "balanced",
                "builder_system_style": "balanced",
                "planner_system_style": "balanced",
            },
            context={
                "recent_batches": 5,
                "response_preview_chars": 200,
                "context_detail": "standard",
            },
            compression={
                "summarization": "balanced",
                "compress_completed_batches": True,
                "compress_long_context": True,
            },
            parallelism={
                "session_fan_out": 1,
                "provider_parallelism": 1,
                "allow_parallel_roles": False,
            },
            review={
                "review_depth": "standard",
                "reviewer_passes": 1,
                "builder_retry_budget": 0,
            },
        )

    def _policy_compact(self) -> WorkflowModePolicy:
        policy = self._policy_normal()
        return policy.model_copy(
            update={
                "budgets": policy.budgets.model_copy(update={
                    "max_files_per_batch": min(policy.budgets.max_files_per_batch, 3),
                    "reviewer_max_tokens": 2200,
                    "planner_max_tokens": 1800,
                    "builder_max_tokens": 3200,
                    "total_token_target": 7200,
                    "cost_intensity": "lean",
                }),
                "roles": [
                    role.model_copy(update={
                        "model": self._compact_model_for(role.role, role.model),
                        "max_tokens": {"reviewer": 2200, "planner": 1800, "builder": 3200}[role.role],
                    })
                    for role in policy.roles
                ],
                "prompts": policy.prompts.model_copy(update={
                    "template_compactness": "tight",
                    "reviewer_system_style": "strict",
                    "builder_system_style": "tight",
                    "planner_system_style": "tight",
                }),
                "context": policy.context.model_copy(update={
                    "recent_batches": 2,
                    "response_preview_chars": 120,
                    "context_detail": "minimal",
                }),
                "compression": policy.compression.model_copy(update={
                    "summarization": "aggressive",
                    "compress_completed_batches": True,
                    "compress_long_context": True,
                }),
                "parallelism": policy.parallelism.model_copy(update={
                    "session_fan_out": 1,
                    "provider_parallelism": 1,
                    "allow_parallel_roles": False,
                }),
                "review": policy.review.model_copy(update={
                    "review_depth": "light",
                    "reviewer_passes": 1,
                    "builder_retry_budget": 0,
                }),
            }
        )

    def _policy_rich(self) -> WorkflowModePolicy:
        policy = self._policy_normal()
        return policy.model_copy(
            update={
                "budgets": policy.budgets.model_copy(update={
                    "max_files_per_batch": max(policy.budgets.max_files_per_batch, 7),
                    "reviewer_max_tokens": 7000,
                    "planner_max_tokens": 6000,
                    "builder_max_tokens": 9000,
                    "total_token_target": 22000,
                    "cost_intensity": "quality",
                }),
                "roles": [
                    role.model_copy(update={
                        "model": self._rich_model_for(role.role, role.model),
                        "max_tokens": {"reviewer": 7000, "planner": 6000, "builder": 9000}[role.role],
                    })
                    for role in policy.roles
                ],
                "prompts": policy.prompts.model_copy(update={
                    "template_compactness": "detailed",
                    "reviewer_system_style": "analytical",
                    "builder_system_style": "detailed",
                    "planner_system_style": "detailed",
                }),
                "context": policy.context.model_copy(update={
                    "recent_batches": 8,
                    "response_preview_chars": 320,
                    "context_detail": "expanded",
                }),
                "compression": policy.compression.model_copy(update={
                    "summarization": "light",
                    "compress_completed_batches": False,
                    "compress_long_context": False,
                }),
                "parallelism": policy.parallelism.model_copy(update={
                    "session_fan_out": 2,
                    "provider_parallelism": 2,
                    "allow_parallel_roles": True,
                }),
                "review": policy.review.model_copy(update={
                    "review_depth": "deep",
                    "reviewer_passes": 2,
                    "builder_retry_budget": 1,
                }),
            }
        )

    def _policy_go_wild(self) -> WorkflowModePolicy:
        policy = self._policy_rich()
        return policy.model_copy(
            update={
                "budgets": policy.budgets.model_copy(update={
                    "max_files_per_batch": max(policy.budgets.max_files_per_batch, 9),
                    "reviewer_max_tokens": 9000,
                    "planner_max_tokens": 8000,
                    "builder_max_tokens": 12000,
                    "total_token_target": 29000,
                    "cost_intensity": "best_results",
                }),
                "roles": [
                    role.model_copy(update={
                        "model": self._go_wild_model_for(role.role, role.model),
                        "max_tokens": {"reviewer": 9000, "planner": 8000, "builder": 12000}[role.role],
                    })
                    for role in policy.roles
                ],
                "prompts": policy.prompts.model_copy(update={
                    "template_compactness": "full",
                    "reviewer_system_style": "maximal",
                    "builder_system_style": "maximal",
                    "planner_system_style": "maximal",
                }),
                "context": policy.context.model_copy(update={
                    "recent_batches": 12,
                    "response_preview_chars": 480,
                    "context_detail": "full",
                }),
                "compression": policy.compression.model_copy(update={
                    "summarization": "minimal",
                    "compress_completed_batches": False,
                    "compress_long_context": False,
                }),
                "parallelism": policy.parallelism.model_copy(update={
                    "session_fan_out": 3,
                    "provider_parallelism": 3,
                    "allow_parallel_roles": True,
                }),
                "review": policy.review.model_copy(update={
                    "review_depth": "exhaustive",
                    "reviewer_passes": 2,
                    "builder_retry_budget": 2,
                }),
            }
        )

    def _compact_model_for(self, role: str, fallback: str) -> str:
        if role == "builder":
            return self._settings.compact_builder_model or fallback
        if role == "planner":
            return self._settings.compact_planner_model or fallback
        return self._settings.compact_reviewer_model or fallback

    def _rich_model_for(self, role: str, fallback: str) -> str:
        if role == "builder":
            return self._settings.rich_builder_model or fallback
        if role == "planner":
            return self._settings.rich_planner_model or fallback
        return self._settings.rich_reviewer_model or fallback

    def _go_wild_model_for(self, role: str, fallback: str) -> str:
        if role == "builder":
            return self._settings.go_wild_builder_model or fallback
        if role == "planner":
            return self._settings.go_wild_planner_model or fallback
        return self._settings.go_wild_reviewer_model or fallback
