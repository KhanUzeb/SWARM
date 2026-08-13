# SPEC.md — swarm

Technical contract for what's actually running (V2 + Phase 8). If code
and this document disagree, one of them is wrong — file it as a bug
against whichever is easier to fix correctly, not whichever is easier
to leave broken.

---

## 1. Data model (current)

### `channels`

| column     | type  | notes |
|------------|-------|-------|
| id         | TEXT  | PK, slug derived from name (`lower`, spaces→hyphens) |
| name       | TEXT  | unique, display name |
| topic      | TEXT  | default `''` |
| created_at | REAL  | unix timestamp |

### `messages`

| column      | type    | notes |
|-------------|---------|-------|
| id          | INTEGER | PK, autoincrement |
| channel_id  | TEXT    | FK → channels.id |
| parent_id   | INTEGER | FK → messages.id, nullable — threading |
| author      | TEXT    | for `human`: must equal the authenticated handle. For `agent`: the agent's name. For `system`: the agent name that triggered the tool call |
| author_kind | TEXT    | `human` \| `agent` \| `system` |
| body        | TEXT    | max 8000 chars, enforced at the pydantic layer |
| created_at  | REAL    | unix timestamp |

Indexed on `(channel_id, created_at)` and `(parent_id)`.

### `users`

| column     | type | notes |
|------------|------|-------|
| handle     | TEXT | PK |
| token_hash | TEXT | SHA-256 of the raw token half, never the raw token itself |
| created_at | REAL | |

### `reactions`

| column     | type    | notes |
|------------|---------|-------|
| message_id | INTEGER | FK → messages.id |
| author     | TEXT    | must equal the authenticated handle on write |
| emoji      | TEXT    | free-text, capped at 8 chars |
| created_at | REAL    | |

`UNIQUE(message_id, author, emoji)` — same tuple twice is a no-op, not
a duplicate row or an error.

### `agents`

| column         | type | notes |
|----------------|------|-------|
| name           | TEXT | PK, mention key (`@<name>`) |
| system_prompt  | TEXT | |
| model          | TEXT | Groq model string |
| channel_scope  | TEXT | nullable FK-ish → channels.id. NULL = every channel |
| created_at     | REAL | |

Seeded on first run: `swarm` (generalist, unscoped) and `ledger`
(decision-summarizer, unscoped).

### Not built

No `threads` table separate from `messages.parent_id` — flat storage
with a nullable self-reference is sufficient; `GET /api/messages/{id}/thread`
returns one level (the parent plus rows whose `parent_id` equals that
id). No migrations system — the schema has only grown via
`CREATE TABLE IF NOT EXISTS`, which is fine until a column needs to
change type or move, at which point this needs an actual migration
tool, not another `ALTER TABLE` bolted into `init_db`. Admin role for
`POST /api/agents` is still a named gap — see `PROJECT.md`.

---

## 2. Auth model

Register once per handle: `POST /api/register` returns a composite
token `"<handle>:<raw>"`. Only the SHA-256 hash of `<raw>` is stored.
The composite form exists so REST and WS share one parsing path
(`parse_token` in `main.py`) instead of needing the handle supplied
separately from the token on every call.

- **REST writes**: `Authorization: Bearer <handle>:<raw>` header,
  required on every POST that creates or modifies data. Read endpoints
  (`GET /api/channels`, `GET /api/channels/{id}/messages`,
  `GET /api/messages/{id}/thread`, `GET /api/agents`) are
  unauthenticated by design — there's no private data in this system
  yet.
- **WS writes**: the first frame after connecting must be
  `{"token": "<handle>:<raw>", "last_seen_id": null}`. `last_seen_id`
  is optional. Invalid or missing token closes the connection with
  code `4001`. All subsequent message frames use the handle
  established at handshake — a client cannot send a different
  `author` over an open connection.
- **Impersonation check**: on REST message/reaction posts, the
  `author` field in the body must equal the authenticated handle or
  the request is rejected with `403`. This is redundant with the WS
  path (which never lets the client set `author` at all) but REST
  still accepts an explicit `author` field, so the check is load-
  bearing there.
