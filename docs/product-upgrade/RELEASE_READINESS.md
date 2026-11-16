# v1.5.0 release readiness assessment (draft)

> Status: **DRAFT / ASSESSMENT** — not a published release note.
> Prepared 2026-11-17 for the operator's "final docs / v1.5.0 release" task.
> Verdict: **NOT READY.** Do not tag, push, publish, or trigger the Docker workflow.

This document exists so the release request has a truthful answer on record. It
does not claim that a v1.5.0 release happened. Per the public-repo rules in
`AGENTS.md` §0 it contains no secrets, tokens, account emails, or local paths.

## 1. Verdict

The `product/local-first-v1` branch is **not** a v1.5.0 release. It is an
in-progress implementation of the master prompt's Gate 1, sitting on the
`0.1.0` code line. Tagging it `v1.5.0`, pushing it to `main`, or triggering
`docker.yml` would misrepresent the product and would violate both the master
prompt's release gate ("Never ... publish a release ... without explicit
operator approval") and the user's own "if work is done and correct" condition.

No push, tag, release publication, or workflow trigger was performed for this
task. All changes are local and limited to documentation.

## 2. State of the repository at assessment time

| Item | Observed value |
| --- | --- |
| Released version in code / docs | `0.1.0` (`backend/main.py`, `README.md`, `docs/DEPLOY.md`) |
| `main` / `origin/main` | `96fb44f1042ae52d61f0d4e8187d383e5d3b835e` (the prompt's baseline reference) |
| Delivery branch | `product/local-first-v1` @ `e46807f669befd755ba8690f356ec74645b824dd` |
| Divergence | 10 commits ahead of the baseline; not merged, not pushed |
| Tags | only `v0.1.0` (on `dac7913`, in `main`'s history) — no `v1.5.0` locally or on the remote |
| Working tree | clean except the untracked `prompt (1).md`, which was read and preserved, never staged |
| Workflows | `ci.yml` (pytest + bun test + build + bundle budget on `main`/PR); `docker.yml` (GHCR multi-arch image + GitHub release on `v*` tags, also `workflow_dispatch`) |

## 3. Master-prompt gates — objective status

| Gate | Required outcome | Actual status |
| --- | --- | --- |
| Gate 0 — baseline | App runs, failures catalogued, demo without paid model, backup before data touched | **Complete** (see `BASELINE.md`) |
| Gate 1 — safety & durability | Recoverable execution, no duplicate irreversible effects, fail-closed approvals, correct event replay | **Implemented in code but NOT closed.** No gate-level full CI record, no concurrency / kill-and-recover integration evidence, no gate sign-off |
| Gate 2 — local install & onboarding | Guided first run, backup/restore verified, no hosted account | **Not implemented** on this branch |
| Gate 3 — cockpit UX | One work model, Desk/Work/approval consolidation, responsive + keyboard accessible | **Not implemented** (one `WorkHome.jsx` change only; the overlapping panes remain) |
| Gate 4 — quality harness | Repeatable eval suite, cost/verification visibility, baseline-vs-after | **Not implemented** |
| Gate 5 — ecosystem | MCP client + one curated integration behind policy/audit | **Not implemented** |

Section 13 deliverables still missing: `GAP_AUDIT.md`, `PRODUCT_BRIEF.md`,
`UX_SPEC.md`, `ARCHITECTURE.md`, `IMPLEMENTATION_PLAN.md`, `THREAT_MODEL.md`,
`EVAL_REPORT.md`. Only `BASELINE.md`, `PHASE_LOG.md`, and this assessment exist.

## 4. What this branch actually contains (draft release notes)

These statements describe code on the branch. They are **not** claims about a
shipped release.

### Safety and durability
- `68f82ce` — v2 workflow approvals are exact-bound and fail-closed: renewable
  lease with fencing generation, single-use grants consumed atomically with the
  unpark, per-grant nonce in the binding hash, stale/expired state derivation,
  reissue/admin reconciliation, fenced artifact writes, durable ambiguity events.
- `9c3e718` — workflows fail on unreported work and keep branch decisions stable.
- `e46807f` — work events get one immutable global order (source-qualified,
  stable resume cursors).
- `01bf1a9` — budget-trimmed memory is applied to the real agent prompt.

### Execution boundary
- `c25747b` — host shell execution (`read_only_shell`, `computer_run`, plugin
  `shell`) requires explicit `SWARM_SYSTEM` opt-in, refuses the `fake` provider,
  and keeps an honest "not isolated" approval label; `SECURITY.md` updated.

### Budgets and observability
- `957aa1b` — token-budget enforcement is honest when usage is missing:
  unknown/malformed fields are not counted as zero, known fields still stop the
  run at the cap, and no extra closing call is made on token exhaustion.

### Install and diagnostics
- `7559ccc` — `swarm doctor` is self-contained (renders under the default
  Windows console encoding) and the model-catalog test is environment-hermetic;
  this resolves the two failures catalogued in `BASELINE.md` (F1, F2).
- `451206f` — backup verification in `docs/DEPLOY.md` now fails on a missing
  database.

### Housekeeping
- `e6ff201` — removed default constants duplicated from `backend/settings.py`.
- `dd29eb7` / `55690c8` — Gate 0 baseline record and phase log.

### Explicitly not included
No install wizard, no verified backup/restore CLI path, no UX consolidation, no
golden-path workflow templates, no evaluation harness, no MCP client, no
container isolation, and **no version bump to 1.5.0**. The version remains
`0.1.0` in all version-bearing files.

## 5. Checks performed for this task

| Check | Result | Caveat |
| --- | --- | --- |
| Focused backend test modules (10 touched files) | 194 passed | Run before the operator's "no pytest" directive; not a full-suite run |
| `bun test` (frontend) | 99 pass, 0 fail, 387 assertions | One more than the 98 recorded in `BASELINE.md` (new contract test in `68f82ce`) |
| `bun run build` | success, 1930 modules, ~0.95 s | Bundle within CI budget: vendor 260.56 kB raw / 81.73 kB gzip |
| `python -m backend.doctor` (default console encoding) | usable — 13 ok, 1 warn, 0 fail, 1 skipped | Confirms the F2 fix from `7559ccc` |

## 6. Verification limitations — must accompany any release claim

- **No full backend suite run.** The operator directed that no pytest be run for
  this release task. Recorded evidence only: baseline `486 passed / 1
  environment-dependent fail`; that one failure was addressed by `7559ccc`, and
  a focused 194-test run passed. No fresh full 487-test result and no GitHub CI
  run have been observed on this branch — do not call it CI-green.
- No browser E2E / UX acceptance tests (master prompt §5.4) were run.
- No live model or tool calls, no concurrency / kill-and-recover integration
  evidence, no upgrade/restore smoke test on a stopped instance.
- Bundle budget was checked locally, not by `ci.yml` on GitHub.
- No security review or threat model was produced for this commit range.

## 7. Blockers

1. **Gates materially incomplete** — Gates 2-5 unimplemented; Gate 1 not closed
   or signed off. The user's "if work is done and correct" condition is false.
2. **Version mismatch** — the tree is `0.1.0`; `v1.5.0` is not grounded in the
   work performed and would need explicit operator agreement even as a target.
3. **No full CI evidence** — no full pytest (per directive) and no GitHub CI run
   on this branch; CI only triggers on `main`/PR, which has not received these
   commits.
4. **No publication approval** — master prompt §0 non-negotiable 10 and the
   coordinator both require explicit approval before push/tag/release.

## 8. Exact intended actions (only after readiness and approval)

None of the following were executed:

1. Reconcile `product/local-first-v1` into `main` (fast-forward or reviewed PR),
   confirm `origin/main` has not moved and no remote work would be overwritten;
   non-force push only.
2. Agree the release number with the operator and bump it consistently in
   `backend/main.py`, `README.md`, `docs/DEPLOY.md`, and any other
   version-bearing file before tagging.
3. Run the full backend suite, `bun test`, `bun run build`, and `doctor` on the
   final tree; confirm `ci.yml` is green on the exact commit to be released.
4. Replace this draft with release notes derived from the merged diff.
5. Create the annotated tag on that exact commit and push the tag (this is what
   `docker.yml` keys on).
6. Trigger `docker.yml` via `workflow_dispatch` to rebuild/push the GHCR image
   and let the `release` job publish the GitHub release with pull instructions.
7. Verify the image tag and the release page, then report completion.

---

See `PHASE_LOG.md` for the chronological change record and `BASELINE.md` for the
Gate 0 evidence.
