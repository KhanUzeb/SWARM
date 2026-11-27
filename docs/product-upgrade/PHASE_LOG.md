# Product Upgrade — Phase Log

Chronological record of completed phases. Entries are factual; nothing is
recorded as done unless a command was actually run and its result observed.
See `BASELINE.md` for the full Gate 0 evidence.

---

## Gate 0 — Baseline and project setup

**State:** complete (baseline established; no source changes made).

**Date:** 2026-11-16 (local machine time).

**Branch / HEAD:** `product/local-first-v1` @
`96fb44f1042ae52d61f0d4e8187d383e5d3b835e`. This already matched the prompt's
reference commit and was based directly on `main`, so no branch creation or
reset was performed. The only pre-existing change was the untracked
`prompt (1).md`, which was read and preserved. A concurrently-edited
`docs/DEPLOY.md` (another worker) was left untouched.

### What was done

1. Read `prompt (1).md` (the 369-line master prompt) and `AGENTS.md` in full
   before acting.
2. Inspected `git status`, branch, HEAD, merge-base, and runtime environment.
3. Took hash-verified backups of all four on-disk SQLite files into gitignored
   `.scratch/baseline-backups/20261116-221103/` *before* running any check.
4. Ran the mandatory baseline commands (venv interpreter for backend):
   - `python -m pytest -q` → **486 passed, 1 failed** (175.90 s)
   - `cd frontend && bun test` → **98 pass, 0 fail** (1.69 s)
   - `cd frontend && bun run build` → **success** (11.34 s, 1930 modules)
   - `python -m backend.doctor` → **crashed** on Windows `cp1252` encoding
   - `PYTHONIOENCODING=utf-8 python -m backend.doctor` → **13 ok, 1 warn,
     0 fail, 1 skipped**, exit 0
5. Root-caused both failures (see F1/F2 below) with reproduction commands.
6. Captured practical measurements (import time, core endpoint latencies,
   bundle sizes) using demo mode and a throwaway DB.
7. Wrote `BASELINE.md` with exact, truthful results and this phase log.

### Findings recorded (pre-existing; not fixed in this phase)

| ID | Finding | Severity | Classification |
| --- | --- | --- | --- |
| F1 | `test_list_provider_models_reports_a_boolean_connected[openai]` fails because ambient `OPENAI_API_KEY` is set; test assumes a clean env. Re-run with the var removed → 3 passed. | Low | Confirmed test-hermeticity defect, not a product regression |
| F2 | `backend.doctor` crashes with `UnicodeEncodeError` on default Windows `cp1252` console (report contains `→`), violating its "never raises / exit 0 when usable" contract. Works under UTF-8. | Medium | Confirmed Windows-specific bug |
| F3 | `GET /api/status` median 190.4 ms anonymous (resolves 46 providers per call). | Low | Performance observation / baseline number |
| F4 | Stale `.pyc` files in `tests/__pycache__` for deleted test modules; not collected by pytest. | Info | Hygiene note |

### Measurements summary

- Backend test suite: **487 tests total** (486 pass + 1 environment-dependent
  fail); frontend: **98 tests, 0 fail**.
- Import `backend.main`: 1.715 s. `/health` median 2.63 ms; `/api/status`
  median 190.40 ms; first-admin register 121.59 ms.
- Frontend bundle: `index + vendor` JS ≈ 395.76 kB raw / 122.47 kB gzip;
  CSS 86.38 kB raw / 15.12 kB gzip.
- Demo path verified without a paid model (`demo=true`, `llm_ready=true`).
- Memory, WS E2E, and real task latencies were **not** measured; no invented
  numbers.

### Gate 0 verdict

All four Gate 0 requirements are met (functionality runs; failures catalogued;
demo launches without a paid model; no data touched without backup). The two
catalogued issues do not block the start of Gate 1 work.

### Files created by this phase

- `docs/product-upgrade/BASELINE.md`
- `docs/product-upgrade/PHASE_LOG.md` (this file)

No source, test, DB, or config files were modified. Raw evidence logs and
backups live in gitignored `.scratch/baseline-backups/20261116-221103/`.

### Commit

Baseline commit: `55690c87c0522da958c8c7ca36199cb1d794018d`
(`docs: record Gate 0 baseline and phase log`). Local only — no push. Only the
two files listed above were staged; other workers' in-progress changes were
left untouched.

