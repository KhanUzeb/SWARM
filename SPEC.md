# SPEC.md — swarm

Technical contract for what's actually running (V2 + Phase 10). If code
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
| kind       | TEXT  | `room` (shared channel) or `dm` (a Bot's 1:1). default `room` |
| owner_agent | TEXT | for `dm`: the Bot that always hears this channel. NULL for rooms |

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

| column          | type    | notes |
|-----------------|---------|-------|
| name            | TEXT    | PK, mention key (`@<name>`) |
| system_prompt   | TEXT    | |
| model           | TEXT    | Groq (or OpenRouter-mapped) model string |
| channel_scope   | TEXT    | nullable FK-ish → channels.id. NULL = every channel |
| created_at      | REAL    | |
| history_window  | INTEGER | last N channel messages injected; default 12, cap 50 |
| max_tool_calls  | INTEGER | cap per trigger; default 3, cap 8 |
| tools           | TEXT    | JSON array of allowed tool names |
| job             | TEXT    | primary job title; default `Teammate` |
| status          | TEXT    | `idle` \| `working` \| `needs_approval` |

Allowed tool names: `read_only_shell`, `search_channel_history`,
`remember`, `recall`, `list_workspace`, `write_workspace`,
`save_skill`, `request_approval`. API responses parse `tools` as a
JSON array, not a raw string. List/get also include `dm_channel_id`
(`dm-<name>`).

Seeded on first run: `swarm` (job `Generalist`, unscoped, all eight
tools) and `ledger` (job `Decision log`, unscoped, no shell/workspace
write — `search_channel_history`, `remember`, `recall`). Creating an
agent also creates its 1:1 channel `dm-<name>` (`kind=dm`,
`owner_agent=<name>`). Existing databases get those DMs from
`ensure_schema()`. Deleting an agent is not supported (history would
dangle) — named gap.

Existing databases that predate these columns are upgraded in
`ensure_schema()` via `PRAGMA table_info` + `ALTER TABLE`, not Alembic.
`ledger`'s tools are rewritten to the no-shell default only when the
`tools` column is first added. Pre-Phase-10 `swarm` rows keep whatever
`tools` JSON they already had; a fresh DB gets the eight-tool default.

### `agent_memory`

| column     | type    | notes |
|------------|---------|-------|
| id         | INTEGER | PK, autoincrement |
| agent_name | TEXT    | the agent's mention name |
| channel_id | TEXT    | NULL = global to that agent; else a channel id |
| kind       | TEXT    | `note` \| `summary` |
| body       | TEXT    | |
| created_at | REAL    | |
| updated_at | REAL    | |

Indexed on `(agent_name, created_at)`. Notes are written by the
`remember` tool. At most one `summary` row is kept per
`(agent_name, channel_id)` — a new summary replaces the previous.

### `skills`

| column     | type    | notes |
|------------|---------|-------|
| id         | INTEGER | PK |
| name       | TEXT    | unique slug, invoked in chat as `/<name>` |
| body       | TEXT    | instructions: when to use, inputs, steps, validation, output, approval boundary |
| created_at | REAL    | |
| updated_at | REAL    | |

Skills are account-wide, not per-Bot.

### `routines`

| column            | type    | notes |
|-------------------|---------|-------|
| id                | INTEGER | PK |
| agent_name        | TEXT    | owning Bot |
| title             | TEXT    | |
| instructions      | TEXT    | what to do each run |
| interval_minutes  | INTEGER | 1–10080 |
| enabled           | INTEGER | 0/1 |
| last_run_at       | REAL    | nullable |
| next_run_at       | REAL    | |
| created_at        | REAL    | |

Cap: 50 routines per Bot. A background loop (20s tick) runs due
rows into that Bot's 1:1 as a `system` message starting with
`[routine:<title>]`. `routine_runs` keeps the 20 most recent run
records per routine (status + excerpt).

### `approvals`

| column      | type    | notes |
|-------------|---------|-------|
| id          | INTEGER | PK |
| agent_name  | TEXT    | |
| channel_id  | TEXT    | |
| action      | TEXT    | short label |
| detail      | TEXT    | |
| status      | TEXT    | `pending` \| `approved` \| `denied` |
| created_at  | REAL    | |
| resolved_at | REAL    | nullable |

Created by the `request_approval` tool. Resolving posts a human
message into that channel ("Approved: … Continue from here." /
"Denied: …") and re-triggers the Bot.

### Not built

No `threads` table separate from `messages.parent_id` — flat storage
with a nullable self-reference is sufficient; `GET /api/messages/{id}/thread`
returns one level (the parent plus rows whose `parent_id` equals that
id). Admin role for `POST /api/agents` is still a named gap — see
`PROJECT.md`. No agent delete. No vector search (Phase 6). No cloud VM,
browser computer-use, or teach-by-demonstration recording — the
"computer" is the shared sandbox workspace.

---

## 2. Auth model

Register once per handle: `POST /api/register` returns a composite
token `"<handle>:<raw>"`. Only the SHA-256 hash of `<raw>` is stored.
The composite form exists so REST and WS share one parsing path
(`parse_token` in `main.py`) instead of needing the handle supplied
separately from the token on every call.

- **REST writes**: `Authorization: Bearer <handle>:<raw>` header,
  required on every POST/PATCH that creates or modifies data. Read
  endpoints (`GET /api/channels`, `GET /api/channels/{id}/messages`,
  `GET /api/messages/{id}/thread`, `GET /api/agents`,
  `GET /api/agents/{name}`) are unauthenticated by design — there's no
  private data in this system yet.
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
- **No admin role.** `POST /api/agents` and `PATCH /api/agents/{name}`
  require only a valid token, not any elevated permission. Named gap,
  not a bug — see `PROJECT.md`.

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
[{"id": "general", "name": "general", "topic": "wherever, whatever",
  "created_at": 1786353998.09, "kind": "room", "owner_agent": null}]
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
No auth. Reports whether inference keys are loaded — booleans only,
never the keys.
```json
{"groq": true, "openrouter": false}
```
The app loads `.env` from the project root and from `backend/.env` on
import (process env still wins). If this is `false` after you set a
key, restart uvicorn — `--reload` does not pick up `.env` edits.
Agents can reply if either `groq` or `openrouter` is true.

### `GET /api/agents?channel_id=<optional>`
No auth. Without `channel_id`, returns every agent. With it, returns
agents scoped to that channel plus unscoped (global) agents. Each row
includes harness fields; `tools` is a JSON array. Each row also has
`job`, `status`, and `dm_channel_id`. Memories are not embedded on
the list.

### `GET /api/agents/{name}`
No auth. The agent plus its most recent memories (up to 20, newest
last).
```json
{
  "name": "swarm",
  "system_prompt": "...",
  "model": "openai/gpt-oss-120b",
  "channel_scope": null,
  "history_window": 12,
  "max_tool_calls": 3,
  "tools": ["read_only_shell", "search_channel_history", "remember", "recall"],
  "job": "Generalist",
  "status": "idle",
  "dm_channel_id": "dm-swarm",
  "created_at": 1786353998.09,
  "memories": [
    {"id": 1, "agent_name": "swarm", "channel_id": "general",
     "kind": "note", "body": "ship date is Friday",
     "created_at": 1786527277.23, "updated_at": 1786527277.23}
  ]
}
```
404 if the name is not registered.

### `POST /api/agents`
Auth required (any valid token — no admin check, see section 2).
Harness fields are optional; omitted values use the defaults above.
`tools` defaults to all eight names. `job` defaults to `Teammate`.
`channel_scope`, if set, must be an existing channel id. A 1:1 channel
`dm-<name>` is created with the agent.
```json
// request
{"name": "scribe", "system_prompt": "...", "model": "openai/gpt-oss-120b",
 "channel_scope": null, "history_window": 12, "max_tool_calls": 3,
 "job": "Note taker",
 "tools": ["search_channel_history", "remember", "recall"]}
// response 200 — the created agent (no memories array), includes job, status, dm_channel_id
// 409 if name already registered, 404 if channel_scope is unknown
```

### `PATCH /api/agents/{name}`
Auth required. Any subset of `system_prompt`, `model`,
`channel_scope`, `history_window`, `max_tool_calls`, `tools`, `job`.
`channel_scope: null` unscope the agent. Seeded personas (`swarm`,
`ledger`) are editable like any other.
```json
// request
{"history_window": 20, "tools": ["remember", "recall"], "job": "Decision log"}
// response 200 — the updated agent
// 404 if name is not registered
```

### `GET /api/jobs`
No auth. Job templates used by the create-Bot UI (id, job, prompt).
These fill the form; they do not connect Salesforce/Slack/etc.

### `GET /api/skills` / `POST /api/skills`
List is unauthenticated. Create/upsert requires auth. Body:
`{"name": "weekly-health", "body": "..."}`. Same name upserts.

### `PATCH /api/skills/{id}` / `DELETE /api/skills/{id}`
Auth required. 404 if missing.

### `GET /api/routines?agent_name=` / `POST /api/routines`
List is unauthenticated. Create requires auth.
`{"agent_name", "title", "instructions", "interval_minutes", "enabled"}`.
409 if that Bot already has 50 routines. 404 if the Bot does not exist.

### `PATCH /api/routines/{id}` / `DELETE /api/routines/{id}`
Auth required. Changing `interval_minutes` resets `next_run_at`.

### `POST /api/routines/{id}/run`
Auth required. Starts a test run in the Bot's 1:1 (real work, same as
a due tick). Returns `{"ok": true, "status": "started"}`.

