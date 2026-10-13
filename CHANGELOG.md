# Changelog

All notable changes to ORC are documented in this file.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- Scheduled jobs: a local cron scheduler built on APScheduler's `BackgroundScheduler`. Jobs use 5-field cron expressions (minute hour day month dow) in local time. Each job runs one whitelisted action type:
  - `fire_saved_prompt`: renders a saved prompt template's body and records it in the job's run log, as a manual hand-off cue.
  - `record_memory_note`: appends a dated note to a canonical memory section through `MemoryStore.apply_patches`.
  - `run_workflow_step`: advances the named workflow by one step through the workflow engine.
- Each scheduled run is recorded in a per-job run log. A job's `last_status` is `ok` or `error`.
- Full-text search index: a SQLite FTS5 index over workflows and other local records. On startup, if the index is empty, it is rebuilt from the JSON files under `data/`.

### Scheduler network behaviour
- No scheduled action runs a shell command.
- The `run_workflow_step` action advances a workflow through the configured LLM providers. It can make network calls when any provider is configured, including a router base URL with no key. A role using `claude_code_local` starts a local `claude` CLI process, which talks to Anthropic.
- With no provider configured, `run_workflow_step` uses the mock provider, which makes no external API call.

### Security
- LAN auth hardening. When `LOCAL_NETWORK_ONLY` is on, requests from non-private, non-loopback addresses are rejected with 403. Loopback requests are allowed without credentials. Requests from other LAN devices require HTTP Basic Auth. If `LOCAL_ACCESS_PASSWORD` is unset, the console returns 503 to LAN clients instead of allowing them in. Credentials are compared in constant time.

## [0.1.0] — 2026-04-22

Initial public release.

### Added
- FastAPI backend for workflow orchestration, provider routing, builder jobs, session management, and idea threads.
- Role-based provider abstraction for reviewer / planner / builder (OpenAI, Anthropic, Gemini, Claude Code local).
- Local-first JSON storage under `data/` for workflows, sessions, ideas, saved prompts, and canonical memory.
- Typed canonical memory with 7 sections (vision, systems, status, decisions, preferences, known_failures, roadmap).
- Experiment journal (`research_manager/experiment_journal.py` integration point) for parameter-change auditing.
- Read-only web UI at `/` with workflow, dispatch queue, session, approvals, experiment, and provider views.
- `orc` CLI with green ANSI-art banner and three subcommands: `start`, `mcp`, `version`.
- MCP server surface with 20 tools for memory, saved prompts, ideas, workflows, sessions, and status (usable from Claude Desktop / Claude Code).
- LAN access mode with Basic Auth for non-localhost browsers.
- Mock provider fallback when provider API keys are unavailable.
- Deterministic next-step planning on ingested worker results.
- State reconciler (`reconcile_state --apply`) for detecting and repairing stale state.
- Apache 2.0 license, NOTICE file, airtight `.gitignore` preventing local state from shipping.
- Logo + README with install/run/architecture documentation.
- `ROADMAP.md` outlining V2 near-term and V3 (chat-first builder for non-coders) directions.
- `OPERATOR.md` with end-to-end operator flow and known limitations.

### Fixed
- `StateStore.list_ids()` previously globbed all `*.json` in the state directory, causing `project_memory.json` and `assistant_threads.json` to be mistakenly treated as workflow state. Added a 12-character hex filter matching the ID format produced by `StateStore.create()`.

[Unreleased]: https://github.com/manojkothapalli1004/orc/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/manojkothapalli1004/orc/releases/tag/v0.1.0
