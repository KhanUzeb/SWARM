# AGENTS.md — guide for coding agents working in Swarm

> Swarm is a self-hosted workspace where AI agents are named teammates in
> team chat. This file tells a coding agent everything it needs to be
> productive here. Humans: see [README.md](README.md) and
> [CONTRIBUTING.md](CONTRIBUTING.md).

## 0. Public-repo rules (read first)

- This is a **public repository**: assume all tracked content and diffs are
  public. Never commit secrets, `.env` files, tokens, private URLs,
  personal data, or real production data — use fake placeholders.
- Review `git status` and the staged diff before committing. Never
  force-add ignored files (`context.md`, `.scratch/`, `*.db`, `.env`).
- Commit messages, PR descriptions, and review replies are public too.
  Describe test results in words; never include local paths, usernames,
  hostnames, account emails, or key material. Naming the env var a
  workflow reads is fine.
- For UI changes, quote any new user-facing copy in the PR and explain why
  it is necessary and why progressive disclosure would not work instead.

## 1. Stack and commands

| Layer | Choice |
| ----- | ------ |
| Backend | FastAPI + Uvicorn, SQLite via `aiosqlite`, native WebSocket hub |
| LLM | Groq (primary) + 14 more via `backend/ai_support/` + Custom OpenAI-compatible (Ollama/LM Studio/vLLM) |
| Frontend | React 19 + Vite 8, built with `bun`; FastAPI serves `frontend/dist` |
| Tracing | Langfuse (no-op when unconfigured) |
| Deploy | Docker Compose, SQLite on a named volume, `GET /health` probe |

```powershell
# backend (from repo root)
.\.venv\Scripts\Activate.ps1
uv pip install -r requirements.txt
uv pip install -r requirements-dev.txt   # for tests
.\.venv\Scripts\python.exe -m pytest -q  # backend suite (offline, mocked)
python -m uvicorn backend.main:app --reload   # app at http://localhost:8000

# frontend (from frontend/)
bun install
bun run build    # production build served by FastAPI
bun run dev      # hot reload on :5173, proxies /api + /ws
```

No API key needed: `$env:SWARM_DEMO="1"` gives deterministic mock replies
plus a seeded `#general` thread. At least one provider key (e.g.
`GROQ_API_KEY` in `.env`) is needed for real replies.

## 2. Architecture (one product, backend owns orchestration)

```
frontend/src/          intent + rendering only (no orchestration, no retries)
backend/main.py        REST + WS routes, auth, rate limiting, agent triggers
backend/agent.py       multi-persona responder, tools loop, streaming, fallback
backend/ai_support/    provider catalog + resolver + encrypted key store
backend/computer_providers.py  computer abstraction: local|none|fake
backend/tools/         central tool registry (builtins, custom, plugins)
backend/v2.py          durable workflows/runs, OAuth state/PKCE, recovery
backend/db.py          schema + ensure_schema() additive migrations
cli/swarm_cli.py       JSON in/out CLI for scripts and other agents
```

Rules (Rakazo-shaped, enforced in review):

- **Frontends express intent and render state.** Orchestration,
  authorization, validation, retries, recovery, and provider translation
  live in the backend. Never add orchestration logic to JSX.
- **Provider-neutral interfaces.** New providers reuse the shared
  contracts in `backend/ai_support/` and deterministic offline tests.
  Never add provider-specific env vars when the generic connection
  (`custom` + `SWARM_OPENAI_COMPAT_*`) can express the behavior.
- **Keep UI minimal.** Ask what can be removed first; disclose advanced
  capability progressively (`<details>`), not as persistent chrome.
- **One source of truth.** Reuse existing primitives; remove duplication
  and speculative abstractions. Add an interface only to protect a real
  external/platform boundary.

## 3. Auth model

- Register: `POST /api/register` → composite token `"<handle>:<raw>"`.
  Only the SHA-256 hash of `<raw>` is stored. First user is `admin`.
- REST writes: `Authorization: Bearer <handle>:<raw>`. All `/api/*` data
  reads need it too, except `GET /api/status`, `GET /health`, and
  `POST /api/register`.
- WS: first frame must be
  `{"token": "<handle>:<raw>", "last_seen_id": null}` or the server closes
  with `4001`. The handle is fixed at handshake; clients never set
  `author` over WS.
- Admin-only: agent/team/tool/provider writes. Browser guard: `Origin`
  must match `SWARM_ALLOWED_ORIGINS` with `X-Swarm-Client: web`.
- Rate limit: 500 ms between writes per handle (`429` / `slow down`).

## 4. Provider model (BYO credentials)

- Catalog: `backend/ai_support/providers.py` (priority-ordered).
  Credentials: `backend/ai_support/store.py` (sealed with `SWARM_SECRET`).
  Resolution: `backend/ai_support/resolver.py` (stored key → env fallback).