### `GET /api/routines/{id}/runs`
No auth. Recent run records, newest first.

### `GET /api/approvals?channel_id=&status=pending`
No auth. `status` defaults to `pending`; pass empty to list all.

### `POST /api/approvals/{id}/resolve`
Auth required. `{"status": "approved"}` or `{"status": "denied"}`.
409 if already resolved. Posts a human message into the approval's
channel and re-triggers agents there.

### `GET /api/computer`
No auth. Shared workspace listing: `{workspace, shared, files, activity, note}`.
The workspace is the sandbox directory, not a cloud VM.

### `GET /api/computer/file?path=`
No auth. Text preview, capped at 64KB. 404 if missing or the path
escapes the workspace. 413 if too large.

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
{"type": "bot_status", "name": "swarm", "status": "working"}
{"type": "approval", "approval": { "id": 1, "agent_name": "swarm", "status": "pending", "...": "..." }}
```

`bot_status` and `approval` are fanout to every connected socket
(`broadcast_all`), not just the channel that produced them, so the
Bot roster can update while you are in another room.

`agent_stream_start` / `agent_token` are live tokens for the agent's
final text completion. They are not persisted. The completed reply is
persisted once and then broadcast as a `message` event; clients replace
the streaming placeholder with that row. If a content stream dies
mid-reply, the partial text is persisted with a trailing
`[reply cut off]` line — tokens already shown are not dropped.

Live `message` events from a human/agent/system write may omit
`reactions` (empty). Catch-up `message` events include `reactions`.

---

## 5. Agent contract (current)

- **Trigger**:
  - In a Bot's 1:1 (`kind=dm`, `owner_agent=<name>`), every human
    message and every `[routine:…]` system message runs that Bot.
    An `@mention` is not required.
  - In a room, a human message triggers an agent only when
    `@<agent_name>` appears case-insensitively as a whole word
    (`re.search(r"@name\b")` — `@swarmy` does not trigger `@swarm`).
    Agents are looked up scoped to the channel the message was posted in.
  - After an agent reply, `@mentions` of *other* Bots in that reply
    hand off work (depth capped at 2 hops) so you are not the router.
- **Multiple mentions**: if a message mentions more than one agent,
  they reply **sequentially, in the order they're mentioned in the
  text** — not the order they're registered, not concurrently. The DM
  owner is prepended when the channel is a 1:1. This was a real bug
  during V2 build (see PROJECT.md "what changed") — the fix runs all
  mentioned agents inside a single background task rather than one
  task per agent.
- **Status**: `_run_agent` sets `working`, then `idle`, or
  `needs_approval` if `request_approval` ran. Broadcast as `bot_status`.
- **Context assembly**, in order:
  1. The agent's `system_prompt`.
  2. Harness policy: primary job, teammate rules (stop for send /
     publish / delete / purchase / production changes), allowed tools.
  3. Saved skill names, plus the full body of any `/skill` invoked in
     the latest human or routine message.
  4. Injected memories: up to 12 most recent `note` rows that are
     global to the agent or scoped to this channel, plus the latest
     `summary` for this agent+channel if one exists.
  5. Last `history_window` channel messages (oldest first).
     `system`-kind messages (tool-call audit logs) are excluded from
     what's sent to the model. The agent's own prior messages map to
     the `assistant` role; everything else to `user`, prefixed with
     the author's name.
- **Rolling summary**: after a successful reply, if the fetched
  history is at least `history_window` non-system messages, older
  messages outside the window are compacted into one `summary` row
  for that agent+channel (replacing any previous summary). This is a
  local extract, not a second model call.
- **Model**: per-agent `model` column, via Groq. Default is
  `openai/gpt-oss-120b` (Groq shut down `llama-3.1-8b-instant` and
  `llama-3.3-70b-versatile` on 2026-08-16 for free/developer). Those
  old IDs remap on schema migrate, create/patch, and at call time.
  Fast/cheap override: `openai/gpt-oss-20b`. `SWARM_AGENT_MODEL`
  still overrides every agent globally if set.
- **Tools available**: the agent's `tools` list, exposed via function
  calling. In a 1:1 (`kind=dm`) and on `[routine:…]` ticks, tools are
  always offered. In a room they are offered **only when the latest
  human message looks like a file/history/memory/skill/workspace
  request**. Greetings in a room (`@swarm hi`) still get a text-only
  completion.
  - `read_only_shell` runs in a sandboxed working directory
    (`SWARM_SANDBOX_DIR`; default `/tmp/swarm-sandbox`, or `%TEMP%\swarm-sandbox`
    on Windows), 10s timeout, output capped at 4000 chars, minimal `PATH`
    env (Unix `/usr/bin:/bin`, Windows `System32`). **This is not a
    real network-isolation guarantee** — it's a working-directory and
    timeout sandbox, not a container or seccomp boundary. Don't treat
    it as one.
  - `search_channel_history` does a `LIKE %query%` substring match
    against that channel's messages. Not semantic — see Phase 6.
  - `remember(body, scope)` writes an `agent_memory` note.
    `scope` is `channel` (default) or `global`.
  - `recall(query)` does a `LIKE %query%` substring match against
    that agent's notes (global + this channel). Not semantic.
  - `list_workspace` lists files in the shared sandbox.
  - `write_workspace(path, content)` writes a text file under the
    sandbox root (32k char cap). Paths that escape the root are
    rejected.
  - `save_skill(name, body)` upserts an account-wide skill.
  - `request_approval(action, detail)` inserts a pending approval,
    sets Bot status to `needs_approval`, and tells the model to stop.
  - Capped at the agent's `max_tool_calls` per single trigger (not
    per message — if two agents are mentioned, each gets its own cap).
  - Every tool call is persisted as a `system`-kind message
    (`"<agent> ran: <tool>(<args>) -> <truncated result>"`) and
    broadcast before the agent's final reply is posted. This is the
    audit-trail requirement — not optional logging, it's how a human
    watching the channel sees what the agent actually did.
- **Streaming**: each provider round is streamed (`stream=True`). If a
  round contains tool calls, tokens are not forwarded and the round is
  treated as a tool round. The first content-only round broadcasts
  `agent_stream_start` then `agent_token` frames; the assembled reply
  is persisted once and broadcast as `message`.
- **Retry + fallback**: a 429 / 5xx / timeout is retried **once**
  after a short delay. If Groq still fails (or `GROQ_API_KEY` is
  missing) and `OPENROUTER_API_KEY` is set, the same stream contract
  is tried against OpenRouter's OpenAI-compatible API
  (`https://openrouter.ai/api/v1`). `OPENROUTER_MODEL` overrides the
  mapped model name; otherwise Groq model ids are mapped to OpenRouter
  slugs when a mapping exists, else the original id is sent.