- **Rate limit**: 500ms minimum between writes, tracked per handle in
  an in-memory dict (`_last_write` in `main.py`). Violating it returns
  `429` (REST) or a `{"type": "error", "detail": "slow down"}` frame
  (WS) — the WS connection stays open, the message is just dropped.
- **No admin role.** `POST /api/agents` requires only a valid token,
  not any elevated permission. Named gap, not a bug — see
  `PROJECT.md`.

---

## 3. REST API (current)

### `POST /api/register`
```json
// request
{"handle": "uzeb"}
// response 200
{"handle": "uzeb", "token": "uzeb:2y0-_JDXp9NN..."}
// response 409 if handle taken
{"detail": "handle already registered"}
```

### `GET /api/channels`
No auth required.
```json
[{"id": "general", "name": "general", "topic": "wherever, whatever", "created_at": 1786353998.09}]
```

### `POST /api/channels`
Auth required.
```json
// request
{"name": "release-planning", "topic": "optional"}
// response 200
{"id": "release-planning", "name": "release-planning", "topic": "optional"}
// 409 if id exists, 401 if unauthenticated, 429 if rate limited
```

### `GET /api/channels/{channel_id}/messages?limit=50&before_id=`
No auth required. Returns messages oldest-first, each with a
`reactions` array embedded. `limit` defaults to 50, capped at 100.
If `before_id` is set, returns the page of messages with `id < before_id`
(still oldest-first in the JSON array) — used to load earlier history.
```json
[{
  "id": 1, "channel_id": "general", "parent_id": null,
  "author": "uzeb", "author_kind": "human",
  "body": "shipping the fix", "created_at": 1786527277.23,
  "reactions": [{"author": "uzeb", "emoji": "🔥", "created_at": 1786527277.28}]
}]
```
404 if channel doesn't exist.

### `POST /api/channels/{channel_id}/messages`
Auth required. `author` must match the authenticated handle.
`parent_id` is optional; if set, the parent message must exist.
```json
// request
{"author": "uzeb", "body": "shipping the fix", "author_kind": "human", "parent_id": null}
// response 200 — the persisted message (no "reactions" field on write, only on read)
{"id": 1, "channel_id": "general", "parent_id": null, "author": "uzeb",
 "author_kind": "human", "body": "shipping the fix", "created_at": 1786527277.23}
```
404 channel or parent missing, 403 author mismatch, 429 rate limited.
Triggers the same broadcast and agent-mention check as a WS send.

### `POST /api/messages/{message_id}/reactions`
Auth required, `author` must match. Idempotent — same
(message, author, emoji) twice is still `204`, not an error.
```json
// request
{"author": "uzeb", "emoji": "🔥"}
// response: 204 No Content
```
404 if message doesn't exist, 403 author mismatch.

### `GET /api/messages/{message_id}/thread`
No auth required. One-level thread: the parent message plus every
reply whose `parent_id` equals that id, oldest-first, each with
`reactions` embedded.
```json
{
  "parent": { "id": 1, "parent_id": null, "body": "...", "reactions": [] },
  "replies": [
    { "id": 2, "parent_id": 1, "body": "...", "reactions": [] }
  ]
}
```
404 if the message doesn't exist.

### `GET /api/status`
No auth. Reports whether `GROQ_API_KEY` is loaded — boolean only, never the key.
```json
{"groq": true}
```
The app loads `.env` from the project root and from `backend/.env` on import (process env still wins). If this is `false` after you set a key, restart uvicorn — `--reload` does not pick up `.env` edits.

### `GET /api/agents?channel_id=<optional>`
No auth. Without `channel_id`, returns every agent. With it, returns
agents scoped to that channel plus unscoped (global) agents.

### `POST /api/agents`
Auth required (any valid token — no admin check, see section 2).
```json
// request
{"name": "ledger", "system_prompt": "...", "model": "llama-3.3-70b-versatile", "channel_scope": null}
// response 200 — the created agent
// 409 if name already registered
```

