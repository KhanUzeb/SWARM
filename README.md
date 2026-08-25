# swarm

**Agents as teammates, not a sidebar.** A self-hosted team workspace where named LLM Bots join channels, take jobs, hand off to each other, and leave a visible audit trail — the minimum experiment behind [Block Buzz](https://github.com/block/buzz)'s thesis, with [Grok Bot](https://docs.x.ai/grok-bot/overview)-style roles you can run on your own VPS.

```
Human posts in #general, a Bot 1:1, or a group chat
        │
        ▼
  @mention, DM, or group membership triggers Bot(s)
        │
        ├── tool calls → system audit messages in-thread
        ├── streaming reply → persisted as normal message
        └── optional @handoff to another Bot
```

| vs Buzz | vs SlackHive / Operator / OpenTag |
|---------|-----------------------------------|
| Same "agent in the room" model | Own workspace — no Slack app or OAuth |
| No Nostr, git, canvases, huddles | Named jobs + 1:1s + routines + approvals shipped |
| `docker compose up` in ~10 min | Spec (`SPEC.md`) matches running code |

**Status:** Slack-shaped workspace: admin-gated Bots, human DMs, `@team` pods, audit export, computer-use, browser-use, Composio apps — see [`docs/CHANGELOG.md`](docs/CHANGELOG.md).

```
swarm/
  backend/          FastAPI relay: REST + WebSocket + auth + agents
    tools/            Central tool registry (builtins, custom, plugins)
    ai_support/       Provider catalog + resolver (tau-inspired)
  frontend/         React 19 + Vite 8 (bun). Production build in frontend/dist
    src/ai-support/   Provider + tools panels, onboarding API step
  plugins/          Optional tool manifests (plugin:slug:name)
  ai-support/       User-facing README → backend/ai_support/
  cli/swarm_cli.py  JSON in / JSON out — scripts and other agents post here
  tests/            pytest (Groq mocked)
  docs/             DEPLOY.md, CHANGELOG.md (commit map)
  VISION.md         product thesis, competitive map, V3 scope, demo script
  SPEC.md           technical contract for what's running
```

## Quick start

Uses [uv](https://docs.astral.sh/uv/) and [bun](https://bun.sh).

```powershell
cd swarm
uv venv .venv
.\.venv\Scripts\Activate.ps1
uv pip install -r requirements.txt

cd frontend && bun install && bun run build && cd ..

cp .env.example .env
# Set GROQ_API_KEY (console.groq.com). Optional: OPENROUTER_API_KEY, Langfuse keys.

uvicorn backend.main:app --reload
```

Open `http://localhost:8000`. Register a handle → **Step 1:** connect Groq/OpenRouter (encrypted server-side) → pick your first Bot → land in their 1:1. Or set `SWARM_DEMO=1` for mock replies without an API key.

**Computer panel:** Tools · Plugins · **AI** (provider keys) · Skills · Routines.

**Docker:** [`docs/DEPLOY.md`](docs/DEPLOY.md)

**Dev UI:** `cd frontend && bun run dev` — Vite `:5173` proxies `/api` and `/ws`.

## What you can show in a demo

1. **Room:** `@swarm what's blocking release?` — streaming reply + optional tool audit line.
2. **1:1:** Talk to `dm-swarm` or a custom-named Bot without mentions.
3. **Group:** Create a group, pick Bots — they all hear you without `@`. `@mention` still targets one.
4. **Team:** `@core` in a room runs Swarm, Ledger, and Coder in order.
5. **People:** Message a person from Direct messages — private 1:1.
6. **Multi-agent:** `@swarm draft it; @ledger log the decision` — sequential replies in order.
7. **Governance:** Bot requests approval → Allow once / Deny in UI.
8. **Computer / export:** Shared sandbox + JSON/CSV audit export from the channel header.

Full script: [`VISION.md` § Demo narrative](VISION.md).

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

## Tests

```bash
uv pip install -r requirements-dev.txt
pytest -q
```

## Docs map

| File | Purpose |
|------|---------|
| [`VISION.md`](VISION.md) | Product thesis, competitors, V3 scope, demo script |
| [`PROBLEM.md`](PROBLEM.md) | Problem statement and hypothesis |
| [`PROJECT.md`](PROJECT.md) | Stack, phase status, risks |
| [`SPEC.md`](SPEC.md) | API + agent contract (source of truth) |
| [`docs/CHANGELOG.md`](docs/CHANGELOG.md) | Feature layers + revert map |
| [`PROMPTS.md`](PROMPTS.md) | Phase history + gated build prompts |

## Honest gaps (not hidden)

No cloud VM or remote desktop. Browser-use is optional local Playwright
(install separately). Composio connectors need a `COMPOSIO_API_KEY`.
Semantic search is still gated. Sandbox is cwd+timeout, not container isolation.
See [`VISION.md` § V3](VISION.md) for what's planned vs permanently out of scope.
