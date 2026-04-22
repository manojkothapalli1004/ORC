# Roadmap

ORC ships today as a local-first operator console for bounded AI work. The V1 focus is on orchestration plumbing — role-based routing, workflow state, experiment journaling, prompt templates, and a read-only UI.

This document is the forward view.

## V1 — shipped

- FastAPI backend + static UI
- reviewer / planner / builder role abstraction
- workflow orchestration engine with idea-to-proposal lifecycle
- builder-job bridge with inbox / outbox / records
- session manager with assignment guard and result ingestion
- deterministic next-step planning
- experiment journal for parameter-change tracking
- local provider registry (OpenAI / Anthropic / Gemini / Claude Code local)
- MCP server surface (`orc mcp`) for Claude Desktop / Claude Code integration
- LAN access mode with Basic Auth
- mock-safe behavior when provider keys are unavailable

## V2 — near-term

Items that strengthen V1 without changing the shape of the product:

- targeted tests for storage helpers, route handlers, workflow modes
- example screenshots / short captures for GitHub presentation
- richer provider + router configuration docs with known-good examples
- clearer worker-operation docs for manual-safe vs provider-backed execution
- workflow result disaggregation when multiple experiments run concurrently
- export / import of canonical memory between installations
- GitHub Actions CI (lint, type-check, run the single existing test)

## V3 and beyond — exploratory direction

A potential larger direction: turn ORC into a **chat-first builder for people who don't write code**.

Rough shape:
- single primary surface: conversation with the assistant
- at each important milestone, the assistant proposes what to build next and the user picks a direction
- the assistant auto-writes build prompts, review prompts, verification prompts, and fix-on-error prompts — all reusing ORC's existing prompt + workflow engine
- a live preview pane for what is being built
- sandboxed code execution for generated code
- one-click deploy integrations (Vercel / Netlify / Cloudflare Pages)
- template library so blank starts have a chance
- project memory per build, not just per session

What's honest about this direction:
- the orchestration backend for this already exists in ORC (role routing, approval gates, experiment journal, prompt templates)
- the **hard** parts are the new ones: sandboxed execution, live preview, deployment pipeline, secret management for user-supplied keys, error-to-fix loop
- the space is competitive (Bolt.new, Lovable, v0, Replit Agent). Differentiation would need to come from orchestration intelligence, not from UI alone.
- this is a multi-month rebuild, not a V1 tweak

This direction is documented here so future contributors can understand where the project may head. It is not the V1 scope and is not implied by the current codebase.

## Non-goals

ORC will not:

- automate arbitrary shell execution against your machine
- apply live destructive changes to external systems
- expose public internet endpoints by default
- bundle OCR / screen automation
- claim to replace engineering judgement on production systems
