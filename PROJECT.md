# PROJECT.md — swarm

A mini Buzz. Chat workspace where LLM agents are channel members, not
a sidebar. See `PROBLEM.md` for why, `SPEC.md` for the technical
contract, `PROMPTS.md` for what built each phase and what's left.

**Status: V2 shipped.** Phases 1–5 and 7 are built and tested. Phase 6
is deliberately not built — see its entry below.

## Stack

| Layer      | Choice                          | Why |
|------------|----------------------------------|-----|
| Backend    | FastAPI + Uvicorn                | async-native, WS support built in, matches VOX/AXIOM stack |
| DB         | SQLite via `aiosqlite`           | zero-ops for a portfolio project; swap to Postgres if this ever needs concurrent writers at scale |
| Realtime   | Native WebSocket, in-memory hub  | one process, one hub — no Redis pub/sub needed at this scale |
| LLM        | Groq (primary)                   | consistent with existing infra layer; OpenRouter fallback not yet wired |
| Frontend   | Vanilla HTML/CSS/JS, no framework | one page, no build step, Slack/Discord-style dark UI |
| Tracing    | Langfuse                         | reuses the eval/observability pattern from VERIS; degrades to no-op if unconfigured |
| Deployment | Docker + docker-compose          | single VPS, named volume for the SQLite file |

## Repo layout

```
swarm/
  backend/
    main.py       FastAPI app: REST + WS routes, auth, rate limiting, agent trigger
    db.py         schema + async queries (channels, messages, users, reactions, agents)
    agent.py      multi-persona LLM responder, tool calling, Langfuse tracing
    models.py     pydantic schemas
  frontend/
    index.html    single-page Slack/Discord-style UI: auth, threads, reactions, agent sidebar
  cli/
    swarm_cli.py  JSON in/out CLI: register, post, react, history, agents
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
  load, not in a single-agent test — worth remembering for Phase 8 if
  agent-to-agent triggering ever gets added.
- **`SWARM_DB_PATH` env var** added, not in the original spec, so the
  Dockerfile can point SQLite at a mounted volume without a code
  change. Small addition, but it's why Phase 7 didn't need to touch
  `db.py`'s query logic at all.

## Explicit risks (updated)

- **In-memory WS hub and rate-limit table don't survive a restart or
  scale past one process.** Still true, still fine for a portfolio
  demo. Unchanged from V1.
- **No admin role.** Anyone with a registered handle can create a new
  agent persona via `POST /api/agents`. Named explicitly in `SPEC.md`
  Phase 4 as a known gap, not fixed in V2 — closing it is Phase 8
  work if this ever needs more than one trusted user.
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

## Natural next steps (Phase 8, not scoped yet)

Not written up as formal prompts because none of these are justified
by anything that's actually happened yet — they're here so the list
exists somewhere before it's needed:

1. Admin auth for `POST /api/agents` (closes the named gap above).
2. WS reconnect with `last_seen_id` so a dropped client doesn't need a
   full REST re-fetch to catch up — flagged as a maybe in the original
   SPEC.md, still a maybe.
3. Streaming agent replies over WS token-by-token, if Groq latency or
   tool-calling rounds ever make the current "wait for the full reply"
   UX feel slow in practice.

## Success criteria for the project as a whole

Unchanged from V1: could someone read `SPEC.md` cold and extend this
without asking me anything? V2's `SPEC.md` describes what's actually
running, not what was planned — that's the bar met.