### Remaining risks / carry-forward

- F1 will keep producing a red baseline on any machine with ambient provider
  keys until the test is made hermetic — do not misread it as a regression in CI.
- F2 means the documented first-run diagnostic is broken by default on the
  Windows-first target platform; it needs a fix before Gate 2 onboarding
  relies on `swarm doctor`.
- Performance numbers are warm in-process measurements, not production or E2E
  figures; do not present them as such.

### Next recommended slice

Per the master prompt §12, the next slice is **S01 — fix run
claim/resume/state/cursor correctness and real failure semantics** (Gate 1),
owned by a different worker. A small `S00.5` hotfix for F2 is a reasonable
parallel candidate because it affects the Windows first-run path; that
decision belongs to the coordinator.

---

## Product-branch progress after Gate 0 (commits `451206f`..`e46807f`)

**State:** Gate 1 work substantially implemented but **not closed**; Gates 2-5
not started. This is a factual append-only record, not a release claim. See
`RELEASE_READINESS.md` for the full assessment.

**Range:** `product/local-first-v1`, ten commits on top of the Gate 0 baseline
(`55690c8`). Latest HEAD at assessment: `e46807f`.

### What changed, by commit (source of truth: `git show <sha>`)

| Commit | Area | Concrete change |
| --- | --- | --- |
| `451206f` | docs / DEPLOY | Backup verification now fails on a missing database (owned by the docs worker; not touched by this documentation pass) |
| `7559ccc` | diagnostics / tests | `swarm doctor` made self-contained (default Windows console encoding); model-catalog test made environment-hermetic. Resolves baseline findings F1 and F2 |
| `c25747b` | execution boundary / SECURITY | Host shell execution requires explicit `SWARM_SYSTEM` opt-in, refuses the `fake` provider, keeps an honest "not isolated" label; `SECURITY.md` updated |
| `01bf1a9` | agent context | Budget-trimmed memory is applied to the real agent prompt |
| `e6ff201` | housekeeping | Removed default constants duplicated from `backend/settings.py` |
| `957aa1b` | budgets | Token-budget enforcement is honest when provider usage is missing; no extra closing call on exhaustion |
| `68f82ce` | v2 approvals | Exact-bound, fail-closed approvals with renewable lease/fencing, single-use nonce-bound grants, reissue/admin reconcile, fenced artifact writes |
| `9c3e718` | v2 workflows | Fail on unreported work; keep branch decisions stable |
| `e46807f` | work events | One immutable global order for work events; stable resume cursors |

`docs/CHANGELOG.md` already carries revert-effect rows for the major layers
(`68f82ce`, `957aa1b`, `c25747b`); this pass does not duplicate or rewrite them.

### Verification evidence available for this range

- Focused re-run of the ten touched backend test modules: **194 passed**.
  Frontend `bun test`: **99 pass, 0 fail** (up from 98 in Gate 0). `bun run
  build`: success, within the CI bundle budget. `swarm doctor`: usable under
  both UTF-8 and the default `cp1252` console, confirming the F2 fix.
- **Limitation:** the operator directed that no pytest be run for the release
  task. The 194-test backend result came from a focused run before that
  directive; no fresh **full** backend suite (487 tests) and no GitHub CI run
  exist for this branch. Do not describe the branch as CI-green.

### Gate status

- Gate 0: complete (see the section above).
- Gate 1: implemented in code but **not closed** — no gate-level full CI record,
  no concurrency or kill-and-recover integration evidence.
- Gates 2-5: not implemented (no onboarding/backup-restore verification, no UX
  consolidation, no eval harness, no MCP client).

### Scope of this documentation pass

Docs only: this file and `RELEASE_READINESS.md`. No source, test, config, or
database files were changed; no push, tag, release, or workflow trigger was
performed. The untracked `prompt (1).md` was preserved and never staged.

---

## Gate 1 — durable cancellation root-cause fix

**State:** implemented and verified with standalone checks; **not** pytest- or
CI-verified. No commit was made for this slice.

**Date:** 2026-11-26. **Branch / HEAD at start:** `main` @ `f4a8586`.

### Confirmed root cause

