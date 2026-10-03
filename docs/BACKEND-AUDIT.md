# Backend audit

Scope: schema and indexes, concurrency, error handling, WebSocket lifecycle,
config sprawl, and test gaps. Findings cite `file:line` and were checked by
reading the code and, where behaviour was in doubt, by running it.

This is a **local-first** tool — one user on their own machine — so severity
is weighted toward what actually bites in that setting: a routine that
deadlocks the UI, a run that hangs forever, a config mistake nobody can
diagnose. "Attacker reaches the open internet" is explicitly out of scope and
is covered in [SECURITY-REVIEW.md](SECURITY-REVIEW.md) instead.

P0 = fix before launch. P1 = two weeks. P2 = roadmap.

---

## 1. Schema and indexes

27 tables across `db.py`, `v2.py`, `work.py`, `knowledge.py`,
`memory_graph.py`. `ensure_schema()` in `db.py` upgrades in place via
`PRAGMA table_info` + `ALTER TABLE`, and `backend/migrations.py` adds
versioned transactional migrations on top.

**Handled now** (verified by `tests/test_sqlite_migrations.py`):

- `journal_mode=WAL`, `foreign_keys=ON`, `busy_timeout` — each set *and read
  back*, because SQLite silently ignores `journal_mode` on some filesystems
  and `foreign_keys` is per-connection and off by default.
- `messages(channel_id, id)` for the `last_seen_id` catch-up, plus
  `messages(created_at)`, `routine_runs(routine_id)`, and equivalents in
  `knowledge` and `memory_graph`.
- The approvals index already existed and was deliberately **not**
  duplicated; a test asserts that.

**P2 — connection sites are copy-pasted.** `aiosqlite.connect` appears at 45
sites: `v2.py` 17, `work.py` 11, `knowledge.py` 8, `db.py` 5, `memory_graph.py` 4.
Each must now remember to apply the pragmas, and any site that forgets silently
gets `foreign_keys=OFF`. A single `connect()` helper that applies pragmas and
yields the connection would make the correct thing the only thing. The pragmas
are correct today; the pattern is what will drift.

---

## 2. Concurrency

**Handled:**

- WAL plus a busy timeout means the routine scheduler and chat writes no longer
  block each other outright.
- Multi-mention replies are sequential: `_run_agents_in_order` awaits each agent
  in turn (`main.py:2026`). Verified correct — the previous depth double-count
  bug is fixed.
- `_background_tasks` is a module-level `set` with explicit cancellation in the
  lifespan teardown.
- Two runs in the same channel cannot interleave mid-sentence, because a
  channel's agents run inside one task.

**P1 — there is no per-channel run queue.** Ordering holds within one trigger,
but two humans posting `@swarm` in the same channel 200 ms apart start two
concurrent runs against the same transcript. `max_tool_calls` bounds each run;
nothing serialises them. For a single user this is rare. For a bot replying to
a routine tick while a human is typing in the same channel, it is reachable.
The fix is a per-channel lock so a run finishes before the next starts.

**P2 — `_last_write` grows unbounded** (`main.py:81`). It is keyed by handle and
never evicted. Locally that is a handful of entries and harmless. It was
flagged in the security review as attacker-mintable on a public deploy; here it
is only a slow leak, worth fixing when convenient.

---

## 3. Error handling

**Handled:**

- 33 `except Exception` handlers, **zero** bare `except:`. Failures become
  `[agent error: …]` messages in the thread rather than 500s, per the documented
  contract.
- Optional Langfuse tracing is wrapped so a tracing failure cannot break a reply.

**P1 — the broad handlers are load-bearing and undocumented.** Most of the 33
exist because a provider call can raise anything. That is defensible, but the
`# noqa: BLE001` comments suppress the lint that would normally force someone
to justify each one. A short comment at each site saying *why* it is broad
would stop a future reader from narrowing one incorrectly.

---

## 4. WebSocket lifecycle

**Handled:**

