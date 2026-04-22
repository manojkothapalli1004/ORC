<p align="center">
  <img src="https://raw.githubusercontent.com/manojkothapalli1004/orc/main/docs/logo.png" alt="ORC logo" width="160" />
</p>

<h1 align="center">ORC</h1>

<p align="center">
  <em>local-first operator console for bounded AI work</em>
</p>

<p align="center">
  <a href="LICENSE"><img alt="License: Apache 2.0" src="https://img.shields.io/badge/license-Apache%202.0-blue.svg"></a>
  <img alt="Python 3.12+" src="https://img.shields.io/badge/python-3.12%2B-blue.svg">
  <img alt="Status: V1" src="https://img.shields.io/badge/status-V1-green.svg">
</p>

---

**ORC** is a local-first orchestration console for planning, reviewing, dispatching, and tracking bounded AI work.

It is designed as a serious operator console, not an autonomous agent runtime. V1 focuses on local visibility, bounded workflow orchestration, safe provider integration, and structured handoff state.

## Install

```bash
git clone git@github.com:manojkothapalli1004/orc.git
cd orc
uv sync
uv pip install -e .      # registers the `ORC` CLI
```

## Run

```bash
orc start                # launch HTTP server + UI on http://127.0.0.1:8100
orc mcp                  # launch MCP server (stdio) for Claude Desktop / Claude Code
orc --help
```

On first launch the UI is usable in mock-safe mode with no provider keys. Add keys to `.env` (copy from `.env.example`) to enable live reviewer / planner / builder calls.

## Principles

- local-first state and storage
- explicit safety boundaries
- mock-safe behavior when providers or keys are unavailable
- no arbitrary shell execution in the orchestrator itself
- no live destructive apply path
- understandable architecture and predictable operator flows

## What this V1 includes

- FastAPI backend for orchestration state, builder job queue visibility, idea threads, result ingestion, and session tracking
- read-only web UI for workflows, dispatch queue, sessions, ideas, approvals, experiments, and provider state
- typed provider abstraction with role-based routing for reviewer / planner / builder
- local builder job bridge with inbox / outbox / records / lock files
- local session manager for Claude / Antigravity work coordination
- deterministic next-step planning on ingested worker results
- LAN-friendly local access mode with Basic Auth for non-localhost clients

## What this V1 does not do

- no public internet tunnel or hosted deployment path
- no screen or OCR automation
- no arbitrary shell command execution
- no trading runtime integration
- no live destructive apply path

## Architecture

```text
Phone / Desktop Browser
        │
        ▼
┌─────────────────────────────────────────────┐
│ Control Tower UI                            │
│ - workflows                                 │
│ - dispatch queue                            │
│ - sessions                                  │
│ - approvals / experiments / providers       │
└─────────────────────┬───────────────────────┘
                      │ REST + static files
                      ▼
┌─────────────────────────────────────────────┐
│ FastAPI Backend                             │
│ - workflow engine                           │
│ - provider registry                         │
│ - builder job registry + handoff contracts  │
│ - session registry                          │
│ - idea thread store                         │
└─────────────────────┬───────────────────────┘
                      │ local JSON persistence
                      ▼
┌─────────────────────────────────────────────┐
│ Local storage                               │
│ - ./data/*                                  │
│ - ./bridge/builder_jobs/*                   │
│ - ./logs/*                                  │
└─────────────────────────────────────────────┘
```

The UI is a static frontend served by the FastAPI app. The backend owns orchestration state, provider routing, builder-job tracking, session state, and idea threads. Runtime data stays in local JSON-backed storage under `data/`, `bridge/`, and `logs/`.

## Safety model

The orchestrator is designed as a local approval and coordination surface, not an autonomous executor.

Current safety boundaries:
- builder jobs are tracked and persisted locally
- session manager tracks assignment and status only
- prompt delivery into external tools is not automated yet
- result capture is structured and local
- non-localhost browser access requires local-network access plus Basic Auth
- fallback and mock-safe behavior keep the UI usable when providers are unavailable
- the orchestrator does not expose arbitrary shell execution or live destructive controls

Operationally, this means V1 is suitable for review, planning, queue visibility, and bounded worker handoff state. It is not positioned as a system that directly applies risky changes to external runtimes.

## Current status

Implemented in the current V1:
- workflow orchestration demo loop
- provider / role mapping with direct and router-backed execution modes
- dispatch queue visibility for builder jobs
- local builder worker runtime for provider-backed job consumption
- session manager with assignment guard, prompt preview, delivery tracking, and result ingestion
- session handoff and restart prompt generation
- idea-thread intake / discussion / proposal-draft surface
- structured result ingestion with deterministic next-step suggestions
- reconciler for detecting and repairing stale state (`reconcile_state --apply`)
- local-only phone access on the same Wi-Fi

Intentionally deferred from V1:
- automated prompt delivery into Claude / Antigravity sessions (manual only)
- live apply / runtime-control actions
- hosted multi-user auth model
- automated worker supervision beyond local queue tracking
- full idea-to-proposal approval workflow in the UI

