# PROJECT.md — swarm

**Agents as teammates, not a sidebar.** Self-hosted workspace where LLM
Bots are channel members with jobs, 1:1s, skills, routines, and a
shared audit trail — the minimum test of [Buzz](https://github.com/block/buzz)'s
thesis without Nostr/git/workflows.

| Doc | Role |
|-----|------|
| `VISION.md` | Product thesis, competitive map (Buzz, SlackHive, Operator, OpenTag, Grok Bot), V3 scope, demo script |
| `PROBLEM.md` | Why this exists and what hypothesis we're testing |
| `SPEC.md` | Technical contract — if code disagrees, file a bug |
| `PROMPTS.md` | Phase history + gated build prompts |

**Status: V2 + Phase 10 + V3.2/V3.7 + tools/providers/security shipped.**
Onboarding (3 steps), demo mode, tool registry, plugins, tau-style AI
providers, and authenticated API reads are live. See `docs/CHANGELOG.md`.
Remaining V3 items (admin, agent teams, human DMs, audit export) gated in `VISION.md`.

## Stack

| Layer      | Choice                          | Why |
|------------|----------------------------------|-----|
| Backend    | FastAPI + Uvicorn                | async-native, WS support built in, matches VOX/AXIOM stack |
| DB         | SQLite via `aiosqlite`           | zero-ops for a portfolio project; swap to Postgres if this ever needs concurrent writers at scale |
| Realtime   | Native WebSocket, in-memory hub  | one process, one hub — no Redis pub/sub needed at this scale |
| LLM        | Groq (primary), OpenRouter/OpenAI/HF/Together fallback | Priority chain via `backend/ai_support/resolver.py`; env or UI-stored keys |
| Frontend   | React 19 + Vite 8, built with bun | small SPA, hashed assets, FastAPI serves `frontend/dist` |
| Tracing    | Langfuse                         | reuses the eval/observability pattern from VERIS; degrades to no-op if unconfigured |
| Deployment | Docker + docker-compose          | single VPS, named volume for the SQLite file |

## Repo layout

```
swarm/
  backend/
    main.py       FastAPI app: REST + WS routes, auth, rate limiting, agent trigger
    db.py         schema + ensure_schema() + agent_memory
    agent.py      multi-persona LLM responder, harness, memory tools, streaming, Langfuse
    tools/        registry.py — builtins, custom_tools table, plugins/
    ai_support/   providers, resolver, encrypted key store (tau-inspired)
    security.py   CORS allowlist + origin guard + optional auth
    jobs.py      Grok Bot-style job templates for create-Bot
    models.py     pydantic schemas
  plugins/        optional manifests → plugin:slug:tool names
  ai-support/     README pointing at backend/ai_support/
  frontend/
    src/          React UI (login, DMs, computer panel, streaming)
    dist/         production build (`bun run build`)
  cli/
    swarm_cli.py  JSON in/out CLI: register, post, react, history, agents, create/patch agent
  tests/          pytest + httpx; Groq mocked
  docs/
    DEPLOY.md     docker compose deployment guide
  Dockerfile
  docker-compose.yml
  PROBLEM.md
  PROJECT.md
  SPEC.md
  VISION.md      product thesis, competitive map, V3 scope, demo script
  PROMPTS.md
  README.md
```

## Phase status

| Phase | Name                      | Status | What shipped |
|-------|---------------------------|--------|-------------------|
| 0     | Foundation                | ✅ done | relay, channels, single agent, mention trigger, CLI |
| 1     | Hardening                 | ✅ done | token auth on every write, impersonation blocked, per-handle rate limit |
| 2     | Agent tool calling        | ✅ done | `read_only_shell` + `search_channel_history` via Groq function calling, 3-call cap, tool calls posted as audit messages |
| 3     | Threads & reactions       | ✅ done | `parent_id` threading, idempotent reactions, both broadcast over WS |
| 4     | Multi-agent personas      | ✅ done | `agents` table, seeded `swarm` + `ledger`, ordered sequential replies on multi-mention |
| 5     | Observability             | ✅ done | Langfuse trace per generation, tagged by channel + agent, silent no-op if unconfigured |
| 6     | Semantic history (opt.)   | ⛔ intentionally skipped | keyword search hasn't been shown insufficient — building Qdrant now would be exactly the speculative work Phase 6's own spec forbids |
| 7     | Deployment                | ✅ done (verified) | Dockerfile, compose, DEPLOY.md — `docker compose build && up -d` run live; `/api/channels` ok; SQLite volume survived down/up; sandbox tool ran in-container at `/tmp/swarm-sandbox` |
| 8     | Reliability + streaming UI | ✅ done | WS `last_seen_id` catch-up, history `before_id` pagination, `GET /api/messages/{id}/thread`, AsyncGroq token streaming, pytest, React UI with thread panel, mention picker, reconnect, mobile layout |
| 9     | Custom agents + memory     | ✅ done | Agent create/edit UI + GET/PATCH, per-agent harness (window, tool toggles), `agent_memory` notes via remember/recall, context injects notes + rolling summary, classified errors, one retry, optional OpenRouter |
| 10    | Grok Bot teammates         | ✅ done | Named jobs, 1:1 DMs (no @ needed), job templates, skills, interval routines, approvals, shared workspace/"computer" panel, bot-to-bot handoff, status chips |
| 11    | V3 onboarding + demo       | ✅ done | Register → job picker → create Bot → 1:1 with suggested prompt; `SWARM_DEMO=1` mock replies + `#general` seed thread |

## What changed from the original plan

- **Auth token format** ended up as a single composite string
  (`handle:raw`) rather than a bare opaque token, so REST and WS auth
  share one code path (`parse_token`) without a second lookup table.
  Not in the original spec — added during Phase 1 implementation
  because the alternative (separate handle + token fields everywhere)
  was more surface area for the same guarantee.
- **Multi-agent ordering bug caught and fixed during build**: firing
  each mentioned agent as its own `asyncio.create_task` let them race
  and reply out of order, violating FR4.2. Fixed by running all
  mentioned agents sequentially inside one task. Documented here
  because it's the kind of bug that only shows up under concurrent
  load, not in a single-agent test — worth remembering if
  agent-to-agent triggering ever gets added. Phase 10 did add
  handoffs: an agent reply that `@mentions` another Bot triggers
  that Bot, depth-capped at 2, still sequential inside the task.
- **`SWARM_DB_PATH` env var** added, not in the original spec, so the
  Dockerfile can point SQLite at a mounted volume without a code
  change. Small addition, but it's why Phase 7 didn't need to touch
  `db.py`'s query logic at all.
- **`ensure_schema()`** is the first real schema upgrade path. Phase 9
  added columns on `agents` and the `agent_memory` table. Existing
  SQLite files get `ALTER TABLE` on startup via `PRAGMA table_info`
  rather than Alembic. Documented because a later column type change
  would still need a real migration tool — this only covers additive
  columns and new tables.

## Explicit risks (updated)

- **In-memory WS hub and rate-limit table don't survive a restart or
  scale past one process.** Still true, still fine for a portfolio
  demo. Unchanged from V1.
- **No admin role.** Anyone with a registered handle can create or
  edit an agent persona. Named explicitly in `SPEC.md` as a known
  gap — not closed in Phase 9. Close it if this ever needs more than
  one trusted user.
- **No agent delete.** Mentions and history would dangle. Named gap.
- **Docker verified locally, not on a VPS.** `docker compose build &&
  up -d` succeeded against Docker Desktop 29.6.1: `/api/channels`
  responded, `swarm-data` kept `persist-check-*` across a down/up
  cycle, and `_run_shell_tool('ls')` ran as user `swarm` with cwd
  `/tmp/swarm-sandbox`. That is not the same as a remote VPS deploy.
- **No conversation-level rate limit on tool calls across agents.**
  The 3-call cap is per single `@mention` trigger. Mentioning two
  agents in one message can still produce up to 6 tool calls total.
  Acceptable at current scale; revisit if the sandbox shell ever does
  anything expensive enough to matter.
- **The shared computer is a sandbox directory, not a VM.** Browser
  sessions, 24/7 cloud work with the laptop closed, and teach-by-
  demonstration are out of scope. Files and logins placed there are
  visible to every Bot on the account — same boundary as Grok Bot's
  docs, without the cloud isolation story.

## V3 roadmap (product-complete, not Buzz-complete)

Full detail in `VISION.md`. Summary:

| Item | Gated on |
|------|----------|
| V3.1 Admin role | Multi-user deploy or open endpoint abuse |
| V3.2 Onboarding flow | Always (UI polish) |
| V3.3 Agent teams (`@team-name`) | Handoffs insufficient for "run the pod" |
| V3.4 Human DMs | Small team needs private human chat |
| V3.5 Semantic history (Phase 6) | Keyword search fails in daily use |
| V3.6 Audit export | Demo / compliance story |
| V3.7 Demo mode (mock LLM) | Portfolio reviewers without API keys |

Permanently out of V3: Nostr signing, git hosting, Slack connectors,
cloud VM, browser computer-use, container-per-agent isolation.

## Natural next steps (still gated)

Same rule as Phase 6 — don't build speculatively:

1. **V3.2 onboarding + V3.7 demo mode** — highest leverage polish, no new primitives.
2. **V3.1 admin auth** — when more than one trusted user exists.
3. Semantic history — only if keyword search has proven insufficient.
4. Agent delete — only if dangling history is actually a problem.
5. Real computer-use — only if sandbox has proven insufficient.

## Competitive positioning (web research, Aug 2026)

| Project | Layer | swarm difference |
|---------|-------|------------------|
| block/buzz | Full workspace + Nostr | swarm = chat-native agents only, 10-min Docker deploy |
| pelago-labs/slackhive | Slack + Boss + specialists | swarm owns the workspace; no Slack OAuth |
| geekforbrains/operator | Slack agents + spawn | swarm has routines/approvals/computer in-app |
| linxidnju/OpenTag | Slack gateway + runtimes | swarm is runtime + UI, not a gateway |
| xAI Grok Bot | Cloud job Bots | same job/1:1/routine model, self-hosted |
| Orloj / Clawix / GAIA | Orchestration runtime | different layer; potential integration later |

## Success criteria

**V2 (met):** `SPEC.md` matches running code; extend without asking the author.

**V3 (target):** Five-minute demo (`VISION.md` script); reviewer places swarm vs Buzz vs SlackHive in one sentence; small-team trust (admin-gated Bot creation).