`backend/v2.py::cancel_run` only called `task.cancel()` on an in-process
`asyncio.Task`. It wrote no durable state. Reproduction (throwaway SQLite file,
standalone script, no pytest): a claimed run with no local task — the normal
state after a restart or when the executor is another process — returned
`cancel_run=False`, stayed `status='running'`, emitted zero `run_cancelled`
events, and after the lease was expired a second worker re-claimed it and
flipped the status back to `running`. A queued run or a gate parked for
approval could not be cancelled at all. This violates the master prompt §3.3
invariant "cancellation is durable and prevents new side effects".

### Fix

`cancel_run` now commits, under the shared event lock and `BEGIN IMMEDIATE`,
one transaction that (1) sets `status='cancelled'` and `finished_at`,
(2) clears `lease_owner`/`lease_expires_at` so every later executor write is
fenced, (3) settles pending/granted approval grants as `cancelled` (the owner
cancelled the run; nobody denied the gate), and (4)
appends exactly one `run_cancelled` event (already mapped to `work_cancelled`
in `backend/work.py`). It then cancels the local task, if any. Terminal runs
reject repeat cancels. The existing `POST /api/v2/runs/{id}/cancel` route was
not edited; it now succeeds for queued, parked and cross-process runs too.

### Verification actually performed

- Before fix: `.scratch/repro_cancel_durability.py` printed
  `cancel_run=False / status=running / reclaimable=True / worker-2 re-claim=True`.
- After fix, the same script printed
  `cancel_run=True / status=cancelled / reclaimable=False / one run_cancelled event /
  worker-2 re-claim=False / status=cancelled`.
- `.scratch/verify_cancel_fix.py` (standalone, throwaway DB): **26/26 checks
  passed** — terminal-state guard, no duplicate event, no recovery/reclaim,
  stale executor event/status/artifact writes rejected, pending and granted
  gates settled and late approval decisions rejected, and a live in-process
  task observing cancellation with a durable `cancelled` status.
- `.scratch/verify_cancel_route.py` (standalone FastAPI `TestClient`,
  throwaway DB): first `POST /api/v2/runs/{id}/cancel` → **200** with
  `status=cancelled`, repeat → **404**, terminal event `run_cancelled`.
- `python -m py_compile backend/v2.py tests/test_v2.py`: OK.
- Tests added to `tests/test_v2.py` (4 test functions, 5 cases after
  parametrization) but **not executed**: the operator's no-pytest directive remains in force. No
  full suite, no frontend tests, no CI result. `ruff check` on the touched
  files reports only pre-existing issues (including the backup worker's
  unused `import os` in `backend/v2.py`, which was preserved).

### Files changed by this slice

`backend/v2.py`, `tests/test_v2.py`, `docs/CHANGELOG.md`, this file. The
backup worker's concurrent untracked files and edits (`backend/backup.py`,
`tests/test_backup.py`, `cli/swarm_cli.py`, `docs/DEPLOY.md`, `import os` in
`backend/v2.py`) were preserved; nothing was staged, committed, pushed, or
published.

### Limitations and next priority

- Cancellation is now durable in SQLite, but no true multi-process
  kill-and-recover integration test was run; that gate evidence is still open.
- A cancel cannot undo an external side effect already started; work after the
  last durable checkpoint remains ambiguous by design (no exactly-once claim).
- The orphan-artifact window after a DB failure in `create_artifact` and the
  one-time work-event cursor replay validation are still open and are the next
  Gate 1 priority, ahead of any UI or Gate 2 work.

---

## Gate 1 — work-session cancellation, mirror durability, truthful restart

**State:** implemented and verified with standalone checks; **not** pytest- or
CI-verified. Nothing was staged, committed, pushed, or published.

### Cancellation at an approval gate (verified end to end)

Before this slice, `work.cancel_session` suppressed every `v2.cancel_run`
failure and appended `work_cancelled` anyway. Reproduced with a parked gate:
a simulated run-cancellation failure returned **200** with the session
`cancelled`, but the run stayed `waiting_for_approval` with a **pending** grant;
a late approval then returned **200** and the "cancelled" work completed — a
phantom resume. With the durable `cancel_run` already in place, the normal
path worked (session/run `cancelled`, grant settled, late approval **409**,
not reclaimable), but the failure path was still false.