See [OPERATOR.md](OPERATOR.md) for the end-to-end operator flow, demo walkthrough, and known limitations.

## Roadmap

Near-term follow-ups that would strengthen the repo after V1:
- targeted tests for storage helpers, route handlers, and workflow mode resolution
- example screenshots or short demo captures for GitHub presentation
- tighter provider and router configuration docs with known-good examples
- clearer worker-operation docs for manual-safe vs provider-backed execution
- packaging cleanup if the project is later split into backend and UI subpackages

Treat this repository as a local orchestration foundation, not a finished autonomous system.

## V1 repo posture

This repository is reasonable to publish as a serious local-first V1 if you frame it accurately:
- backend- and storage-first functionality
- useful operator-facing UI, still evolving
- mock-safe defaults when providers are unavailable
- explicit non-goals around live automation and destructive execution

Publish it as an operator console and orchestration foundation rather than a finished autonomous system.

## Quick start

Requirements:
- Python 3.12+
- `uv`

```bash
cd orchestrator
cp .env.example .env
uv sync
uv run uvicorn backend.app:app --reload --host 127.0.0.1 --port 8100
```

Open:
- UI: `http://localhost:8100`
- API docs: `http://localhost:8100/api/docs`

If you do not set provider keys, the orchestrator remains usable in mock-safe mode for local development and UI exploration.

## Local phone access on the same Wi-Fi

Use this only on your local network.

1. Set `LOCAL_ACCESS_PASSWORD` in `.env`
2. Start the server bound to your LAN interface:

```bash
cd orchestrator
HOST=0.0.0.0 LOCAL_NETWORK_ONLY=true uv run uvicorn backend.app:app --reload --host 0.0.0.0 --port 8100
```

3. Open from your phone:

```text
http://<your-computer-lan-ip>:8100
```

For non-localhost clients, the browser will prompt for:
- username: `LOCAL_ACCESS_USERNAME` (default `operator`)
- password: `LOCAL_ACCESS_PASSWORD`

## Developer startup notes

Recommended first-run flow:
1. copy `.env.example` to `.env`
2. leave provider keys blank unless you want live provider calls
3. start the backend with `uvicorn`
4. open the dashboard and verify `/api/health`
5. create or run a demo workflow before testing queue-driven paths

Useful local endpoints:
- `GET /api/health`
- `GET /api/config`
- `GET /api/providers`
- `GET /api/assistant/provider-status`
- `GET /api/workflows`
- `GET /api/builder-jobs/queue`
- `GET /api/sessions`
- `GET /api/ideas`
- `GET /api/workflows/{workflow_id}/results`
- `GET /api/workflows/{workflow_id}/next-step`

Common local loop:
1. start backend/UI with uvicorn
2. open the dashboard in browser
3. create or inspect workflows and ideas
4. inspect dispatch queue and sessions
5. run the local worker when you want queued builder jobs to move forward
6. review builder job / session / next-step state through the UI or JSON endpoints

Worker example:

```bash
cd orchestrator
uv run python -c "from backend import builder_worker_entrypoint; print(builder_worker_entrypoint(limit=1))"
```

## Project structure

```text
orchestrator/
├── backend/
│   ├── api/                 # REST routes
│   ├── models/              # typed workflow, queue, handoff, idea, session models
│   ├── providers/           # provider abstractions and registry
│   ├── storage/             # local JSON stores
│   ├── workflows/           # orchestration engine
│   ├── app.py               # FastAPI app entry
│   ├── config.py            # environment-backed settings
│   ├── security.py          # local-network / Basic Auth middleware
│   └── workers.py           # local builder worker runtime layer
├── bridge/                  # builder-job handoff queue files
├── data/                    # local workflow / session / idea state
├── logs/                    # local logs
├── ui/                      # premium web UI
├── .env.example
├── .gitignore
├── pyproject.toml
└── uv.lock
```

## Configuration notes

Key configuration groups:
- provider keys: OpenAI / Anthropic / Gemini / router
- per-role provider mapping: reviewer / planner / builder
- per-role execution mode: `direct` or `router`
- assistant brain provider: `ASSISTANT_PROVIDER` (`auto` | `openai` | `anthropic` | `mock`) with model overrides
- router mode: OpenAI-compatible backend via `ROUTER_BASE_URL`
- local security: `LOCAL_NETWORK_ONLY`, `LOCAL_ACCESS_USERNAME`, `LOCAL_ACCESS_PASSWORD`
- local storage paths for workflows, sessions, ideas, logs, and builder jobs

Recommended first-run setup:
- keep all roles on direct mode unless you are intentionally testing a local router
- set only the provider keys you actually want to use
- leave missing keys blank to exercise mock-safe behavior
- set `LOCAL_ACCESS_PASSWORD` before using the dashboard from another device on your LAN

## Roadmap direction

Good next steps after this V1:
- prompt-delivery wiring into tracked sessions
- structured result ingestion and history views
- tighter provider/router configuration docs
- targeted tests for storage helpers and route handlers
- screenshot refreshes / examples for GitHub presentation
