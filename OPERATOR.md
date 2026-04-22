# Orchestrator V1 — Operator Guide

Practical reference for running, operating, and demoing the local orchestrator.

---

## Quick start

```bash
cd orchestrator
cp .env.example .env          # leave provider keys blank to run in mock-safe mode
uv sync
uv run uvicorn backend.app:app --reload --host 127.0.0.1 --port 8100
```

Open:
- Dashboard: `http://localhost:8100`
- API docs: `http://localhost:8100/api/docs`

State files are written to `data/`, `bridge/`, and `logs/`. These are local-only and not committed to git.

---

## End-to-end operator flow

This is the canonical V1 flow. Each step maps to a single API call. All state changes are auditable via the relevant store files.

### 1. Create an idea

```bash
curl -s -X POST http://localhost:8100/api/ideas \
  -H 'Content-Type: application/json' \
  -d '{"title": "Add session handoff summary", "initial_note": "Capture current stage and next safe step when handing off between sessions."}'
```

Use the `idea_id` from the response in the next steps.

### 2. Finalize the idea

```bash
curl -s -X POST http://localhost:8100/api/ideas/<idea_id>/finalize \
  -H 'Content-Type: application/json' \
  -d '{"note": "Agreed: add handoff endpoint and model."}'
```

### 3. Convert idea → workflow

```bash
curl -s -X POST http://localhost:8100/api/ideas/<idea_id>/convert \
  -H 'Content-Type: application/json' \
  -d '{"approval_mode": "auto_with_limits"}'
```

This creates a `WorkflowState` and links it to the idea. Use the `workflow.id` from the response.

### 4. Approve a proposal

Proposals are created by the workflow engine when it runs. To approve a pending proposal manually:

```bash
curl -s -X POST http://localhost:8100/api/workflows/<workflow_id>/proposals/<proposal_id>/approve \
  -H 'Content-Type: application/json' \
  -d '{"approval": "approved", "notes": "Looks good."}'
```

In `auto_with_limits` mode, proposals within file and token limits are approved automatically.

### 5. Dispatch a builder job

```bash
curl -s -X POST http://localhost:8100/api/workflows/<workflow_id>/builder-jobs \
  -H 'Content-Type: application/json' \
  -d '{"proposal_id": "<proposal_id>", "category": "build"}'
```

The job is persisted to `bridge/builder_jobs/inbox/<job_id>.json` and its handoff contract is written as a structured prompt package for the assigned session.

Use the `job.id` from the response.

### 6. Create a session and assign

```bash
curl -s -X POST http://localhost:8100/api/sessions \
  -H 'Content-Type: application/json' \
  -d '{"session_id": "claude-1", "role": "claude"}'

curl -s -X POST http://localhost:8100/api/sessions/claude-1/assign \
  -H 'Content-Type: application/json' \
  -d '{"job_id": "<job_id>", "next_expected_action": "Deliver prompt contract to session."}'
```

Only one active session can be assigned to a job at a time. A second assign to the same job returns HTTP 400.

### 7. Preview the handoff prompt

```bash
curl -s http://localhost:8100/api/sessions/claude-1/prompt-preview
```

Returns the full handoff contract text, token estimate, contract version, and expected return format. This is the prompt that gets delivered to the builder session. It does not mutate state.

### 8. Mark prompt delivered

After you have manually delivered the prompt to the Claude/Antigravity session:

```bash
curl -s -X POST http://localhost:8100/api/sessions/claude-1/mark-delivered \
  -H 'Content-Type: application/json' \
  -d '{"operator": "operator", "note": "Delivered manually."}'
```

Session transitions to `waiting_for_result`. A `PROMPT_DELIVERED` lifecycle event is appended.

### 9. Record the result

When the session returns a result:

```bash
curl -s -X POST http://localhost:8100/api/sessions/claude-1/result \
  -H 'Content-Type: application/json' \
  -d '{
    "outcome": "success",
    "last_result_summary": "Handoff endpoint and model added. Tests passing.",
    "notes": "No follow-up needed.",
    "output_ref": "pr://orchestrator/123",
    "artifacts": ["pr://orchestrator/123"],
    "next_expected_action": "Close workflow.",
    "metadata": {}
  }'
```

Valid `outcome` values: `success`, `partial_success`, `needs_followup`, `blocked`, `failed`.

The system deterministically suggests a next action (`mark_workflow_complete`, `create_followup_job`, `request_approval`, `retry_with_changes`) based on outcome.

### Check result and next step

```bash
curl -s http://localhost:8100/api/workflows/<workflow_id>/next-step
curl -s http://localhost:8100/api/builder-jobs/<job_id>/results
```