`work.cancel_session` now cancels the run first and fails closed with
`CancellationFailed` when the run is still active; `POST /api/work/{id}/cancel`
maps that to **409** instead of claiming success. Reproduced after the fix:
failed cancellation → **409**, session still `waiting_for_approval`, grant
still pending, later decision can legitimately resume. The grant settled by a
cancel is now recorded as `cancelled`, not `denied`: no human denied that gate.

### Work-event mirror (crash reservation + pagination)

Two defects were reproduced with throwaway databases:
1. A reservation with `work_seq=0` (crash after reservation, before event
   append) permanently hid the run event — 0 mirrored events.
2. A run with 2006 events mirrored only **2000**; `_mirror_run_events` always
   requested `after=0` with the default limit, so the tail was never fetched.

`_mirror_run_events` now commits the work event and its reservation in one
transaction via a shared `_append_event_in_conn` helper (no public-lock
re-entry), retries old `work_seq=0` rows, adopts an already-appended work event
instead of duplicating it, and pages from the oldest pending reservation past
the high-water mark. After the fix: crashed reservation mirrored (reservation
`(1,2)`), crash-after-append adopted exactly once (`(2,2)`), 2006/2006 events
mirrored, and the existing cursor-stability scenario still passed (6 initial /
8 final events, stable tail).

### Review follow-ups

- A failed lease renewal was swallowed by the heartbeat. `execute_run` now
  sets `lease_lost` and raises `StaleRunClaim` before the next agent call,
  parallel child, or final checkpoint. Reproduced: with a failing renewal and
  a two-node workflow, **1** agent call, `execution_ambiguous`, no
  `run_completed`.
- `recover_interrupted` recorded "interrupted by restart" (and cancelled the
  session) even when the run had already completed. It now mirrors first and
  records the real terminal outcome. Reproduced: completed run + stale active
  session → session `completed`, last event `work_completed`.

Tests added but **not executed** (no-pytest directive): five work-session and
mirror cases in `tests/test_work.py`, one lease case in `tests/test_v2.py`,
plus the parameter-case updates in `tests/test_v2.py`. `py_compile` is the only
static check run for this slice; no full suite, no frontend tests, no CI.

### Simplicity, orphan artifact and migration investigation

Owned code was simplified: `work.cancel_session` no longer reads the run
before calling `cancel_run` (which already handles missing/terminal runs), and
`v2.cancel_run` no longer sets an unused row factory.

Two reported issues were reproduced but are **low impact, not fixed**:
- `create_artifact` writes the file before the DB row commits. A simulated
  commit failure after the write left `file exists=True, DB rows=0` — an
  unreferenced file, not a false artifact or lost data. A future fix would
  write to a temp name, rename after the commit, and prune unreferenced run
  files at startup; not done here because it risks a row pointing at a missing
  file and is outside this slice.
- A legacy work database with no mirror rows and a client cursor at the old
  tip receives a one-time catch-up (`work_queued`, `tool_finished`,
  `work_completed`), then replay is identical and the session ends
  `completed`, `requires_action=False`. That matches the documented migration
  behaviour; no compatibility fix was warranted.

Three review findings *were* confirmed and fixed:
- `recover_interrupted` appended a second `work_cancelled` after the mirror
  had already delivered the run's terminal event (seq 3 mirrored, seq 4
  native). It now re-reads the session after mirroring and skips the native
  event when the session is already terminal. Reproduced: one terminal event.
- A gate denial produced two `work_cancelled` events (native seq 5, mirrored
  seq 11). The native settlement now carries `_via_run`/`_via_run_seq`, so the
  mirror adopts it. Reproduced: one terminal event.
- `reconcile_ambiguous(..., "cancel")` left `lease_owner` set, and a stale
  executor could still append a fenced event after the admin cancel. The
  cancel now clears the lease in the same transaction. Reproduced: lease
  `None`, late append rejected with `StaleRunClaim`.
- A late approval after cancel was already refused (409) and produced no
  event; no mirror terminal regression was reproducible.

Tests were added but **not executed** (no-pytest directive). `py_compile`
passed; no pytest, no CI, no git operations.

### Remaining gaps

No true multi-process kill-and-recover test; the mirror fix is atomic within
one process/connection (SQLite serialises writers, but no cross-process
stress test was run). External side effects after the last checkpoint stay
ambiguous. The orphan-artifact window remains open, as does a full automated
cursor-migration test in CI (only standalone and unexecuted pytest coverage
exists here).

