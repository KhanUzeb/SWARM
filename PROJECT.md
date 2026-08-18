# PROJECT.md — swarm

A mini Buzz. Chat workspace where LLM agents are channel members, not
a sidebar. See `PROBLEM.md` for why, `SPEC.md` for the technical
contract, `PROMPTS.md` for what built each phase and what's left.

**Status: V2 + Phase 10 shipped.** Phases 1–5, 7, 8, 9, and 10 are
built. Phase 6 is deliberately not built — see its entry below. Admin
role for `POST /api/agents` remains a named gap. Agent delete is not
built. There is no cloud VM; the "computer" is the shared sandbox.

## Stack

| Layer      | Choice                          | Why |
|------------|----------------------------------|-----|
| Backend    | FastAPI + Uvicorn                | async-native, WS support built in, matches VOX/AXIOM stack |
| DB         | SQLite via `aiosqlite`           | zero-ops for a portfolio project; swap to Postgres if this ever needs concurrent writers at scale |
| Realtime   | Native WebSocket, in-memory hub  | one process, one hub — no Redis pub/sub needed at this scale |
| LLM        | Groq (primary), OpenRouter fallback | Groq first; one retry then OpenRouter if `OPENROUTER_API_KEY` is set |
| Frontend   | Vanilla HTML/CSS/JS, no framework | no build step; Slack/Discord-style dark UI with a thread panel |
| Tracing    | Langfuse                         | reuses the eval/observability pattern from VERIS; degrades to no-op if unconfigured |
| Deployment | Docker + docker-compose          | single VPS, named volume for the SQLite file |

## Repo layout

```
swarm/
  backend/
    main.py       FastAPI app: REST + WS routes, auth, rate limiting, agent trigger
    db.py         schema + ensure_schema() + agent_memory
    agent.py      multi-persona LLM responder, harness, memory tools, streaming, Langfuse
    jobs.py      Grok Bot-style job templates for create-Bot
    models.py     pydantic schemas
  frontend/
    index.html    markup
    styles.css    dark Grok-like theme, computer panel, bot roster
    app.js        auth, WS, DMs, skills/routines/approvals, streaming
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
| 8     | Reliability + streaming UI | ✅ done | WS `last_seen_id` catch-up, history `before_id` pagination, `GET /api/messages/{id}/thread`, AsyncGroq token streaming, pytest, vanilla UI split into html/css/js with thread panel, mention picker, reconnect, mobile layout |
| 9     | Custom agents + memory     | ✅ done | Agent create/edit UI + GET/PATCH, per-agent harness (window, tool toggles), `agent_memory` notes via remember/recall, context injects notes + rolling summary, classified errors, one retry, optional OpenRouter |
| 10    | Grok Bot teammates         | ✅ done | Named jobs, 1:1 DMs (no @ needed), job templates, skills, interval routines, approvals, shared workspace/"computer" panel, bot-to-bot handoff, status chips |

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

## Natural next steps (still gated)

These remain gated the same way Phase 6 was — don't build them
speculatively:

1. Admin auth for `POST`/`PATCH /api/agents` (closes the named gap above).
2. Semantic history (Phase 6) — only if keyword search has actually
   proven insufficient.
3. Agent delete — only if dangling history is actually a problem.
4. Real computer-use (browser + cloud VM) — only if the sandbox
   workspace has actually been shown insufficient for the jobs people
   run here.

## Success criteria for the project as a whole

Unchanged from V1: could someone read `SPEC.md` cold and extend this
without asking me anything? The spec describes what's actually
running, not what was planned — that's the bar met.
