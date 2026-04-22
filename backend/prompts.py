"""Prompt OS composition helpers."""

from __future__ import annotations

import re

from backend.models.prompt import (
    GeneratedPromptPayload,
    PromptCompositionMode,
    PromptContext,
    PromptPreset,
    PromptPresetName,
    PromptRole,
    PromptTemplate,
    PromptTemplateCategory,
)
from backend.storage.prompts import PromptTemplateStore


PRESETS: dict[PromptPresetName, PromptPreset] = {
    PromptPresetName.BUILDER_STANDALONE: PromptPreset(
        name=PromptPresetName.BUILDER_STANDALONE,
        role=PromptRole.BUILDER,
        mode=PromptCompositionMode.NORMAL,
        description="Builder working alone on a scoped task",
    ),
    PromptPresetName.BUILDER_PARALLEL: PromptPreset(
        name=PromptPresetName.BUILDER_PARALLEL,
        role=PromptRole.BUILDER,
        mode=PromptCompositionMode.NORMAL,
        include_parallel_rules=True,
        description="Builder in a parallel session with file isolation",
    ),
    PromptPresetName.REVIEWER_STANDALONE: PromptPreset(
        name=PromptPresetName.REVIEWER_STANDALONE,
        role=PromptRole.REVIEWER,
        mode=PromptCompositionMode.NORMAL,
        description="Reviewer doing a full review pass",
    ),
    PromptPresetName.VERIFIER_STANDALONE: PromptPreset(
        name=PromptPresetName.VERIFIER_STANDALONE,
        role=PromptRole.VERIFIER,
        mode=PromptCompositionMode.NORMAL,
        description="Verifier running safe local checks",
    ),
    PromptPresetName.PLANNER_STANDALONE: PromptPreset(
        name=PromptPresetName.PLANNER_STANDALONE,
        role=PromptRole.PLANNER,
        mode=PromptCompositionMode.NORMAL,
        description="Planner producing a bounded implementation plan",
    ),
    PromptPresetName.FULL_PARALLEL: PromptPreset(
        name=PromptPresetName.FULL_PARALLEL,
        role=PromptRole.BUILDER,
        mode=PromptCompositionMode.RICH,
        include_parallel_rules=True,
        description="Full-context parallel session with all templates",
    ),
}

_VAR_PATTERN = re.compile(r"\{\{(\w+)\}\}")

# Mode controls which template categories are included.
# COMPACT: role + safety only (minimal framing, task-forward)
# NORMAL: role + safety + startup + return_format (standard operating prompt)
# RICH: everything including extra_sections and full context dumps
_MODE_CATEGORIES: dict[PromptCompositionMode, set[str]] = {
    PromptCompositionMode.COMPACT: {"role", "safety"},
    PromptCompositionMode.NORMAL: {"startup", "role", "safety", "return_format"},
    PromptCompositionMode.RICH: {"startup", "role", "parallel_rule", "safety", "return_format"},
}

# Categories that must appear at most once per composed prompt. Anything
# belonging to these categories is deduped on first insertion. Keeps a single
# authoritative return_format / safety / verification block and prevents the
# "two conflicting return formats" footgun.
_SINGLE_INSTANCE_CATEGORIES: set[str] = {
    "return_format",
    "safety",
    "parallel_rule",
    "verification",
    "discipline",
    "unresolved",
    "task",
}

_DISCIPLINE_GUARDRAILS = (
    "Operating discipline (apply to every batch):\n"
    "- Inspect current code/context before writing anything new.\n"
    "- Take the smallest clean implementation path that satisfies the scope.\n"
    "- If the actual file structure, symbols, or behaviour differ from what\n"
    "  this prompt assumes, stop and report the mismatch instead of patching\n"
    "  around it.\n"
    "- Do not invent files, endpoints, flags, or fields that you have not\n"
    "  verified exist. Quote the evidence (file + line or exact command).\n"
    "- Prefer editing existing files over creating new ones.\n"
    "- Prefer diffs over full rewrites.\n"
    "- Stop when the scope is done. Do not add adjacent cleanup or refactors."
)

_DEFAULT_VERIFICATION_STEPS = [
    "Syntax-check every edited file (py_compile / go build / node -c).",
    "Run any existing tests that cover the changed code paths.",
    "Load the module or hit the endpoint at least once to confirm import/wiring.",
    "State explicitly what was verified vs. what was only edited.",
]


