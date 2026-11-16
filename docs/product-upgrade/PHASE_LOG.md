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
