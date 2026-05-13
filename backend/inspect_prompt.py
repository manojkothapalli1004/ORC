"""CLI inspector for Prompt OS — view composed prompts locally.

Usage:
    python3 -m backend.inspect_prompt --role builder --mode compact
    python3 -m backend.inspect_prompt --preset builder_parallel --format json
    python3 -m backend.inspect_prompt --role reviewer --mode rich --var scope=orchestrator/ --var workspace_root=<your-workspace-root>
    python3 -m backend.inspect_prompt --list-presets
    python3 -m backend.inspect_prompt --list-templates
"""

from __future__ import annotations

import argparse
import sys

from backend.models.prompt import (
    PromptCompositionMode,
    PromptPresetName,
    PromptRole,
)
from backend.prompts import PRESETS, PromptComposer
from backend.models.prompt import PromptContext
from backend.storage.prompts import PromptTemplateStore


def _parse_var(raw: str) -> tuple[str, str]:
    if "=" not in raw:
        print(f"Invalid --var format: {raw!r} (expected key=value)", file=sys.stderr)
        sys.exit(1)
    key, _, value = raw.partition("=")
    return key.strip(), value.strip()


def main() -> None:
    parser = argparse.ArgumentParser(description="Prompt OS inspector")
    parser.add_argument("--role", choices=[r.value for r in PromptRole])
    parser.add_argument("--mode", choices=[m.value for m in PromptCompositionMode], default="normal")
    parser.add_argument("--preset", choices=[p.value for p in PromptPresetName])
    parser.add_argument("--task", default="", help="primary task/instruction text")
    parser.add_argument("--var", action="append", default=[], help="key=value variable pairs")
    parser.add_argument("--format", choices=["text", "json"], default="text", dest="output_format")
    parser.add_argument("--list-presets", action="store_true")
    parser.add_argument("--list-templates", action="store_true")

    args = parser.parse_args()
    store = PromptTemplateStore()
    composer = PromptComposer(store)

    if args.list_presets:
        for preset in PRESETS.values():
            print(f"  {preset.name.value:25s}  {preset.role.value:10s}  {preset.mode.value:8s}  {preset.description}")
        return

    if args.list_templates:
        for template in store.list():
            role_str = template.role.value if template.role else "-"
            vars_str = ", ".join(template.variables) if template.variables else "-"
            print(f"  {template.id:30s}  {template.category.value:15s}  {role_str:10s}  vars=[{vars_str}]")
        return

    variables = dict(_parse_var(v) for v in args.var)

    if args.preset:
        preset_name = PromptPresetName(args.preset)
        payload = composer.compose_preset(preset_name, variables=variables, task=args.task)
    elif args.role:
        role = PromptRole(args.role)
        mode = PromptCompositionMode(args.mode)
        context = PromptContext(
            task=args.task,
            mode=mode,
            workflow_context=variables,
        )
        payload = composer.compose(role=role, context=context)
    else:
        parser.print_help()
        sys.exit(1)

    if args.output_format == "json":
        print(payload.model_dump_json(indent=2))
    else:
        print(payload.prompt_text)
        print(f"\n--- token_estimate: {payload.token_estimate} ---")
        if payload.metadata.get("unresolved_variables"):
            print(f"--- unresolved: {payload.metadata['unresolved_variables']} ---")


if __name__ == "__main__":
    main()