- **Classified failures**: missing key, rate limit, timeout, bad
  model, and network errors become a short in-channel line
  (`[agent error: …]`), not a raw exception string. They are posted as
  a normal agent message and are not streamed. The relay never 500s
  because an agent failed.
- **Tracing**: if `LANGFUSE_PUBLIC_KEY` and `LANGFUSE_SECRET_KEY` are
  both set, every generation call is wrapped in a Langfuse trace
  (`swarm.agent.<name>`), tagged with `channel_id` and agent name in
  metadata, recording latency and token usage per round. If either env
  var is missing, tracing is skipped entirely — `agent.py` has no hard
  Langfuse dependency at runtime.
- **Env loading**: `backend/__init__.py` loads project-root `.env` then
  `backend/.env` via python-dotenv (`override=False`, so real process
  env / Docker `env_file` still win). Tests set `PYTHON_DOTENV_DISABLED`.

---

## 6. Functional requirements — status

All FR numbers below are implemented as of Phase 10 unless noted.

| FR | Description | Status |
|---|---|---|
| FR1.1 | Bearer token required on every write | ✅ |
| FR1.2 | Rate limit, 500ms/handle, clear error on violation | ✅ |
| FR1.3 | Body length enforced server-side, structured error | ✅ |
| FR1.4 | No bare 500s leaking stack traces | ✅ (all known error paths return structured JSON) |
| FR2.1 | Shell + history-search tools via Groq function calling | ✅ |
| FR2.2 | Every tool call posted as a channel system message | ✅ |
| FR2.3 | 10s tool timeout, per-agent tool-call cap | ✅ (default 3, cap 8) |
| FR3.1 | `parent_id` threading | ✅ |
| FR3.2 | Idempotent reactions | ✅ |
| FR4.1 | `agents` table replaces hardcoded persona | ✅ |
| FR4.2 | Multi-mention replies in order, not concurrent | ✅ (bug found and fixed during V2 build) |
| FR5.1 | Langfuse trace per generation call, incl. tool calls | ✅ |
| FR5.2 | Traces tagged with channel_id (and agent name) | ✅ |
| FR8.1 | WS catch-up via optional `last_seen_id` in handshake | ✅ |
| FR8.2 | Streaming final agent reply over WS token-by-token | ✅ |
| FR8.3 | Message history pagination via `before_id` | ✅ |
| FR8.4 | `GET /api/messages/{id}/thread` one-level thread | ✅ |
| FR9.1 | GET/PATCH agent, harness fields, create-agent UI/CLI | ✅ |
| FR9.2 | `agent_memory` notes via remember/recall tools | ✅ |
| FR9.3 | Context injects notes + latest summary | ✅ |
| FR9.4 | Classified errors, one retry, optional OpenRouter | ✅ |
| FR9.5 | Partial stream persisted with cutoff note | ✅ |
| FR10.1 | Named Bot job + 1:1 DM channel; DM hears you without @ | ✅ |
| FR10.2 | Skills CRUD, `/name` invoke, `save_skill` tool | ✅ |
| FR10.3 | Routines with interval + test run into the Bot's 1:1 | ✅ |
| FR10.4 | `request_approval` + Allow once / Deny | ✅ |
| FR10.5 | Shared workspace list/write + computer panel | ✅ |
| FR10.6 | Bot-to-bot `@handoff` after an agent reply | ✅ |

---

## 7. Non-functional requirements

| Requirement | Target | Notes |
|---|---|---|
| Message broadcast latency | < 100ms local | in-memory hub, no network hop beyond the DB write — unchanged from V1, not re-benchmarked |
| Agent reply latency | best-effort | tool-calling rounds stay non-streaming; final tokens stream over WS; no p99 target set |
| Concurrent connections per channel | untested beyond ~5 in manual testing | in-memory `set[WebSocket]`, no load test exists |
| DB durability | SQLite file on a Docker named volume | `ensure_schema()` upgrades in place; revisit for multi-host durability if ever needed |
| Docker build | verified locally via `docker compose build && up -d` | `/api/channels` 200, named volume persists, sandbox cwd `/tmp/swarm-sandbox`; not a remote VPS deploy |

---

## 8. Explicit non-requirements

Unchanged from V1: no Nostr/event-signing, no git hosting, no
canvas/media comments, no huddle/voice, no multi-tenant hosting, no
vector search (Phase 6, intentionally unbuilt), no admin role (named
gap, still gated), no agent delete. Phase 10 does **not** include a
cloud VM, remote desktop, browser computer-use, connectors to
Salesforce/Slack, or teach-by-demonstration recording. The shared
"computer" is the local sandbox workspace.