- **Secrets are never returned by any API** — only key hints and
  connection metadata. Keep it that way; `tests/test_rakazo_parity.py`
  guards this.
- OAuth access/refresh tokens are sealed; expiring tokens refresh when
  the provider exposes a refresh endpoint + client env config.
- Generic endpoint: provider id `custom`, base URL from
  `SWARM_OPENAI_COMPAT_BASE_URL` (default `http://127.0.0.1:11434/v1`),
  key from `SWARM_OPENAI_COMPAT_API_KEY` (any value works for keyless
  local servers). UI: Command Center → AI providers → Custom card.
- Env loading: project-root `.env` then `backend/.env`
  (`override=False`; real process env wins). Tests set
  `PYTHON_DOTENV_DISABLED=1`.

## 5. Computer model (Team vs Private)

- `SWARM_COMPUTER_PROVIDER=local|none|fake` (default `local`).
  `none` boots without a computer host; `fake` is a test emulator.
- **Team home** = shared sandbox (`SWARM_SANDBOX_DIR`). **Private homes**
  = per-bot drafts at `private/<agent>` under the team home. See
  `backend/computer_providers.py`.
- `/api/computer` returns `provider` + `homes` metadata alongside the
  legacy `workspace`/`files` keys (backward compatible — do not remove
  legacy keys without a migration note).
- The sandbox is cwd + timeout, **not** container or network isolation.
  Host-system tools are bound to `SWARM_SYSTEM_ROOT`. Never claim
  otherwise in code, UI copy, or docs.

## 6. Agent contract (essentials)

- Triggers: `@mention` in rooms (whole-word, case-insensitive); every
  message in a bot's 1:1 (`dm-<name>`) and group rooms; `[routine:…]`
  ticks. Multi-mentions reply **sequentially in mention order**.
- Agent replies can `@handoff` to another bot (depth cap 3), or call
  `delegate_task` to run another bot as a headless sub-agent
  (depth cap 2; sub-agents never approve/spawn/delegate at the cap).
- Tools are capped per trigger (default 6, hard cap 24); rooms offer
  tools on every work-like turn — only clear smalltalk stays text-only.
  Every tool call persists as a `system` audit message in-channel.
- Streaming: `agent_stream_start` / `agent_token` frames, then one
  persisted `message`. Partial streams persist with a cutoff marker.
- Failures become `[agent error: …]` messages (never raw exceptions,
  never a 500); retry via `POST /api/messages/{id}/retry`.
- `SPEC.md` is the contract. Code vs `SPEC.md` disagreement = bug.

## 7. Data and migrations

- SQLite at `SWARM_DB_PATH` (`/app/data/swarm.db` in Docker).
- `ensure_schema()` in `backend/db.py` upgrades in place via
  `PRAGMA table_info` + `ALTER TABLE` — additive columns and new tables
  only. A type change needs a real migration; say so in the PR.
- Tests use a temp SQLite file and never touch `swarm.db`. Keep tests
  deterministic and offline (Groq mocked).

## 8. Docs map (after the 2026 cleanup)

| File | Purpose |
| ---- | ------- |
| `README.md` | SEO-friendly front door + quick start + demo script |
| `SPEC.md` | API + agent contract (source of truth) |
| `docs/PRODUCT.md` | Thesis, competition, scope, principles |
| `docs/PRODUCT-APPROACH.md` | Problem, core loop, success criteria |
| `docs/DEPLOY.md` | Docker deployment |
| `docs/CHANGELOG.md` | Feature layers + revert map |
| `docs/adr/` | Architecture decisions (append-only, never rewrite) |
| `ai-support/README.md` | Provider settings user guide |

Deleted in the cleanup: root `VISION.md`, `PROJECT.md`, `PROBLEM.md`
(folded into `docs/PRODUCT.md`) and `frontend/frontend/` (stale duplicate
of `frontend/src/`). Do not reintroduce root-level vision/project/problem
docs or nested frontend copies.

## 9. Verification before every PR

```powershell
.\.venv\Scripts\python.exe -m pytest -q
cd frontend && bun run build
```

Stay with the PR until CI (`.github/workflows/ci.yml`: pytest + bun
build) and review feedback are resolved. Address every actionable item;
do not merge with pending bot reviews or unresolved threads.

## 10. Suggested first tasks for a new agent

1. `pytest -q` + `bun run build` to confirm green.
2. Register → connect `SWARM_DEMO=1` → create a workflow → launch a run →
   watch WS events → approve/deny an approval node → download the report.
3. Read `backend/main.py` routes + `backend/v2.py` recovery before
   touching execution paths.
