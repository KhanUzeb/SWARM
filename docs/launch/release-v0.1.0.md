# Swarm v0.1.0 — release notes

**Status:** draft. Nothing has been tagged or published. Sections marked
`[maintainer]` need a decision before release.

## What v0.1.0 is

The first tagged release of Swarm: a self-hosted workspace where AI agents are
named teammates in team chat, with an audit trail.

The pitch in one line: agents use the same channels, the same message
primitives, and the same history as people — and everything they do stays in
the thread where it happened.

## The three flagship flows

These are what the release is built around. Everything else is supporting
cast.

### 1. Approval-gated actions

A bot that wants to do something outward-facing calls `request_approval` and
stops. A human allows or denies it from the approvals panel, and the decision
lands in the thread as a message. Deny genuinely stops the action.

Tool calls, approvals, and failures are persisted in-channel, so the record
survives without anyone exporting a log.

### 2. `@handoff` chains

Agents hand work to each other by name. `swarm` can hand a diff question to
`coder` and both replies stay visible in the transcript. Multi-mention replies
run **sequentially in mention order** — bots do not talk over each other.

Depth is capped (handoffs 3, `delegate_task` 2), so a chain terminates.

### 3. Scheduled routines and digests

Cron-style routines post into a channel on a schedule. `@ledger` summarises
decisions from recent history. This is the "AI ops desk" case: it works while
nobody is watching.

## Try it with no API key

```bash
docker run -p 8000:8000 -e SWARM_DEMO=1 ghcr.io/khanuzeb/swarm:latest
```

Demo mode seeds a deterministic scene: a release-blocker question, a handoff
chain, and one pending approval you can click. No provider key, no network
call, no telemetry.

## Security posture

This release defaults to the safe side, and says plainly where it does not.

- **Provider keys are obfuscated, not encrypted.** They are stored sealed with a
  keyed transform under `SWARM_SECRET`. That protects a key at rest against
  casual disclosure — a copied database file, a backup, a screenshot. It is not
  a vault, and it is not a defence against an attacker who already has the
  secret. Set a strong `SWARM_SECRET`. See
  [docs/SECURITY-REVIEW.md](../SECURITY-REVIEW.md) finding 5.
- **Host-system tools are off by default.** `system_run`, `system_read`, and
  `system_write` execute commands on the machine running the backend. They
  require an explicit `SWARM_SYSTEM=1` — in the app *and* in the published
  image. When enabled on a non-loopback bind, startup logs a warning naming the
  bind address and the effective root.
- **The sandbox is a working directory and a timeout.** It is not a container
  and not network isolation. A deny-list backs it up; it is a backstop, not a
  boundary. See the Threat model section of [SECURITY.md](../../SECURITY.md).
- **Only admins can rebind the host root** or connect providers.

## Known gaps

Stated plainly rather than discovered later.

- **Not built for the public internet.** Swarm is local-first. It is designed
  for one person on their own machine or a trusted LAN. Putting it on the open
  internet needs TLS, a reverse proxy, and real rate limiting in front of it.
- **No token revocation.** A leaked token stays valid until the database is
  reset. There is no logout.
- **Container isolation is not implemented.** Bots run as the user that started
  the server.
- **Cost accounting is collected but not surfaced.** Token usage is tracked per
  run in memory; there is no per-run cost figure in the UI yet, so there is no
  cost figure in this release either.
- **MCP is not implemented.** The plugin loader an MCP server would plug into
  exists; the client and server do not.
- **Semantic search is not implemented.** Knowledge search is FTS5 keyword
  search. Adequate for a small local corpus, and it fails quietly rather than
  loudly when it misses.

## Upgrade notes

Databases created by earlier commits upgrade in place. Schema changes are
applied at startup through `ensure_schema()`, which is additive — it adds
columns and tables and never drops anything. No destructive migration ships in
this release.

`[maintainer]` — if the versioned migration runner lands before the tag, name
it here. Until then the honest statement is the one above.

To adopt the new host-tools default, set `SWARM_SYSTEM=1` explicitly if you
want it. Nothing changes if you never set it.

## Thanks

Everyone who filed an issue, opened a PR, or read the docs and told us where
they were wrong. `[maintainer]` — names, credits, and the funding/contact
details go here before this ships.