# PROMPTS.md — swarm

Phases 1–5, 7, and 8 are built (see PROJECT.md). This file records
what actually got built per phase, and keeps paste-ready prompts for
the remaining gated items (admin role, semantic history).

If you're extending this repo with an agentic coding tool, point it at
`PROBLEM.md`, `PROJECT.md`, and `SPEC.md` first either way.

---

## Phases 1–5, 7, 8 — what shipped (reference, not re-runnable as-is)

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

---

## Open — gated leftovers

Admin role and Phase 6 (semantic history) are still gated below.
Do not build them speculatively.

---

## Remaining candidates

Same rule as Phase 6 had: **don't build any of these speculatively.**
Each one is listed with the condition that would justify it. If you're
an agent reading this and none of the conditions have actually
happened, say so and stop.

```
Read PROBLEM.md, PROJECT.md, and SPEC.md. Before implementing anything
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
```

---

## How to use this file

Same principle as before: if implementing something here surfaces a
spec gap, fix SPEC.md first, then the code. The spec is the source of
truth, not whatever got built.
