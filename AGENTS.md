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
- The code is the contract. `docs/PRODUCT.md` records what the product is for
  and what is deliberately out of scope; section 8a records the visual system.
  If the code drifts from either, that is the bug.

## 7. Data and migrations

- SQLite at `SWARM_DB_PATH` (`/app/data/swarm.db` in Docker).
- `ensure_schema()` in `backend/db.py` upgrades in place via
  `PRAGMA table_info` + `ALTER TABLE` — additive columns and new tables
  only. A type change needs a real migration; say so in the PR.
- Tests use a temp SQLite file and never touch `swarm.db`. Keep tests
  deterministic and offline (Groq mocked).

## 8. Docs map

| File | Purpose |
| ---- | ------- |
| `README.md` | SEO-friendly front door + quick start + demo script |
| `AGENTS.md` | This file. Product rules **and** the visual system |
| `docs/PRODUCT.md` | Thesis, competition, scope, principles |
| `docs/DEPLOY.md` | Docker deployment |
| `docs/CHANGELOG.md` | Feature layers + revert map |
| `ai-support/README.md` | Provider settings user guide |

Two things are the whole durable record: **the code** for behaviour and **this
file's section 8a** for the visual system. `docs/PRODUCT.md` says what the
product is for and what is deliberately out of scope. A local `DESIGN.md` may
exist on a working machine; it is gitignored, so never rely on it being present
and never add a tracked reference to it.

Deleted: root `VISION.md`, `PROJECT.md`, `PROBLEM.md` (folded into
`docs/PRODUCT.md`); `frontend/frontend/` (stale duplicate of `frontend/src/`);
`SPEC.md`, `docs/PRODUCT-APPROACH.md`, and `docs/adr/` (2026-09-26 — their
content lives in the code, section 8a below, and `docs/CHANGELOG.md`).
`docs/VISUAL-IDENTITY.md` went at the same time, replaced by the station board.
Do not reintroduce any of them, root-level vision/project/problem docs, or
nested frontend copies. Record a decision's rationale in the code comment or the
section below that it affects, and add a row to `docs/CHANGELOG.md`.

## 8a. Design system

**Section 8a is the contract for anything visual.** Read it before touching
`frontend/src/styles.css` or any component's markup.

- **The station board.** Two rules generate the whole interface: every
  discrete, stateful thing is a physical flap module with a lamp behind it;
  everything continuous is a printed line on a roll. There are no message
  bubbles, for anyone.
- **State is never a tint.** A component's state is carried by the lamp
  behind a flap and by the flap face inverting. Exactly two signal hues
  exist: enamel green (`--go`) for live/cleared, enamel red (`--hold`) for
  needs-you/hold. Adding a third hue means re-deciding the world, not
  picking a colour.
- **One stylesheet, one control grammar.** `styles.css` is the only
  stylesheet — `components.css` is gone. The primitives in
  `frontend/src/components/ui/*` delegate to it via the `.btn`, `.chip`,
  `.input` classes rather than carrying their own Tailwind colours. Do not
  reintroduce a second styling system or hard-coded hex in JSX.
- **Render budget is architecture.** No `backdrop-filter`, no blur filters,
  no canvas/WebGL, no gradient washes, no infinite animation on an idle
  surface, and no `width`/`height` transitions. The app is open all day.
  Depth comes from the three declared shadows in `:root`.
- **Icons** are lucide at one weight. Emoji may not stand in for an
  interface icon. A teammate's own avatar glyph is user content and is
  fine.
- **Typography:** Archivo for interface text, Departure Mono for the machine
  register only (codes, timestamps, flap faces, counts). Departure Mono is
  never a costume for "technical".
- No eyebrow labels above headings, no section numbers, no middle-dot meta
  strings, no gradient text, no nested cards.

Rationale for a visual decision lives in section 8a and in the comment on the
code it governs. Add a row to `docs/CHANGELOG.md` when you change the world.

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