---

## State health check

Run the reconciler at any time to audit and optionally repair stale state:

```bash
# Dry-run (read-only)
uv run python -m backend.reconcile_state

# Apply safe deterministic repairs
uv run python -m backend.reconcile_state --apply
```

The reconciler detects:
- multiple active sessions assigned to the same job
- jobs stuck in `pending` with ingested result state (lifecycle gap)
- sessions stuck in `waiting_for_prompt_delivery` after prompt delivery was already recorded
- completed workflows with nonterminal next-step suggestions

---

## Assistant Brain provider setup

The Assistant Brain chat panel uses a configurable LLM provider. By default it runs in `auto` mode — it tries OpenAI, then Anthropic, and falls back to mock if neither key is set.

**Enable a live provider:**

Set the relevant key in `.env` and optionally pin the provider:

```bash
# Use OpenAI (default auto priority)
OPENAI_API_KEY=sk-...
ASSISTANT_PROVIDER=auto          # or explicitly: openai

# Use Anthropic instead
ANTHROPIC_API_KEY=sk-ant-...
ASSISTANT_PROVIDER=anthropic

# Force mock mode (no external calls)
ASSISTANT_PROVIDER=mock
```

**Supported modes:**

| `ASSISTANT_PROVIDER` | Behavior |
|---|---|
| `auto` (default) | Tries openai, then anthropic. Falls back to mock if no keys are set. |
| `openai` | Uses OpenAI. Falls back to mock if `OPENAI_API_KEY` is missing. |
| `anthropic` | Uses Anthropic. Falls back to mock if `ANTHROPIC_API_KEY` is missing. |
| `mock` | Always mock. No external API calls. Safe for demos. |

**Verify the active provider:**

- **UI**: The Assistant Brain thread bar shows a badge — green `openai / gpt-4o` when live, gray `mock` with a tooltip showing the fallback reason when in mock mode.
- **API**: `GET /api/assistant/provider-status` returns the requested mode, active provider, model, and fallback reason.

**Override the model:**

```bash
ASSISTANT_MODEL_OPENAI=gpt-4o-mini       # default: gpt-4o
ASSISTANT_MODEL_ANTHROPIC=claude-haiku-4-5-20251001  # default: claude-sonnet-4-20250514
```

---

## V1 scope

What V1 covers:
- idea intake → workflow → proposal → dispatch → session → result → next-step
- local builder job queue with inbox / outbox / archive / lock file bridge
- session lifecycle with assignment guard, prompt preview, delivery tracking, result ingestion
- deterministic next-step planning on ingested results
- structured handoff contracts for manual prompt delivery
- mock-safe provider fallback when API keys are absent
- reconciler for detecting and repairing stale state
- session handoff and restart prompt generation
- LAN access with Basic Auth for phone/tablet access on the same network

What V1 does not do:
- automated prompt delivery into Claude/Antigravity sessions (manual only)
- live destructive apply to any external runtime (trading bot, shell, etc.)
- multi-user auth or hosted deployment
- automated worker supervision beyond local queue tracking
- real options/spot trading integration

---

## Known limitations

| Limitation | Impact | Mitigation |
|---|---|---|
| Prompt delivery is manual | Operator must copy-paste handoff contract into the session | `GET /api/sessions/{id}/prompt-preview` returns the full text |
| Bridge snapshot in workflow context can drift from actual job status | Cosmetic — does not affect routing | Run reconciler; future rule will sync drift |
| No TTL on unclaimed inbox jobs | Stale pending jobs accumulate silently | Review `GET /api/builder-jobs/queue` regularly |
| Duplicate log lines in paper trading runner | Cosmetic | Unrelated to orchestrator |
| No automated test coverage for provider routing with live keys | Mock-safe tests pass; live provider calls are not tested in CI | Use `POST /api/demo/run` with real keys for a manual smoke test |

---

## Demo walkthrough

Use this flow to show V1 to someone else. All steps run in mock-safe mode without any provider keys.

**Setup (once):**
```bash
cd orchestrator && uv run uvicorn backend.app:app --reload --host 127.0.0.1 --port 8100
```

**Step 1 — show the dashboard**
Open `http://localhost:8100`. Point out: workflows, sessions, ideas, queue panels.

**Step 2 — run a demo workflow**
```bash
curl -s -X POST http://localhost:8100/api/demo/run \
  -H 'Content-Type: application/json' \
  -d '{}' | python3 -m json.tool | grep -E '"status"|"current_stage"|"is_demo"'
```
Shows the planner → builder → reviewer loop completing end-to-end in mock mode.