---

## Remaining-work and verification assessment (narrowed local-first scope)

**Date:** 2026-11-26. **Scope:** truthful status for the current working tree;
this is **not** a release-readiness statement and authorises no push, tag,
release, or deployment. Earlier sections are the detailed evidence; this is the
short current summary.

### Completed and verified within this assignment

- Gate 1 safety fixes in `backend/v2.py` and `backend/work.py`: durable, fenced
  cancellation; fail-closed `work.cancel_session`; exact-bound single-use grant
  settlement; truthful gate state; lease-loss stop before further side effects;
  crash-safe, paginated work-event mirroring; truthful restart settlement;
  single terminal audit events; and a fenced admin reconcile-cancel.
- Each fix was reproduced before and after with standalone, throwaway-DB
  scripts (no pytest): cancellation routes 200/409 as expected, late approval
  refused with 409, 26/26 cancellation checks passed, 2006/2006 run events
  mirrored, crash reservations adopted without duplication, one terminal audit
  event after recovery and denial, failed renewal stopped the second agent
  call, and stale executors were fenced after admin cancel.
- `python -m py_compile` passed on the touched Python and test files. No data,
  push, tag, release, or workflow trigger was produced by this assignment.

### Verified as low impact and deliberately not fixed

- An orphan file can remain when `create_artifact` commits fail after the file
  write; reproduced as `file exists=True, DB rows=0`, with no false success or
  data loss. Cleanup is deferred.
- A legacy work DB with no mirror rows gives an old cursor one documented
  catch-up replay; the replay then stabilises and the session ends in the
  truthful terminal state. No compatibility break was reproduced.

### Verification still missing

- **No pytest / no full CI.** The operator's no-pytest directive remains in
  force. New tests exist in `tests/test_v2.py` and `tests/test_work.py` but
  were not executed; no full backend suite, frontend tests, build, or GitHub
  CI run exists for the current tree. Historical counts in earlier sections
  are not current results.
- **Independent reviews:** findings reported so far were reproduced and fixed
  where confirmed; review sign-off on the current tree is still pending.
- **Browser:** no Playwright/manual E2E, no 390px/keyboard/screen-reader
  acceptance, and no verified end-to-end "task to artifact" journey.
- **Docker/install:** no image build, compose boot, upgrade, or restore-on-a-
  stopped-instance verification. Backup/restore CLI code from another worker
  is uncommitted and not covered by this assessment.
- **Crash/recovery:** no true multi-process kill-and-recover test; SQLite
  serialises writers, but cross-process takeover and recovery were not
  stressed. No exactly-once external effect is claimed; ambiguous side effects
  after the last checkpoint remain by design.

### Scope correction — essential work continues

Gates 2–5 are **not** wholesale deferred. The operator asked the essential
local-first work to continue: onboarding, the UI/UX surface, and the agentic
features that deliver useful work remain in scope. Only novel, oversized, or
misaligned items are deferred — for example a full MCP ecosystem beyond one
useful integration, semantic/vector memory, per-user credentials, a public-demo
profile, native packaging, a cloud control plane, and any speculative
extension that does not improve a local owner's daily work. Smaller, measured
versions of evaluation evidence and integrations can be chosen when they serve
that local-first path. Other workers' uncommitted UI and backup edits were
preserved but not verified by this documentation pass.

### Bottom line

Gate 1 safety code has targeted behavioral evidence, but the product is **not
release ready**: full-suite/CI, browser, Docker, and multi-process recovery
evidence are missing, independent review sign-off is pending, and the required
work in Gates 2–5 is still incomplete while essential onboarding, UX and
agentic implementation continues. Next priority is to lift the no-pytest
restriction, run the full backend/frontend checks and CI, close independent
review, then verify install/backup/restore on a stopped disposable instance
before any release discussion.

---

## Scope correction and work-cancel audit dedupe (2026-11-26)

The "Deferred under the narrowed local-first scope" wording was corrected:
Gates 2–5 are not deferred wholesale. Essential onboarding, UI/UX and agentic
features continue; only novel, oversized or misaligned extensions are deferred.

