from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from backend.config import settings
from backend.models.core import ProviderRole
from backend.models.memory import AssistantChatRequest, AssistantChatResponse, ProjectMemory
from backend.providers.factory import ProviderFactory
from backend.providers.base import ProviderRequest


# Priority order when ASSISTANT_PROVIDER=auto
_AUTO_PRIORITY = ["openai", "anthropic", "claude_code_local"]


@dataclass
class ProviderResolution:
    """Result of resolving which provider the assistant will use."""
    provider_name: str       # "openai" | "anthropic" | "claude_code_local" | "mock"
    model: str
    is_mock: bool
    reason: str              # why this provider was chosen


class AssistantService:
    """Multi-provider assistant for discussing ideas, planning, and drafting prompts."""

    def __init__(self) -> None:
        self.factory = ProviderFactory()
        self._last_resolution: ProviderResolution | None = None

    @property
    def last_resolution(self) -> ProviderResolution | None:
        return self._last_resolution

    def resolve_provider(self) -> tuple[Any, ProviderResolution]:
        """Pick a provider based on ASSISTANT_PROVIDER setting.

        Returns (provider_instance, resolution_info).
        """
        mode = (settings.assistant_provider or "auto").lower().strip()

        if mode == "mock":
            return self._build_mock("Operator requested mock mode")

        if mode in ("openai", "anthropic", "claude_code_local"):
            return self._try_specific(mode)

        # mode == "auto": try each in priority order
        for name in _AUTO_PRIORITY:
            provider, res = self._try_specific(name)
            if not res.is_mock:
                return provider, res
        # All unavailable — fall back to mock
        return self._build_mock(
            f"Auto mode: no live provider available (tried {', '.join(_AUTO_PRIORITY)})"
        )

    def _try_specific(self, name: str) -> tuple[Any, ProviderResolution]:
        """Try to build a specific provider; return mock with reason if unavailable."""
        if name == "openai":
            model = settings.assistant_model_openai
        elif name == "anthropic":
            model = settings.assistant_model_anthropic
        elif name == "claude_code_local":
            model = settings.assistant_model_claude_code_local
        else:
            return self._build_mock(f"Unknown provider: {name}")

        role_config = ProviderRole(role="assistant", provider=name, model=model)
        provider = self.factory.build(role_config)

        if getattr(provider, "provider_name", "").startswith("mock"):
            reason = self.factory.last_unavailability_reason or f"{name} requested but unavailable"
            return provider, ProviderResolution(
                provider_name="mock",
                model=model,
                is_mock=True,
                reason=f"{name} fell back to mock — {reason}",
            )

        return provider, ProviderResolution(
            provider_name=name,
            model=model,
            is_mock=False,
            reason=f"{name} provider active",
        )

    def _build_mock(self, reason: str) -> tuple[Any, ProviderResolution]:
        role_config = ProviderRole(role="assistant", provider="mock", model="mock-v1")
        provider = self.factory.build(role_config)
        return provider, ProviderResolution(
            provider_name="mock", model="mock-v1", is_mock=True, reason=reason,
        )

    async def chat(self, request: AssistantChatRequest, memory: ProjectMemory) -> AssistantChatResponse:
        provider, resolution = self.resolve_provider()
        self._last_resolution = resolution

        sys_prompt = self._build_system_prompt(memory)
        if request.system_prompt_override:
            sys_prompt = request.system_prompt_override

        prompt_parts: list[str] = []
        if request.history:
            prompt_parts.append("--- CHAT HISTORY ---")
            for msg in request.history[-10:]:
                role = "Operator" if msg.role == "user" else "Assistant"
                prompt_parts.append(f"{role}: {msg.content}\n")
            prompt_parts.append("--- END CHAT HISTORY ---\n")

        prompt_parts.append("Operator: " + request.message)

        req = ProviderRequest(
            role="assistant",
            prompt="\n".join(prompt_parts),
            system_prompt=sys_prompt,
        )

        resp = await provider.complete(req)

        return AssistantChatResponse(
            reply=resp.content,
            usage={
                "input": resp.token_usage.input_tokens,
                "output": resp.token_usage.output_tokens,
            },
        )

    def _build_system_prompt(self, memory: ProjectMemory) -> str:
        canonical = self._render_canonical_memory(memory)
        return f"""You are the Project Orchestrator Assistant — a disciplined control-tower companion for a solo operator running this project.

====================  CANONICAL PROJECT MEMORY  ====================
This block is the primary, authoritative source of project state. It
outranks anything in chat history. If chat history and canonical memory
disagree about project-operational facts (what exists, what is running,
what was decided), trust canonical memory and say so.

{canonical}
====================================================================

WHEN ANSWERING PROJECT-OPERATIONAL QUESTIONS
(what is running, what was decided, what is next, what is the status):
- Ground the answer in the canonical memory above.
- Quote the section you are drawing from ("per Current Systems…",
  "per Decisions…").
- If the question cannot be answered from canonical memory, say so
  explicitly instead of guessing. Suggest which section should be
  updated so a future answer can.
- If chat history conflicts with canonical memory, flag the conflict
  and prefer canonical memory.

WHEN GENERATING A PROMPT for a builder / reviewer / verifier / planner
sub-agent, the output MUST include every one of these elements:
1. Role line and scope (directory or files).
2. "Inspect current code/context first" instruction.
3. Smallest clean implementation path — no refactors or cleanups
   outside scope.
4. Stop-on-mismatch clause: if the actual structure differs from what
   the prompt assumes, stop and report instead of patching around it.
5. Exact verification steps (syntax check, tests to run, endpoints to
   hit). Use canonical memory to pick the right commands.
6. Exact return format — numbered, specific, bounded.
7. Hard safety constraints (no live trading, no shell automation, no
   browser automation, scope boundaries).

NEVER in a generated prompt:
- Duplicate the same section under different headings.
- Emit two conflicting "return format" blocks.
- Leave unresolved placeholders like {{scope}} or {{files_affected}}
  in the output. If a placeholder has no value, either ask the
  operator for it or drop that line.
- Invent files, endpoints, or flags you cannot point to in canonical
  memory or the user's message.

STYLE
- Concise, direct, no conversational fluff.
- No preamble, no recap, no "happy to help".
- Prefer bullet points and exact filenames.
- If asked to distill a session into a memory update, return the
  update as a structured block with `section:` headers (one of:
  vision, systems, status, decisions, preferences, known_failures,
  roadmap) and tight bullets — never raw chat paste.
"""

    @staticmethod
    def _render_canonical_memory(memory: ProjectMemory) -> str:
        labelled = [
            ("VISION", memory.vision),
            ("SYSTEMS", memory.systems),
            ("STATUS", memory.status),
            ("DECISIONS", memory.decisions),
            ("PREFERENCES", memory.preferences),
            ("KNOWN_FAILURES", memory.known_failures),
            ("ROADMAP", memory.roadmap),
        ]
        blocks = []
        for name, body in labelled:
            text = (body or "").strip()
            if not text:
                blocks.append(f"[{name}]\n(empty — ask operator to populate before relying on this section)")
            else:
                blocks.append(f"[{name}]\n{text}")
        return "\n\n".join(blocks)
