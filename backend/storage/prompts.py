"""Local persistence for Prompt OS templates."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from backend.config import settings
from backend.models.prompt import PromptRole, PromptTemplate, PromptTemplateCategory


_DEFAULT_TEMPLATES = [
    PromptTemplate(
        id="startup-default",
        name="Default startup",
        category=PromptTemplateCategory.STARTUP,
        composition_order=10,
        audience="shared",
        variables=["scope", "workspace_root"],
        body=(
            "You are working inside {{workspace_root}}.\n"
            "Scope: {{scope}}\n\n"
            "Rules:\n"
            "- Read CONTEXT.md, STATUS.md, HANDOFF.md, and WORKLOG.md first.\n"
            "- Assume saved constraints remain active.\n"
            "- Resume from the last safe point or current stage.\n"
            "- Before any real work, update HANDOFF.md and WORKLOG.md first.\n"
            "- After each batch, update STATUS.md, HANDOFF.md, and WORKLOG.md.\n"
            "- Use just-in-time reads for anything else.\n"
            "- Output only requested scope.\n"
            "- Prefer diffs for edits.\n"
            "- Stop when done."
        ),
    ),
    PromptTemplate(
        id="role-builder",
        name="Builder role",
        category=PromptTemplateCategory.ROLE,
        role=PromptRole.BUILDER,
        composition_order=20,
        audience="role",
        variables=["scope", "files_affected"],
        body=(
            "Role: Builder\n"
            "Scope: {{scope}}\n"
            "Files: {{files_affected}}\n\n"
            "Rules:\n"
            "- Inspect current code/context first. Confirm the files and symbols\n"
            "  you plan to edit actually exist before writing anything.\n"
            "- Take the smallest clean implementation path. No refactors,\n"
            "  renames, or abstractions beyond the approved scope.\n"
            "- Stop on mismatch: if the actual structure differs from what this\n"
            "  prompt assumes, stop and report the mismatch instead of\n"
            "  patching around it.\n"
            "- Do not expand requirements. Do not add features beyond scope.\n"
            "- Do not add live execution changes, screen automation, or\n"
            "  destructive behavior.\n"
            "- Prefer editing existing files over creating new ones.\n"
            "- Prefer diffs for edits to existing files.\n"
            "- Do not add error handling for scenarios that cannot happen.\n"
            "- Do not create helpers or abstractions for one-time operations.\n"
            "- Verify each edited file compiles/parses after editing, and state\n"
            "  which verifications actually ran vs. were skipped.\n"
            "- After each batch, update STATUS.md, HANDOFF.md, and WORKLOG.md."
        ),
    ),
    PromptTemplate(
        id="role-reviewer",
        name="Reviewer role",
        category=PromptTemplateCategory.ROLE,
        role=PromptRole.REVIEWER,
        composition_order=20,
        audience="role",
        variables=["scope", "files_affected"],
        body=(
            "Role: Reviewer\n"
            "Scope: {{scope}}\n"
            "Files: {{files_affected}}\n\n"
            "Inspection discipline:\n"
            "- Read the actual files first. Do not review from description alone.\n"
            "- If the implementation does not match what the prompt claims was\n"
            "  built, stop and report the mismatch — do not review the gap away.\n\n"
            "Review checklist:\n"
            "1. Correctness — does the code do what it claims?\n"
            "2. Coherence — do modules interact correctly?\n"
            "3. Safety — no live execution, no shell injection, no scope violations?\n"
            "4. Typing — are all public interfaces fully typed?\n"
            "5. Blocker classification — CRITICAL / HIGH / MEDIUM / LOW.\n"
            "6. Scope — does it stay within the approved boundaries?\n\n"
            "Return format:\n"
            "- List of issues, each with: file, line, severity, description.\n"
            "- Summary: total issues by severity, overall assessment\n"
            "  (PASS / PASS_WITH_ISSUES / FAIL).\n"
            "- Do not suggest improvements outside the reviewed scope."
        ),
    ),
    PromptTemplate(
        id="role-verifier",
        name="Verifier role",
        category=PromptTemplateCategory.ROLE,
        role=PromptRole.VERIFIER,
        composition_order=20,
        audience="role",
        variables=["scope", "files_affected"],
        body=(
            "Role: Verifier\n"
            "Scope: {{scope}}\n"
            "Files: {{files_affected}}\n\n"
            "Verification approach:\n"
            "1. Syntax check all changed files (py_compile, go build, etc.).\n"
            "2. Run existing tests if any cover the changed scope.\n"
            "3. Smoke-test key entry points.\n"
            "4. Distinguish verified facts from assumptions.\n\n"
            "What counts as verified:\n"
            "- File compiles/parses without errors.\n"
            "- Tests pass (list which tests).\n"
            "- Entry point runs without crash.\n\n"
            "Return format:\n"
            "- VERIFIED: list of verified items with evidence.\n"
            "- UNVERIFIED: list of items not yet checked and why.\n"
            "- FAILED: list of items that failed with error output.\n"
            "- Overall: PASS / PARTIAL / FAIL."
        ),
    ),
    PromptTemplate(
        id="role-planner",
        name="Planner role",
        category=PromptTemplateCategory.ROLE,
        role=PromptRole.PLANNER,
        composition_order=20,
        audience="role",
        variables=["scope", "workspace_root"],
        body=(
            "Role: Planner\n"
            "Scope: {{scope}}\n"
            "Workspace: {{workspace_root}}\n\n"
            "Planning discipline:\n"
            "1. Read existing code and constraints before proposing changes.\n"
            "2. Identify all files that will be touched.\n"
            "3. List dependencies between changes (what must be done first).\n"
            "4. Bound the scope — state what is explicitly excluded.\n"
            "5. Define verification steps for each batch.\n"
            "6. Estimate token cost (compact vs rich mode).\n\n"
            "Return format:\n"
            "- Context: why this change is needed.\n"
            "- Files changed: list with summary per file.\n"
            "- Batches: ordered groups of changes with dependencies.\n"
            "- Verification: how to confirm each batch works.\n"
            "- Exclusions: what is explicitly out of scope."
        ),
    ),
    PromptTemplate(
        id="parallel-default",
        name="Parallel session rule",
        category=PromptTemplateCategory.PARALLEL_RULE,
        composition_order=30,
        audience="parallel_session",
        variables=["scope"],
        body=(
            "PARALLEL-SESSION RULE:\n"
            "You are working in parallel with other builder sessions.\n"
            "Your scope: {{scope}}\n\n"
            "- Do not overwrite unrelated files.\n"
            "- Minimize edits to shared files (README, package manifests, root config, app entrypoints).\n"
            "- If a shared file must be changed, keep the change minimal and explicitly mention it in the result.\n"
            "- Do not modify files outside your assigned scope.\n"
            "- If you discover a conflict with another session's work, stop and report it."
        ),
    ),
    PromptTemplate(
        id="safety-default",
        name="Safety model",
        category=PromptTemplateCategory.SAFETY,
        composition_order=80,
        audience="shared",
        variables=[],
        body=(
            "SAFETY CONSTRAINTS (hard boundaries):\n"
            "- No screen automation.\n"
            "- No arbitrary shell execution.\n"
            "- No live bot control or dispatch.\n"
            "- No live trading or order placement.\n"
            "- No edits outside the assigned scope directory.\n"
            "- No deletion of state files, logs, or configuration.\n"
            "- No force-push, reset --hard, or other destructive git operations.\n"
            "- No secrets or credentials in committed files."
        ),
    ),
    PromptTemplate(
        id="return-format-default",
        name="Return format",
        category=PromptTemplateCategory.RETURN_FORMAT,
        composition_order=90,
        audience="shared",
        variables=[],
        body=(
            "EXPECTED RETURN FORMAT:\n"
            "Return only the requested scope. Structure your response as:\n"
            "1. Files changed — list with one-line summary per file.\n"
            "2. What was done — concise description of changes.\n"
            "3. What was verified — compilation, tests, smoke checks.\n"
            "4. What is next — immediate next step if any.\n\n"
            "- Prefer diffs for edits.\n"
            "- No context recap unless asked.\n"
            "- No preamble.\n"
            "- Stop when done."
        ),
    ),
]


class PromptTemplateStore:
    def __init__(self, root_dir: Path | None = None) -> None:
        self._dir = root_dir or settings.prompt_template_dir
        self._dir.mkdir(parents=True, exist_ok=True)
        self._seed_defaults()

    def _path(self, template_id: str) -> Path:
        return self._dir / f"{template_id}.json"

    def _seed_defaults(self) -> None:
        for template in _DEFAULT_TEMPLATES:
            path = self._path(template.id)
            if not path.exists():
                path.write_text(template.model_dump_json(indent=2))
                continue
            existing = PromptTemplate.model_validate_json(path.read_text())
            merged = existing.model_copy(
                update={
                    "body": template.body,
                    "variables": template.variables,
                    "variant": template.variant,
                    "composition_order": template.composition_order,
                    "audience": template.audience,
                    "role": template.role or existing.role,
                    "category": template.category,
                }
            )
            if merged != existing:
                path.write_text(merged.model_dump_json(indent=2))

    def list(self) -> list[PromptTemplate]:
        return [PromptTemplate.model_validate_json(path.read_text()) for path in sorted(self._dir.glob("*.json"))]

    def load(self, template_id: str) -> PromptTemplate | None:
        path = self._path(template_id)
        if not path.exists():
            return None
        return PromptTemplate.model_validate_json(path.read_text())

    def save(self, template: PromptTemplate) -> PromptTemplate:
        template.updated_at = datetime.now(timezone.utc)
        tmp = self._path(template.id).with_suffix('.tmp')
        tmp.write_text(template.model_dump_json(indent=2))
        tmp.rename(self._path(template.id))
        return template

    def by_category(self, category: PromptTemplateCategory) -> list[PromptTemplate]:
        return sorted(
            [template for template in self.list() if template.category == category],
            key=lambda item: (item.composition_order, item.name, item.id),
        )

    def by_role(self, role: PromptRole) -> list[PromptTemplate]:
        return sorted(
            [template for template in self.list() if template.role == role],
            key=lambda item: (item.composition_order, item.name, item.id),
        )

    def get(self, template_id: str) -> PromptTemplate | None:
        return self.load(template_id)

    def filter(
        self,
        *,
        category: PromptTemplateCategory | None = None,
        role: PromptRole | None = None,
        variant: str | None = None,
    ) -> list[PromptTemplate]:
        templates = self.list()
        if category is not None:
            templates = [template for template in templates if template.category == category]
        if role is not None:
            templates = [template for template in templates if template.role == role]
        if variant is not None:
            templates = [template for template in templates if template.variant == variant]
        return sorted(templates, key=lambda item: (item.composition_order, item.name, item.id))

    def startup_templates(self, *, variant: str | None = None) -> list[PromptTemplate]:
        return self.filter(category=PromptTemplateCategory.STARTUP, variant=variant)

    def parallel_rules(self, *, variant: str | None = None) -> list[PromptTemplate]:
        return self.filter(category=PromptTemplateCategory.PARALLEL_RULE, variant=variant)