One remaining local-first workflow defect was reproduced and fixed in this
pass: `work.cancel_session` wrote a native `work_cancelled` event and the run
mirror later appended a second one for the same `run_cancelled` event (seq 6
native, seq 8 mirrored). The native settlement now carries `_via_run` and
`_via_run_seq`, so the mirror adopts it. Verified offline with a throwaway DB:
one terminal work event, still tagged `via_run`, and the cancellation routes,
late-approval refusal, restart recovery, denial dedupe and reconcile fencing
checks all still passed. A test assertion was added to
`tests/test_work.py` but **not executed** (no-pytest directive); `py_compile`
passed, with no pytest, no git operations and no other workers' files touched.

### Final review findings disposition

- Duplicate `work_cancelled` after `cancel_session`: already fixed above by
  tagging the native settlement with `_via_run`/`_via_run_seq`. Re-verified:
  exactly one terminal work event, `via_run` set.
- "Recovery fallback missing report": not current. `get_run_status_any_owner`
  already selects and decodes `report`; re-verified with a completed run and a
  stale active session — recovery logged `work_completed` with summary
  `real result`.
- Missing `run_cancelled` on reconcile cancel: reproduced
  (`run events: run_queued, approval_reconciled`) and fixed.
  `reconcile_ambiguous` now writes `run_cancelled` in the same transaction as
  the reconcile decision; the work-session sync tags its native cancellation
  with that event so the mirror adopts it. Re-verified: run events include
  `run_cancelled`, the work log has one terminal event with `via_run` set, and
  the stale executor is still fenced. Tests updated but **not executed**
  (no-pytest directive).

---

## Local workflow usability — truthful queued/running state

A red-capable offline loop (`.scratch/verify_work_status_before_start.py`, with
execution patched out) showed the exact user-facing defect: a newly created run
was `queued`, but its work session already read `running` and carried a
`work_started` event. `POST /api/v2/runs` wrote that event before any executor
claimed the run.

Fix: run creation now only creates the queued session; `_sync_work_with_run`
flips a queued session from the durable run state via `work.mark_started_from_run`,
and the run's own `run_started` event stays the single audit record that the
mirror materialises once. After the fix the same loop showed
`run=queued / work=queued / no work_started`; with execution enabled and the
run completed, a second loop showed `run=completed / work=completed` and exactly
one `work_started` event with `via_run` set. All earlier cancel, mirror, cursor,
denial and reconcile offline checks still passed.

Tests were added to `tests/test_work.py` but **not executed** (no-pytest
directive). `py_compile` passed. No pytest, no git operations, no release claim;
other workers' files were preserved.

---

## Local workflow usability — real approval gate identity

A red-capable offline loop (`.scratch/verify_gate_step_label.py`) showed the
user-facing defect: a gate named `review_gate` was reported by the run's
authoritative approval state, but the work rail showed `active_step="approval"`
because `_sync_work_with_run` hard-coded that step id and generic label.

The sync now reads `v2.approval_state(run_id, owner)` and uses its real
`step_id` and canonical label. After the fix the loop showed
`run gate step=review_gate / work active_step=review_gate`. A behavior test was
added to `tests/test_work.py` but **not executed** (no-pytest directive);
earlier cancel/mirror/start offline checks were re-run and stayed green. No
pytest, no git operations, no release claim; other workers' files preserved.

---

## Approval event dedupe and concurrent work polls

Independent review finding reproduced with a throwaway DB
(`.scratch/verify_approval_event_dupes.py`): three concurrent work-list syncs
of one parked session wrote three native `approval_requested` events, and the
mirror later added a fourth; an approval decision was also written natively
and again by the mirror.

Fix: `work.mark_waiting_from_run` performs an atomic, owner-scoped state-only
transition guarded by session status, so concurrent polls cannot each append a
request; the run's own `approval_requested` event stays the single audit
record and the mirror materialises it with the real step and label. The
approval route now tags its native `approval_resolved` event with the run event
it mirrors, so the mirror adopts it. After the fix the loop showed one
`approval_requested` and one `approval_resolved`, both with `via_run` set;
earlier cancel, denial, status, gate-name, recovery and mirror checks stayed
green. Two behavior tests were added to `tests/test_work.py` but **not
executed** (no-pytest directive); `py_compile` passed, no pytest or git
operations, no handoff-file edits, other workers' files preserved.

---

## Terminal audit dedupe and current gate identity