- First-frame auth: `{"token": "<handle>:<raw>", "last_seen_id": …}`, else
  close `4001`. The handle is fixed at handshake, so a client cannot set
  `author` over WS.
- Disconnects are caught and sockets are removed (`main.py:1930`, with a
  `finally`).
- Dead sockets are reaped: `broadcast` collects failures and calls `leave`
  (`main.py:130-138`).
- Reconnect uses a **generation guard** plus a `localStorage` outbox, so a
  stale socket's frames are ignored and unsent messages replay.

**P1 — there is no heartbeat.** Nothing pings an idle socket. A connection that
died without a close frame (laptop sleep, NAT timeout, container restart) is
only discovered on the next write. Locally that means a bot that appears
"working" in the UI while the socket is dead. A periodic ping with a pong
deadline would close the gap.

**P1 — `broadcast` awaits sockets serially** (`main.py:130`). One slow client
delays delivery to every other client in the room. With a handful of tabs this
is invisible; it is the shape that becomes a problem with more.

**P2 — no backpressure.** There is no bounded send queue, so a slow consumer
grows memory instead of dropping frames. Acceptable locally.

---

## 5. Config sprawl

22 distinct `SWARM_*` variables read across the backend. Parsing is **not**
consistent between variables: `SWARM_SYSTEM` and `SWARM_SYSTEM_UNRESTRICTED`
accept `1/true/yes/on` (`tools/system.py:50,84`), while `SWARM_DEMO` accepts
only `1/true/yes` (`agent.py:986`, `db.py:869`). Each variable is at least
internally consistent, so this is duplication rather than a live bug — but the
duplication is why an operator who learns that `on` works somewhere else gets a
silent no-op instead of an error somewhere else.

**P1 — one validated settings object.** A `pydantic-settings` model checked at
startup, with one `truthy()` helper, would make `on`/`yes`/`1` behave the same
everywhere and turn a bad value into a startup error naming the variable.
`swarm doctor` then becomes a thin wrapper over the same object. This is the
single highest-leverage cleanup in the backend.

---

## 6. Test gaps

244 tests, all passing, offline and deterministic. Genuinely covered: auth
roles, approvals, computer/sandbox paths, memory and knowledge, v2 workflows,
mentions, orchestration, retry, GC.

**P1 — the OpenAI SDK path has zero tests.** No test imports `openai`;
`_complete_stream` and `build_openai_compatible_client` are untested. The suite
would stay green if the SDK removed `chat.completions.create`. This is exactly
why `docs/launch/pr-triage.md` recommends **request changes** on the openai
3.11→3.19 bump: real risk, zero signal. Same for Langfuse, which CI never
constructs because the tracing keys are unset.

**P1 — `docs/launch/pr-triage.md` records that every pin is an open `>=` with
no lockfile.** All four open Dependabot PRs, and `main`, install byte-identical
sets. Four green badges are not four verifications.

**P2 — no load or soak test.** `scripts/loadtest.py` does not exist, and
`docs/PERFORMANCE.md` does not exist. No benchmark should be published until one
is measured on real hardware.

---

## Priority order

| # | Item | Sev |
| --- | --- | --- |
| 1 | One validated settings object with consistent truthy parsing | P1 |
| 2 | Tests around the provider SDK surface (`_complete_stream`, compat client) | P1 |
| 3 | WebSocket heartbeat and pong deadline | P1 |
| 4 | Per-channel run queue so two triggers cannot interleave | P1 |
| 5 | `connect()` helper so pragmas cannot be forgotten at a new call site | P2 |
| 6 | Parallel `broadcast` instead of serial awaits | P2 |
| 7 | Bounded send queue / backpressure | P2 |
| 8 | Evict `_last_write` | P2 |
| 9 | Justify the 33 broad `except Exception` sites | P2 |
| 10 | A lockfile, so dependency floors mean something | P1 |

Items 1 and 2 are the ones that will bite a new contributor first: a config
value that silently does nothing, and a dependency bump with no test to catch
the breakage.