**Step 3 — create and dispatch a real operator job**
```bash
# Create idea
IDEA=$(curl -s -X POST http://localhost:8100/api/ideas \
  -H 'Content-Type: application/json' \
  -d '{"title":"Demo job","initial_note":"Show end-to-end."}' | python3 -c "import sys,json; print(json.load(sys.stdin)['idea']['id'])")

# Finalize
curl -s -X POST http://localhost:8100/api/ideas/$IDEA/finalize \
  -H 'Content-Type: application/json' -d '{"note":"ok"}' > /dev/null

# Convert to workflow
WF=$(curl -s -X POST http://localhost:8100/api/ideas/$IDEA/convert \
  -H 'Content-Type: application/json' \
  -d '{"approval_mode":"auto_with_limits"}' | python3 -c "import sys,json; print(json.load(sys.stdin)['workflow']['id'])")
echo "Workflow: $WF"
```

**Step 4 — show the queue and sessions**
```bash
curl -s http://localhost:8100/api/builder-jobs/queue | python3 -m json.tool | head -30
curl -s http://localhost:8100/api/sessions | python3 -m json.tool | head -20
```

**Step 5 — show the health check**
```bash
curl -s http://localhost:8100/api/health | python3 -m json.tool
uv run python -m backend.reconcile_state
```
Zero issues = clean state.

**Talking points:**
- all state is local JSON — no database, no cloud dependency
- mock-safe by default — works without any API keys
- operator controls every step: approve, dispatch, assign, preview, deliver, record
- reconciler can audit and repair stale state at any time
- the bridge queue (inbox/outbox/archive) is inspectable as plain files

---

## MCP server

The orchestrator exposes a minimal MCP tool surface for use with MCP clients (Claude Desktop, etc.).

**Run via stdio (default — for Claude Desktop):**
```bash
cd orchestrator
uv run python -m backend.mcp_server
```

**Run via SSE (for browser-based or HTTP MCP clients):**
```bash
cd orchestrator
uv run python -m backend.mcp_server --sse
# Listens on http://127.0.0.1:8101
```

**Claude Desktop config** (`~/Library/Application Support/Claude/claude_desktop_config.json`):
```json
{
  "mcpServers": {
    "orchestrator": {
      "command": "uv",
      "args": ["run", "python", "-m", "backend.mcp_server"],
      "cwd": "/path/to/calwbot/orchestrator"
    }
  }
}
```
Replace `/path/to/calwbot/orchestrator` with the actual absolute path.

**Tools exposed:**

Read (safe, no state mutation):
| Tool | Purpose |
|---|---|
| `health` | System status and provider availability |
| `list_workflows` | All workflow summaries |
| `get_workflow` | Single workflow detail |
| `list_ideas` | All idea threads |
| `get_idea` | Single idea thread with messages |
| `list_sessions` | All session summaries |
| `get_session` | Single session with lifecycle |
| `preview_prompt` | Handoff contract for an assigned session |

Write (bounded — idea/workflow creation only, no shell execution):
| Tool | Purpose |
|---|---|
| `create_idea` | Start a new idea thread |
| `add_idea_note` | Append a note (role: user/assistant/system) |
| `finalize_idea` | Mark idea ready for conversion |
| `convert_idea` | Convert finalized idea → workflow |

**Verify with `mcp dev`:**
```bash
cd orchestrator
uv run mcp dev backend/mcp_server.py
```
This opens the MCP inspector UI where you can call each tool interactively.

## Useful endpoints reference

| Endpoint | Purpose |
|---|---|
| `GET /api/health` | System status and provider availability |
| `GET /api/config` | Runtime configuration summary |
| `GET /api/workflows` | All workflows |
| `GET /api/builder-jobs/queue` | Full queue with status breakdown |
| `GET /api/sessions` | All sessions with status |
| `GET /api/ideas` | All idea threads |
| `GET /api/sessions/{id}/prompt-preview` | Read-only prompt contract preview |
| `GET /api/workflows/{id}/next-step` | Latest next-step suggestion |
| `GET /api/workflows/{id}/results` | Full result history |
| `POST /api/demo/run` | Create and run a full demo workflow in one call |
| `POST /api/sessions/{id}/assign` | Assign a job to a session |
| `POST /api/sessions/{id}/mark-delivered` | Record manual prompt delivery |
| `POST /api/sessions/{id}/result` | Record session result and trigger next-step planning |
| `POST /api/sessions/{id}/handoff` | Generate a restart handoff summary |
| `GET /api/sessions/resumable` | List sessions with handoff summaries |