---

## 4. WebSocket protocol (current)

`ws://host/ws/{channel_id}`

**Handshake** (required, first frame):
```json
{"token": "handle:raw", "last_seen_id": 42}
```
`last_seen_id` is optional. Failure → close code `4001`. Channel not
found → close code `4004` (checked before accept, so this happens even
before the handshake).

After a successful handshake the server delivers catch-up (if
`last_seen_id` was set): every message in that channel with
`id > last_seen_id`, oldest-first, as normal `message` events, each
with a `reactions` array. Then it switches to live broadcast.

**Client → server**, after handshake, on send:
```json
{"body": "message text", "parent_id": null}
```
`author` is never accepted from the client here — it's the handle
established at handshake. This is stricter than the REST path
intentionally: REST needs an explicit `author` field to validate
against, WS doesn't need one to exist at all.

**Server → client** event types:
```json
{"type": "message", "message": { ...same shape as REST message... }}
{"type": "typing", "author": "swarm"}
{"type": "reaction", "message_id": 1, "author": "uzeb", "emoji": "🔥"}
{"type": "error", "detail": "slow down"}
{"type": "agent_stream_start", "author": "swarm"}
{"type": "agent_token", "author": "swarm", "delta": "..."}
```

`agent_stream_start` / `agent_token` are live tokens for the agent's
final text completion. They are not persisted. The completed reply is
persisted once and then broadcast as a `message` event; clients replace
the streaming placeholder with that row.

Live `message` events from a human/agent/system write may omit
`reactions` (empty). Catch-up `message` events include `reactions`.

---

## 5. Agent contract (current)

- **Trigger**: any human message where `@<agent_name>` appears
  case-insensitively, matched as a whole word (`re.search(r"@name\b")`
  — `@swarmy` does not trigger `@swarm`). Agents are looked up scoped
  to the channel the message was posted in.
- **Multiple mentions**: if a message mentions more than one agent,
  they reply **sequentially, in the order they're mentioned in the
  text** — not the order they're registered, not concurrently. This
  was a real bug during V2 build (see PROJECT.md "what changed") —
  the fix runs all mentioned agents inside a single background task
  rather than one task per agent.
- **Context window**: last `HISTORY_WINDOW` (12) messages in the
  channel, oldest first. `system`-kind messages (tool-call audit logs)
  are excluded from what's sent to the model — they're for humans
  reading the channel, not context for the agent itself. The agent's
  own prior messages map to the `assistant` role; everything else to
  `user`, prefixed with the author's name.
- **Model**: per-agent `model` column in the `agents` table (default
  `llama-3.3-70b-versatile`), via Groq.
- **Tools available**: `read_only_shell(command)` and
  `search_channel_history(query)`, exposed via Groq's function-calling
  API (`tools` + `tool_choice="auto"`) **only when the latest human
  message looks like a file/history request**. Greetings (`@swarm hi`)
  get a text-only completion — small models otherwise burn the 3-call
  cap on `ls`/`dir`.
  - `read_only_shell` runs in a sandboxed working directory
    (`SWARM_SANDBOX_DIR`; default `/tmp/swarm-sandbox`, or `%TEMP%\swarm-sandbox`
    on Windows), 10s timeout, output capped at 4000 chars, minimal `PATH`
    env (Unix `/usr/bin:/bin`, Windows `System32`). **This is not a
    real network-isolation guarantee** — it's a working-directory and
    timeout sandbox, not a container or seccomp boundary. Don't treat
    it as one.
  - `search_channel_history` does a `LIKE %query%` substring match
    against that channel's messages. Not semantic — see Phase 6.
  - Capped at 3 tool calls per single agent trigger (not per message —
    if two agents are mentioned, each gets its own cap of 3).
  - Every tool call is persisted as a `system`-kind message
    (`"<agent> ran: <tool>(<args>) -> <truncated result>"`) and
    broadcast before the agent's final reply is posted. This is the
    audit-trail requirement — not optional logging, it's how a human
    watching the channel sees what the agent actually did.