class PromptComposer:
    def __init__(self, store: PromptTemplateStore | None = None) -> None:
        self.store = store or PromptTemplateStore()

    def compose(self, *, role: PromptRole, context: PromptContext) -> GeneratedPromptPayload:
        variables = self._collect_variables(context)
        templates = self._select_templates(role=role, context=context)
        unresolved: list[str] = []
        sections: list[dict] = []
        seen_section_ids: set[str] = set()
        seen_categories: set[str] = set()

        def add(section: dict) -> None:
            # Guardrail: no duplicate section ids and at most one section per
            # single-instance category (return_format, safety, parallel_rule).
            # Prevents the "two return formats with conflicting language" class
            # of bug that used to slip in when both a template and the context
            # expected_return_format were populated.
            sid = section.get("id", "")
            cat = section.get("category", "")
            if sid and sid in seen_section_ids:
                return
            if cat in _SINGLE_INSTANCE_CATEGORIES and cat in seen_categories:
                return
            if sid:
                seen_section_ids.add(sid)
            if cat:
                seen_categories.add(cat)
            sections.append(section)

        # --- Leading task block (always first when present) ---
        task_text = context.task or variables.get("task", "")
        if task_text:
            scope = variables.get("scope", "")
            files = variables.get("files_affected", "")
            header_lines = [f"Role: {role.value.title()}"]
            header_lines.append(f"Task: {task_text}")
            if scope:
                header_lines.append(f"Scope: {scope}")
            if files:
                header_lines.append(f"Files: {files}")
            add(self._section(
                "task-directive",
                "Primary directive",
                "\n".join(header_lines),
                source="composed",
                category="task",
            ))

        # --- Builder-discipline guardrails (NORMAL and RICH) ---
        # Inspect-first + smallest-clean-path + stop-on-mismatch are added as a
        # dedicated section rather than being patched into every role template,
        # so the rule is uniform across builder/reviewer/verifier/planner runs.
        if context.mode != PromptCompositionMode.COMPACT:
            add(self._section(
                "discipline-guardrails",
                "Discipline guardrails",
                _DISCIPLINE_GUARDRAILS,
                source="composed",
                category="discipline",
            ))

        # --- Template sections (filtered by mode) ---
        allowed_categories = _MODE_CATEGORIES.get(context.mode, _MODE_CATEGORIES[PromptCompositionMode.NORMAL])
        for template in templates:
            if template.category.value not in allowed_categories:
                continue
            add(
                self._section(
                    template.id,
                    template.name,
                    self._interpolate(template.body, variables, unresolved),
                    source="template",
                    category=template.category.value,
                    role=template.role.value if template.role else None,
                    variant=template.variant,
                    audience=template.audience,
                    composition_order=template.composition_order,
                )
            )

        # --- Dynamic context sections ---
        # NORMAL and RICH: include workflow/proposal/startup context
        include_context = context.mode in (PromptCompositionMode.NORMAL, PromptCompositionMode.RICH)

        if include_context and context.workflow_context:
            # Filter out keys already rendered in the task block
            ctx_filtered = {k: v for k, v in context.workflow_context.items()
                           if k not in ("task", "scope", "files_affected", "workspace_root")}
            if ctx_filtered:
                add(self._section(
                    "workflow-context", "Workflow context",
                    self._interpolate(self._render_dict(ctx_filtered), variables, unresolved),
                    source="context", category="workflow_context",
                ))

        if include_context and context.proposal_context:
            add(self._section(
                "proposal-context", "Proposal context",
                self._interpolate(self._render_dict(context.proposal_context), variables, unresolved),
                source="context", category="proposal_context",
            ))

        if include_context and context.startup_context:
            add(self._section(
                "startup-context", "Startup context",
                self._interpolate(self._render_dict(context.startup_context), variables, unresolved),
                source="context", category="startup_context",
            ))

        # Parallel rules context (NORMAL and RICH, only when requested)
        if context.parallel_session_rules and context.mode != PromptCompositionMode.COMPACT:
            add(self._section(
                "parallel-session-rules", "Parallel session rules",
                self._interpolate(self._render_list(context.parallel_session_rules), variables, unresolved),
                source="context", category="parallel_rule",
            ))

        # Safety constraints from context (all modes)
        if context.safety_constraints:
            add(self._section(
                "safety-constraints", "Safety constraints",
                self._interpolate(self._render_list(context.safety_constraints), variables, unresolved),
                source="context", category="safety",
            ))

        # Verification steps (NORMAL and RICH)
        verification_items = list(context.verification_steps or [])
        if not verification_items and context.mode != PromptCompositionMode.COMPACT:
            verification_items = list(_DEFAULT_VERIFICATION_STEPS)
        if verification_items and context.mode != PromptCompositionMode.COMPACT:
            add(self._section(
                "verification-steps", "Verification steps",
                self._interpolate(self._render_list(verification_items), variables, unresolved),
                source="context", category="verification",
            ))

        # Return format from context (NORMAL and RICH).
        # The single-instance dedupe on category="return_format" ensures this
        # block replaces (not duplicates) any template-sourced return_format.
        if context.expected_return_format and context.mode != PromptCompositionMode.COMPACT:
            add(self._section(
                "expected-return-format", "Expected return format",
                self._interpolate(self._render_list(context.expected_return_format), variables, unresolved),
                source="context", category="return_format",
            ))

        # Extra sections (RICH only)
        if context.extra_sections and context.mode == PromptCompositionMode.RICH:
            add(self._section(
                "extra-sections", "Extra sections",
                self._interpolate(self._render_list(context.extra_sections), variables, unresolved),
                source="context", category="extra",
            ))

        # --- Unresolved-placeholder guardrail ---
        # Scrub any residual {{var}} tokens from rendered section bodies so the
        # outgoing prompt never silently leaks them. The operator still sees
        # the list in metadata.unresolved_variables and, in NORMAL/RICH, in a
        # dedicated header section so the sub-agent is told to stop rather
        # than guess.
        deduped_unresolved = sorted(set(unresolved))
        if deduped_unresolved:
            for section in sections:
                section["body"] = _VAR_PATTERN.sub(
                    lambda m: f"<<unresolved:{m.group(1)}>>", section["body"]
                )
            if context.mode != PromptCompositionMode.COMPACT:
                notice_body = (
                    "The following variables were not supplied by the operator:\n"
                    + "\n".join(f"- {name}" for name in deduped_unresolved)
                    + "\n\nIf any of these block correct execution, stop and ask "
                    "the operator to resolve them before editing code."
                )
                # Prepend so the sub-agent sees it before the directive.
                sections.insert(0, self._section(
                    "unresolved-variables", "Unresolved variables",
                    notice_body,
                    source="composed", category="unresolved",
                ))

        prompt_text = "\n\n".join(f"{section['name']}:\n{section['body']}" for section in sections)
        return GeneratedPromptPayload(
            role=role,
            mode=context.mode,
            template_ids=[template.id for template in templates],
            sections=sections,
            prompt_text=prompt_text,
            token_estimate=self._estimate_tokens(prompt_text),
            metadata={
                "workflow_id": context.workflow_id,
                "proposal_id": context.proposal_id,
                "included_categories": sorted(
                    {str(section.get("category", "")) for section in sections if section.get("category")}
                ),
                "included_roles": [role.value],
                "composition_mode": context.mode.value,
                "section_count": len(sections),
                "unresolved_variables": deduped_unresolved,
            },
        )

    def compose_preset(
        self,
        preset_name: PromptPresetName,
        variables: dict[str, str] | None = None,
        task: str = "",
    ) -> GeneratedPromptPayload:
        preset = PRESETS.get(preset_name)
        if preset is None:
            raise ValueError(f"Unknown preset: {preset_name}")
        merged_vars = {**preset.default_variables, **(variables or {})}
        context = PromptContext(
            task=task or preset.default_task,
            mode=preset.mode,
            workflow_context=merged_vars,
            parallel_session_rules=(
                ["Active — see parallel-default template for rules"]
                if preset.include_parallel_rules
                else []
            ),
        )
        payload = self.compose(role=preset.role, context=context)
        payload.metadata["preset"] = preset_name.value
        return payload

    def _select_templates(self, *, role: PromptRole, context: PromptContext) -> list[PromptTemplate]:
        variant = context.template_filters.get("variant") if context.template_filters else None
        templates: list[PromptTemplate] = []
        templates.extend(self.store.startup_templates(variant=variant))
        templates.extend(self.store.filter(category=PromptTemplateCategory.ROLE, role=role, variant=variant))
        if context.parallel_session_rules:
            templates.extend(self.store.parallel_rules(variant=variant))
        templates.extend(self.store.filter(category=PromptTemplateCategory.SAFETY, variant=variant))
        templates.extend(self.store.filter(category=PromptTemplateCategory.RETURN_FORMAT, variant=variant))
        return sorted(templates, key=lambda item: (item.composition_order, item.name, item.id))

    def _interpolate(
        self, text: str, variables: dict[str, str], unresolved: list[str] | None = None,
    ) -> str:
        def replacer(match: re.Match) -> str:
            key = match.group(1)
            if key in variables:
                return variables[key]
            if unresolved is not None:
                unresolved.append(key)
            return match.group(0)
        return _VAR_PATTERN.sub(replacer, text)

    @staticmethod
    def _estimate_tokens(text: str) -> int:
        return len(text) // 4

    def _collect_variables(self, context: PromptContext) -> dict[str, str]:
        merged: dict[str, str] = {}
        for source in (context.startup_context, context.workflow_context, context.proposal_context):
            for key, value in source.items():
                merged[key] = str(value)
        if context.task:
            merged["task"] = context.task
        return merged

    def _section(self, section_id: str, name: str, body: str, **metadata) -> dict:
        return {"id": section_id, "name": name, "body": body, **metadata}

    def _render_dict(self, value: dict) -> str:
        return "\n".join(f"- {key}: {item}" for key, item in value.items())

    def _render_list(self, value: list[str]) -> str:
        return "\n".join(f"- {item}" for item in value) if value else ""
