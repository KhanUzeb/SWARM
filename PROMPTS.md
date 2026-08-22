# PROMPTS.md — swarm

Phases 1–5 and 7–10 are built (see PROJECT.md). This file records
what actually got built per phase, and keeps paste-ready prompts for
the remaining gated items (admin role, semantic history, agent delete).

If you're extending this repo with an agentic coding tool, point it at
`VISION.md`, `PROBLEM.md`, `PROJECT.md`, and `SPEC.md` first either way.

---

## Phases 1–5, 7–10 — what shipped (reference, not re-runnable as-is)

These prompts describe what was actually implemented. They're kept
for context, not meant to be re-run — running them again against
already-built code would just ask an agent to redo work that exists.
If you're rebuilding this from scratch, they're still accurate as a
build order.

**Phase 1 (Hardening)**: `users` table, `POST /api/register` returning
a composite `handle:raw` token, auth required on every write path
(REST header + WS handshake frame), impersonation check (`author` must
equal authenticated handle), 500ms/handle rate limit. CLI updated to
carry `SWARM_TOKEN`.

**Phase 2 (Tool calling)**: `read_only_shell` and
`search_channel_history` tools wired through Groq function calling,
sandboxed working directory, 10s timeout, 3-call cap per trigger,
every tool call posted as an audit `system` message before the final
reply.

**Phase 3 (Threads & reactions)**: `parent_id` on messages,
`reactions` table with a uniqueness constraint making repeat reactions
a no-op, both broadcast over WS as new event types.

**Phase 4 (Multi-agent personas)**: `agents` table replacing the
hardcoded persona, seeded with `swarm` and `ledger`. Building this
surfaced a real ordering bug — concurrent `asyncio.create_task` per
mentioned agent let replies race — fixed by running mentioned agents
sequentially inside one task. See SPEC.md section 5 for the current
(correct) contract.

**Phase 5 (Observability)**: Langfuse trace wrapping every generation
call, degrading to a silent no-op when `LANGFUSE_PUBLIC_KEY` /
`LANGFUSE_SECRET_KEY` aren't set — verified by booting the server with
neither present.

**Phase 7 (Deployment)**: Dockerfile (non-root user, `SWARM_DB_PATH`
and `SWARM_SANDBOX_DIR` env-driven), `docker-compose.yml` with a named
volume, `docs/DEPLOY.md`. Live-verified: image builds, container
serves `/api/channels` on `:8000`, SQLite on `swarm-data` survives
`docker compose down` / `up`, `_run_shell_tool` cwd is
`/tmp/swarm-sandbox`. DEPLOY.md matched the live cycle; no doc fix
required. Sandbox was checked in-container (deterministic) rather than
via an `@swarm` mention, which depends on Groq choosing the tool.

**Phase 8 (Reliability + streaming UI)**: SPEC updated first. WS
handshake accepts optional `last_seen_id` and delivers missed messages
before live broadcast. `GET /api/channels/{id}/messages?before_id=`
paginates older history. `GET /api/messages/{id}/thread` returns a
one-level thread. Agent generation uses `AsyncGroq` with `stream=True`;
tool-call rounds do not forward tokens; the content round emits
`agent_stream_start` / `agent_token`, then the assembled reply is
persisted and broadcast as `message`. Frontend split into
`index.html` / `styles.css` / `app.js` (still no build step): thread
side panel, `@` mention picker, auto-reconnect, auto-login, in-app
modals, mobile sidebar, loading/empty states. pytest + httpx cover
auth, reactions, pagination, threads, WS 4001, catch-up, and a mocked
streaming mention. Admin role was
not in this phase.

**Phase 9 (Custom agents + memory)**: SPEC updated first. `ensure_schema()`
adds harness columns (`history_window`, `max_tool_calls`, `tools`) and
`agent_memory`. `GET`/`PATCH /api/agents/{name}` plus create-agent UI
and CLI. `remember`/`recall` tools write keyword notes (not vectors).
Context injects recent notes + one rolling summary. Groq errors are
classified; 429/5xx/timeout retry once; `OPENROUTER_API_KEY` is an
optional fallback. Partial streams persist with a cutoff note. Vanilla
UI: agent panel, clearer empty/login/status, collapsible tool rows.

**Phase 10 (Grok Bot teammates)**: SPEC updated first against
https://x.ai/bot and https://docs.x.ai/grok-bot/overview. Named `job`
+ `status` on agents, auto-created `dm-<name>` 1:1 channels that
trigger without `@mention`, job templates, skills (`/` invoke +
`save_skill`), interval routines into the 1:1, `request_approval` with
Allow once / Deny, shared sandbox listed as the computer, bot-to-bot
handoff after an agent reply. UI: bot roster, computer panel, darker
theme. Explicitly not a cloud VM or browser computer-use.

---

## Open — gated leftovers + V3

Admin role, Phase 6 (semantic history), and agent delete remain gated
below. **V3 scope** (onboarding, demo mode, agent teams, human DMs,
audit export) is defined in `VISION.md` — build only when each item's
condition is met. Do not build speculatively.

---

## V3 candidates (from VISION.md)

```
Read VISION.md, PROBLEM.md, PROJECT.md, and SPEC.md. Before implementing
any V3 item, check its condition in VISION.md. If not met, report and stop.

V3.1 Admin role — same as item 1 below.
V3.2 Onboarding flow — shipped (UI): after register, job template picker,
create first Bot, land in 1:1 with suggested prompt.
V3.7 Demo mode — shipped: SWARM_DEMO=1 mock replies + #general seed thread.
```

---

## Remaining candidates (pre-V3)

Same rule as Phase 6 had: **don't build any of these speculatively.**
Each one is listed with the condition that would justify it. If you're
an agent reading this and none of the conditions have actually
happened, say so and stop.

```
Read VISION.md, PROBLEM.md, PROJECT.md, and SPEC.md. Before implementing anything
below, check whether its stated condition has actually occurred. If
not, report that back and don't implement it.

1. Admin role for POST /api/agents
   Condition: this deployment ever has more than one person who should
   be able to register new agent personas, or a stranger actually
   abuses the open endpoint.
   If justified: add a `role` column to `users` (default 'member'),
   gate agent creation on `role = 'admin'`, add a one-time bootstrap
   path to promote the first registered user to admin.

2. Phase 6 (semantic history) — unchanged condition from before:
   only if keyword search has actually proven insufficient.

3. Agent delete
   Condition: dangling agent names in history are actually a problem
   (renames, retired personas, or a user asking to remove one).
   If justified: soft-delete or rename-in-place so old messages still
   resolve; do not hard-delete rows that messages still point at.

4. Cloud computer / browser computer-use
   Condition: the shared sandbox has actually been shown insufficient
   for the jobs people run here.
   If justified: a real isolated workspace, not a second chat UI over
   the same directory.
```

---

## How to use this file

Same principle as before: if implementing something here surfaces a
spec gap, fix SPEC.md first, then the code. The spec is the source of
truth, not whatever got built.