- **Streaming**: each Groq round is streamed via AsyncGroq
  (`stream=True`). If a round contains tool calls, tokens are not
  forwarded and the round is treated as a tool round (same 3-call cap).
  The first content-only round broadcasts `agent_stream_start` then
  `agent_token` frames; the assembled reply is persisted once and
  broadcast as `message`. Failures still become `[agent error: ...]`
  posted as a normal agent message — they are not streamed.
- **Tracing**: if `LANGFUSE_PUBLIC_KEY` and `LANGFUSE_SECRET_KEY` are
  both set, every generation call is wrapped in a Langfuse trace
  (`swarm.agent.<name>`), tagged with `channel_id` and agent name in
  metadata, recording latency and token usage per round. If either env
  var is missing, tracing is skipped entirely — `agent.py` has no hard
  Langfuse dependency at runtime.
- **Env loading**: `backend/__init__.py` loads project-root `.env` then
  `backend/.env` via python-dotenv (`override=False`, so real process
  env / Docker `env_file` still win). Tests set `PYTHON_DOTENV_DISABLED`.
- **Failure mode**: any exception — missing `GROQ_API_KEY`, API error,
  tool execution error — is caught and returned as the reply text
  itself (`[agent error: ...]`), posted as a normal agent message. The
  relay never 500s because an agent failed. Verified during build:
  killing `GROQ_API_KEY` mid-test produced a clean in-channel error on
  both agents mentioned in the same message, server stayed up.

---

## 6. Functional requirements — status

All FR numbers below are implemented as of Phase 8 unless noted.

| FR | Description | Status |
|---|---|---|
| FR1.1 | Bearer token required on every write | ✅ |
| FR1.2 | Rate limit, 500ms/handle, clear error on violation | ✅ |
| FR1.3 | Body length enforced server-side, structured error | ✅ |
| FR1.4 | No bare 500s leaking stack traces | ✅ (all known error paths return structured JSON) |
| FR2.1 | Shell + history-search tools via Groq function calling | ✅ |
| FR2.2 | Every tool call posted as a channel system message | ✅ |
| FR2.3 | 10s tool timeout, 3-call cap per trigger | ✅ |
| FR3.1 | `parent_id` threading | ✅ |
| FR3.2 | Idempotent reactions | ✅ |
| FR4.1 | `agents` table replaces hardcoded persona | ✅ |
| FR4.2 | Multi-mention replies in order, not concurrent | ✅ (bug found and fixed during build) |
| FR5.1 | Langfuse trace per generation call, incl. tool calls | ✅ |
| FR5.2 | Traces tagged with channel_id (and agent name) | ✅ |
| FR8.1 | WS catch-up via optional `last_seen_id` in handshake | ✅ |
| FR8.2 | Streaming final agent reply over WS token-by-token | ✅ |
| FR8.3 | Message history pagination via `before_id` | ✅ |
| FR8.4 | `GET /api/messages/{id}/thread` one-level thread | ✅ |

---

## 7. Non-functional requirements

| Requirement | Target | Notes |
|---|---|---|
| Message broadcast latency | < 100ms local | in-memory hub, no network hop beyond the DB write — unchanged from V1, not re-benchmarked |
| Agent reply latency | best-effort | tool-calling rounds stay non-streaming; final tokens stream over WS; no p99 target set |
| Concurrent connections per channel | untested beyond ~5 in manual testing | in-memory `set[WebSocket]`, no load test exists |
| DB durability | SQLite file on a Docker named volume | acceptable through Phase 7; revisit for multi-host durability if ever needed |
| Docker build | verified locally via `docker compose build && up -d` | `/api/channels` 200, named volume persists, sandbox cwd `/tmp/swarm-sandbox`; not a remote VPS deploy |

---

## 8. Explicit non-requirements

Unchanged from V1: no Nostr/event-signing, no git hosting, no
canvas/media comments, no huddle/voice, no multi-tenant hosting, no
vector search (Phase 6, intentionally unbuilt), no admin role (named
gap, still gated).