Independent review finding reproduced offline
(`.scratch/verify_terminal_and_gate_dupes.py`): completion and failure each
produced two work events (native plus mirrored), and a second approval gate
kept `active_step="g1"` although the authoritative gate was `g2`.

Fix: `_sync_work_with_run` tags its native `work_completed` and `work_failed`
settlements with the run event they mirror, so the mirror adopts them;
`mark_waiting_from_run` now replaces `active_step` with the current gate step
instead of preserving the previous one. After the fix the loop showed one
`work_completed`, one `work_failed`, and `active_step="g2"` at the second gate.
Tests asserting exact event counts and two-gate behavior were added to
`tests/test_work.py` but **not executed** (no-pytest directive). `py_compile`
passed, no pytest or git operations, no handoff-file edits, other workers'
files preserved.

---

## Concurrent stale terminal syncs and unknown gate identity

Independent review finding reproduced with `.scratch/verify_concurrent_terminal_sync.py`:
four concurrent `_sync_work_with_run` calls reading the same stale session
snapshot wrote four `work_completed` and four `work_failed` events even though
each carried `via_run` tags; sequential dedupe could not stop that race. The
same loop showed a run waiting with no approval grant still displaying the old
`active_step="old_gate"`.

Fix: `work.mark_terminal_from_run` performs one owner-scoped, non-terminal-
guarded state update, so only the first poll settles the session; the run's own
terminal event is the single audit record the mirror materialises. The waiting
transition now sets `active_step` from the authoritative grant step, clearing
it when no grant exists. After the fix the loop showed one `work_completed`,
one `work_failed`, and `active_step=None` for the unknown-gate case; earlier
approval dedupe, two-gate, cancel, start-status, recovery and mirror checks
stayed green. Behavior tests were added/updated in `tests/test_work.py` but
**not executed** (no-pytest directive). `py_compile` passed, no pytest or git
operations, no handoff-file edits, other workers' files preserved.

---

## Run retry — unfinished, removed

A checkpointed retry endpoint was implemented and twice revised after review.
Independent re-review then reproduced two further defects: unrelated gate
reconciliation could clear an execution-ambiguity barrier, and the linked work
session reopen was not atomic with the run requeue. Two rounds did not produce
a safe retry contract, so the endpoint, `v2.retry_run`, the work-session
reopen helper, its event mapping, its tests and its changelog rows were
removed. **Retry remains unfinished and is not part of this change set.**
Prompt §5.2's "Retry when valid" action still needs a design that handles
live executors, stale grants, ambiguous side effects and linked-session state
in one transaction before it is reintroduced. No pytest or git operations;
earlier Gate 1 durability and work-sync work was left intact.

---

## Reconciliation with merged main (2026-11-27)

**State:** the Gate 1 code described above is merged into `origin/main`
`3ffdf86` through PRs #43, #44, #45 and #42. This section updates the
current-state caveats in the 2026-11-26 summaries. It does not rewrite the
historical entries or close a gate.

- The integrated tree is CI-green. GitHub ran `ci.yml` on the merge commits
  for all four PRs; both jobs (`backend`: `pytest -q`, `frontend`: `bun test`,
  `bun run build`, bundle budget) reported success. That is evidence for the
  merged tree, not for later uncommitted edits.
- The earlier "no pytest / no full CI" caveat described the branch at the
  time. It is not a current statement about `origin/main`.
- Gate 1 is still not signed off. No multi-process kill-and-recover result,
  orphan-artifact cleanup, automated cursor-migration replay, browser
  acceptance, or independent review sign-off is on record here.
- Gates 2-5 remain incomplete, and run retry remains removed. No release, tag,
  version bump, or Docker workflow trigger is claimed. Optional prospects are
  listed in `docs/product-upgrade/FUTURE_SCOPE.md`.

---

## Closing note (2026-11-27)

The watchdog pass found no blockers. `ci.yml` is green on the four merge
commits carrying the merged slices (#43, #44, #45 and #42), with both the
backend and frontend jobs successful. Independent review of those merged
slices is complete with no blockers raised; this updates the review caveat
in the reconciliation section above for those slices only. It does not close
Gate 1 or any later gate, and it makes no release claim. Verification still
owed and the optional prospects are listed in
`docs/product-upgrade/FUTURE_SCOPE.md`.
