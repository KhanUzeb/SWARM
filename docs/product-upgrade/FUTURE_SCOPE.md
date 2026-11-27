# Future scope: optional prospects

This is a list of things Swarm could do next, not a schedule and not a
promise. No gate is closed by this file. `docs/CHANGELOG.md` records what is
merged; `docs/product-upgrade/PHASE_LOG.md` records the evidence and what is
still open. Prospects are ordered by what a local owner would feel next, not
by how interesting the technology is.

## Ground rules

- `docs/PRODUCT.md` gates speculative work: build the simpler thing first, and
  take up a prospect when daily use shows the simpler thing has failed.
- Ship one slice at a time. A slice has a user-visible result, enforcement in
  the backend when safety is involved, offline tests, and docs in the same
  change.
- A label is not evidence. A gate closes only with a test or an observation
  that supports it. `AGENTS.md` section 9 has the standing checks.

## Verification still owed

These close work already in the tree. They are not new features.

- Run the full backend and frontend checks plus CI on the final tree. CI was
  green on the 2026-11-27 merges into `main`, which does not cover later edits.
- Close Gate 1: multi-process kill-and-recover, the cursor migration and its
  one-time replay, the orphan file left when an artifact commit fails, and
  independent review sign-off. Do not claim exactly-once external effects
  without an idempotency contract from the external system.
- Verify backup and restore on a disposable stopped volume, then boot it and
  check a known row. `docs/DEPLOY.md` states what is and is not verified.
- Run browser acceptance: first run to a finished task, the approval journey,
  a narrow layout, keyboard-only approval, and screen-reader semantics.

## Optional prospects

### Local onboarding and data (Gate 2)

A guided first run checks the runtime, sets `SWARM_SECRET`, connects a model
or chooses demo, picks a workspace, and runs one starter task. Windows first.
Add data and privacy controls: storage use, export, retention, and a visible
backup location.

### Cockpit and useful work (Gate 3)

Give work, see who is doing it, intervene, and get a verified result from one
place. The launch, work list, and inspector are still separate surfaces.
Keep `AGENTS.md` section 8a: flaps for states, a printed log for events, two
signal hues, and no motion that runs while the app sits idle.

### Quality and cost (Gate 4)

Repeatable offline task fixtures with baseline-versus-after reports, truthful
usage and verification state, and golden paths for audit, research, and
repro-and-fix. Show unknown costs as unknown; do not infer savings.

### Ecosystem (Gate 5)

A local stdio MCP client behind the existing tool policy and audit trail, plus
one integration with a real fixture. Remote HTTP MCP stays off until SSRF,
authorization, and redirect handling are reviewed.

### Deliberately deferred

Per-user credentials, container isolation, semantic or vector memory, a
public demo profile, native packaging, and a cloud control plane. `ROADMAP.md`
records why each is on the list and which groundwork already exists. Being
listed here is not a commitment to build it.

## Not decided here

No version bump, release note, tag, or workflow trigger follows from this
document. Those come after merged behavior and explicit operator approval.
