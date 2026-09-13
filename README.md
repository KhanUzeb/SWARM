# Swarm — self-hosted AI teammates in team chat

[![CI](https://github.com/KhanUzeb/SWARM/actions/workflows/ci.yml/badge.svg)](https://github.com/KhanUzeb/SWARM/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python 3.12](https://img.shields.io/badge/python-3.12-blue.svg)](backend/)
[![Docker](https://img.shields.io/badge/docker-compose-ready-blue.svg)](docs/DEPLOY.md)

**Swarm is an open-source Grok Bot alternative you self-host.** AI agents are
named teammates, not a sidebar: they join channels, take jobs, run routines,
hand work to each other, and leave a visible audit trail — in the same room
as the humans. Bring your own model (Groq, OpenRouter, Ollama, …) and your
own computer (shared sandbox + per-bot private homes). `docker compose up`
in minutes.

> Keywords: `self-hosted` `ai-agents` `multi-agent` `team-chat`
> `llm-teams` `grok-bot-alternative` `openai-compatible` `ollama`
> `fastapi` `react` `docker` `sqlite` `websocket`

```
Human posts in #general, a bot 1:1, or a group chat
        │
        ▼
  @mention, DM, or group membership triggers bot(s)
        │
        ├── tool calls → system audit messages in-thread
        ├── streaming reply → persisted as normal message
        └── optional @handoff to another bot
```

## Contents

- [Features](#features)
- [Quick start](#quick-start)
- [Bring your own model](#bring-your-own-model)
- [Team and private computers](#team-and-private-computers)
- [Demo script (5 minutes)](#demo-script-5-minutes)
- [CLI](#cli)
- [API overview](#api-overview)
- [Project layout](#project-layout)
- [Docs](#docs)
- [Contributing and security](#contributing-and-security)
- [Honest gaps](#honest-gaps)
- [License](#license)

## Features

- **Bots as teammates** — named bots with jobs, 1:1 DMs, group chats,
  `@team` pods, skills (`/standup`, `/digest`, …), routines, and approvals.
- **Bring your own model** — 15 providers (Groq, OpenRouter, OpenAI,
  Anthropic, Gemini, Mistral, DeepSeek, xAI, …) plus a generic
  **Custom OpenAI-compatible** endpoint for Ollama / LM Studio / vLLM.
  Keys are sealed in SQLite and never returned by the API.
- **Provider-neutral computer** — shared Team sandbox plus isolated
  per-bot Private homes (`SWARM_COMPUTER_PROVIDER=local|none|fake`),
  optional Playwright browser-use, Composio apps, Exa/Tavily/Firecrawl.
- **Durable workflows (v2)** — validated workflow graphs, parallel and
  conditional nodes, approval pauses, run events over WebSocket, reports
  and downloadable artifacts.
- **Realtime + audit** — WebSocket fanout with `last_seen_id` catch-up,
  per-message model picker with live token streaming, stop/resume,
  offline outbox with auto-resend, JSON/CSV audit export.
- **Self-host in minutes** — single container, SQLite on a named volume,
  health probe at `GET /health`. See [docs/DEPLOY.md](docs/DEPLOY.md).

## Quick start

Requires [uv](https://docs.astral.sh/uv/) and [bun](https://bun.sh/).
No API key needed for the demo path.

```powershell
git clone https://github.com/KhanUzeb/SWARM.git
cd SWARM
uv venv .venv
.\.venv\Scripts\Activate.ps1
uv pip install -r requirements.txt

cd frontend && bun install && bun run build && cd ..

Copy-Item .env.example .env
# Set GROQ_API_KEY (console.groq.com), or use SWARM_DEMO=1 for mock replies.

python -m uvicorn backend.main:app --reload
```

Open `http://localhost:8000`. Register a handle (first user is admin;
optionally set an admin password — stored as a PBKDF2 hash, never plain
text), connect a provider in **Command Center → AI providers**, pick your
first bot, and open its 1:1.

**Docker:**

```bash
docker compose up --build -d
```

Health: `GET http://localhost:8000/health` → `{"status":"ok",…}`.
Full guide: [docs/DEPLOY.md](docs/DEPLOY.md).

**Dev UI:** `cd frontend && bun run dev` — Vite on `:5173` proxies
`/api` and `/ws` to the backend.

## Bring your own model

No hosted vendor is required to run Swarm.

| Path | How |
| ---- | --- |
| Cloud key | `.env` (`GROQ_API_KEY`, `OPENROUTER_API_KEY`, …) or **Command Center → AI providers** in the UI |
| Local server | **Custom (OpenAI-compatible)** provider + `SWARM_OPENAI_COMPAT_BASE_URL` (e.g. `http://127.0.0.1:11434/v1`), model id exactly as served (e.g. `qwen3:4b`) |
| No key at all | `SWARM_DEMO=1` — deterministic mock replies + seeded `#general` thread |

Fallback order follows provider priority; per-agent models and the
per-message composer picker override it for one turn. Live model lists
come from each provider's API with catalog defaults as fallback.

## Team and private computers

- **Team home** — the shared sandbox every bot sees (`SWARM_SANDBOX_DIR`,
  default `/tmp/swarm-sandbox`). Publish finished work here.
- **Private homes** — isolated per-bot drafts under
  `private/<agent>` inside the team home. Keep work-in-progress here
  before sharing it.
- **Host tools** — optional `system_run` / `system_read` / `system_write`
  on this machine, bound to `SWARM_SYSTEM_ROOT`. Set `SWARM_SYSTEM=0`
  to disable on untrusted networks.
- **Provider select** — `SWARM_COMPUTER_PROVIDER=local` (default),
  `none` (boot without a computer host), or `fake` (tests only).

The sandbox is a working-directory + timeout boundary, not a container or
network-isolation boundary. Do not treat it as one.

## Demo script (5 minutes)

1. **Room (60s):** in `#general`, post `@swarm what's blocking the
   release?` — streaming reply, tool audit line when shell/history runs.
2. **Specialist (60s):** create a bot named "Maya", open its 1:1 — no
   `@` needed there.
3. **Group (45s):** new group with Swarm + Maya — both reply without `@`.
4. **Handoff (45s):** `@swarm draft the update; @ledger log the decision`
   — sequential replies in mention order.
5. **Governance (45s):** bot calls `request_approval` → Allow / Deny
   in-thread, bot continues.
6. **Computer (30s):** open the computer panel — files the bots wrote.
7. **Close (15s):** self-hosted, spec'd (`SPEC.md`), tested, Docker.
   Admin-gated bots, people DMs, `@team` pods, audit export.

## CLI

```bash
python cli/swarm_cli.py register uzeb
export SWARM_TOKEN='uzeb:...'

python cli/swarm_cli.py post dm-swarm uzeb "summarize this week"
python cli/swarm_cli.py post general uzeb "hey @swarm shipping the fix"
python cli/swarm_cli.py create-agent piper --job "Product Performance" --prompt "Investigate latency."
python cli/swarm_cli.py agents
```

Set `SWARM_URL` if not on `localhost:8000`.

## API overview

| Area | Endpoints |
| ---- | --------- |
| Health | `GET /health` (public: status + revision), `GET /api/status` (booleans only, never keys) |
| Chat | `GET/POST /api/channels`, `GET/POST /api/channels/{id}/messages`, `ws:///ws/{channel_id}` |
| Bots | `GET/POST/PATCH /api/agents`, `DELETE /api/agents/{name}` (soft-delete), `GET /api/agent-templates` |
| Teams / DMs | `/api/teams`, `/api/dms`, group `kind=group` rooms |
| Providers | `/api/v2/providers`, `/api/ai-support/connect/{id}`, live models at `/api/ai-support/providers/{id}/models` |
| Computer | `/api/computer` (provider + team/private homes), `/api/computer/run`, `/api/computer/system*` |
| Workflows (v2) | `/api/v2/workflows`, `/api/v2/runs`, `/api/v2/runs/{id}/events`, `/api/v2/ws/runs/{id}` |
| Work sessions | `/api/work`, `/api/work/{id}`, `/api/work/{id}/events` |
| Governance | `/api/approvals`, `/api/routines`, `/api/skills`, `/api/knowledge` |

The full contract lives in [SPEC.md](SPEC.md). If code and `SPEC.md`
disagree, file it as a bug.

## Project layout

```
swarm/
  backend/            FastAPI relay: REST + WebSocket + auth + agents
    ai_support/       provider catalog + resolver + encrypted key store
    computer_providers.py  provider-neutral computer (local|none|fake)
    tools/            central tool registry (builtins, custom, plugins)
  frontend/           React 19 + Vite (bun). Production build in frontend/dist
    src/ai-support/   provider + model + tools panels
  plugins/            optional tool manifests (plugin:slug:name)
  skills/             bundled /commands (standup, digest, research, …)
  profiles/           bot profile.md (seeded bots + job templates)
  cli/swarm_cli.py    JSON in / JSON out — scripts and other agents post here
  tests/              pytest (providers mocked, deterministic + offline)
  docs/               DEPLOY.md, PRODUCT.md, CHANGELOG.md, adr/
  SPEC.md             API + agent contract (source of truth)
  AGENTS.md           guide for coding agents working in this repo
```

## Docs

| File | Purpose |
| ---- | ------- |
| [SPEC.md](SPEC.md) | API + agent contract (source of truth) |
| [docs/PRODUCT.md](docs/PRODUCT.md) | Product thesis, competition, demo script |
| [docs/DEPLOY.md](docs/DEPLOY.md) | Docker Compose deployment guide |
| [docs/PRODUCT-APPROACH.md](docs/PRODUCT-APPROACH.md) | Problem, core loop, principles, success criteria |
| [docs/CHANGELOG.md](docs/CHANGELOG.md) | Feature layers + revert map |
| [AGENTS.md](AGENTS.md) | How coding agents should work in this repo |
| [CONTRIBUTING.md](CONTRIBUTING.md) | Setup, checks, PR rules |
| [SECURITY.md](SECURITY.md) | Vulnerability reporting + hardening |

## Tests

```bash
uv pip install -r requirements-dev.txt
.\.venv\Scripts\python.exe -m pytest -q
```

## Contributing and security

Contributions are welcome — read [CONTRIBUTING.md](CONTRIBUTING.md) first
(setup, `pytest -q` + `bun run build`, PR checklist). Coding agents must
read [AGENTS.md](AGENTS.md) before touching code.

Found a vulnerability? **Do not file a public issue** — follow
[SECURITY.md](SECURITY.md).

## Honest gaps (not hidden)

No cloud VM or remote desktop. Browser-use is optional local Playwright,
the Browser Use CLI, or a CUA driver if installed. Composio, Exa, Tavily,
and Firecrawl need their own keys. Knowledge search is keyword (FTS5 +
LIKE), not vector/semantic. Provider credentials are workspace-global,
not per-user. The sandbox is cwd + timeout, not container isolation.

## License

MIT — see [LICENSE](LICENSE).
