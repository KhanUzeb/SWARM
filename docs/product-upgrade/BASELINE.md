# Gate 0 — Baseline Record

> Scope: Gate 0 baseline and project setup for the `product/local-first-v1` upgrade.
> This file records **only what was actually run and observed** on 2026-11-16.
> It does not claim fixes, screenshots, benchmarks, or user testing that did not happen.
> Operator-specific absolute path segments (user directory) are redacted as
> `$REPO`, `$TEMP`, and `$USERPROFILE` in keeping with the public-repo rules in
> `AGENTS.md` §0. Nothing here contains a secret value.

## 1. Repository / environment at baseline

| Item | Observed value |
| --- | --- |
| Date / timezone | 2026-11-16 (local machine time) |
| Branch | `product/local-first-v1` |
| HEAD | `96fb44f1042ae52d61f0d4e8187d383e5d3b835e` (`chore: blank SWARM_SECRET, set version 0.1.0, add aiosqlite import`) |
| `main` HEAD | `96fb44f1042ae52d61f0d4e8187d383e5d3b835e` |
| Merge-base (`HEAD`, `main`) | `96fb44f1042ae52d61f0d4e8187d383e5d3b835e` — the product branch was already created from the exact prompt reference commit; **no branch operation was needed** |
| OS | Microsoft Windows 11 Home Single Language, version 10.0.26300, build 26300 |
| CPU / RAM | Not inventoried (not required for Gate 0 verdict) |
| Python (venv, used for all backend checks) | 3.11.14, `$REPO/.venv/Scripts/python.exe` |
| Python (system, on PATH) | 3.11.9; 3.14.x also installed but not used |
| bun | 1.4.3 (`$USERPROFILE/.bun/bin/bun.exe`) |
| uv | 0.13.5 |
| Frontend deps | `$REPO/frontend/node_modules` present; `bun.lock` present |
| Installation method | Local source checkout with uv-managed `.venv` + bun-installed frontend deps. **Not** Docker. |
| Console encoding (default) | Windows code page `cp1252` — see Finding F2 |

### Working-tree state (preserved, not overwritten)

At the start of the task the only change was the **untracked** `prompt (1).md` (the
master prompt, 369 lines). It was read, never modified, and remains untracked.
During the task another worker began editing `docs/DEPLOY.md`; that file is
**owned by that worker and was not touched here**. No user work was discarded.

### Data-safety step taken before any check

Gate 0 requires "no data touched without backup." Hash-verified copies of all
four on-disk SQLite files were written to gitignored
`$REPO/.scratch/baseline-backups/20261116-221103/` before any command ran:

| Original | SHA-256 |
| --- | --- |
| `backend/swarm.db` | `B913D7EB81D0A4C3B53D619B6D2143F86EB2EE59E853A6C528A3C76BB5A92287` |
| `tests/_test.db` | `5325D6ED52B41223A82A158F6FC37A76C90D595EA80C0EDCDCD253434AAE942A` |
| `.scratch/hallmark-preview.db` | `72E4F25E30D6EC80EE1FF032BB26D61958031D09A968CB7717A28304A841C8DF` |
| `.scratch/openrouter-browser-smoke.db` | `4039857234855E8F238A0B33964578CD6C349AAB5699942DC81FAC3144D0972E` |

`swarm doctor` only reads the DB (it opens it append-mode but writes nothing);
`pytest` uses `tests/_test.db` via `conftest.py`, which also sets
`PYTHON_DOTENV_DISABLED=1`. The measurement script used a throwaway DB in the
same gitignored backup folder. No real user data was modified.

## 2. Mandatory baseline commands — exact results

All backend commands used the venv interpreter (the documented repo path),
not the bare system `python`.

| # | Command | Exit | Result |
| --- | --- | --- | --- |
| 1 | `.venv\Scripts\python.exe -m pytest -q` | 1 | **486 passed, 1 failed** in 175.90 s (0:02:55). See Finding F1. |
| 2 | `cd frontend && bun test` | 0 | **98 pass, 0 fail**, 379 `expect()` calls across 4 files, 1.69 s. |
| 3 | `cd frontend && bun run build` | 0 | Vite 8.3.0 build succeeded, 1930 modules transformed, **11.34 s**. Bundle table below. |
| 4 | `.venv\Scripts\python.exe -m backend.doctor` | 1 | **Crashed** with `UnicodeEncodeError` on default `cp1252` console. See Finding F2. |
| 5 | `PYTHONIOENCODING=utf-8 ... -m backend.doctor` | 0 | `swarm doctor: usable — 13 ok, 1 warn, 0 fail, 1 skipped`. Full report below. |

Raw stdout/stderr for every command is retained in the gitignored log folder
(listed in §7).

### Frontend production bundle (from `bun run build`)

| Asset | Size | Gzip |
| --- | --- | --- |
| `dist/assets/index-D5SDqZ3g.js` | 135.20 kB | 40.74 kB |
| `dist/assets/vendor-B00R66Az.js` | 260.56 kB | 81.73 kB |
| `dist/assets/index-Dc6ipqnH.css` | 86.38 kB | 15.12 kB |
| `dist/assets/WorkHome-C1rEFlRs.js` | 15.50 kB | 5.03 kB |
| `dist/assets/WorkRail-OwEWfEON.js` | 12.74 kB | 3.66 kB |
| `dist/assets/ProviderPanel-B5XTKxMt.js` | 10.43 kB | 3.16 kB |
| `dist/assets/SystemPanel-D1tnbkAd.js` | 7.92 kB | 2.44 kB |
| `dist/assets/ComputerPanel-Bc6R_s9-.js` | 7.28 kB | 2.51 kB |
| `dist/assets/BrowserPanel-DGKDar3d.js` | 2.37 kB | 0.97 kB |
| `dist/assets/PluginsPanel-B88GKYrB.js` | 1.90 kB | 0.86 kB |
| `dist/assets/KnowledgeView-CnM2NU-d.js` | 4.40 kB | 1.65 kB |
| `dist/assets/AppsPanel-BRB8h3rG.js` | 4.49 kB | 1.69 kB |
| `dist/assets/ToolsPanel-CZXrYTl5.js` | 4.88 kB | 1.62 kB |
| `dist/assets/rolldown-runtime-CbXtAM7H.js` | 0.58 kB | 0.36 kB |
| `dist/index.html` | 2.64 kB | 1.06 kB |

No bundle budget was found in CI for this repo, so no pass/fail budget claim is made.
The output is recorded for the baseline-vs-after comparison in Gate 4 / `EVAL_REPORT.md`.

### `swarm doctor` report under UTF-8 (the run that completed)

```
swarm doctor: usable — 13 ok, 1 warn, 0 fail, 1 skipped

  [ok  ] python: Python 3.11.14
  [ok  ] dependencies: all 6 required packages import
  [ok  ] settings: 25 SWARM_* variables parsed
  [ok  ] providers: 4 provider(s) connected
  [ok  ] provider:groq: connected via GROQ_API_KEY
  [ok  ] provider:openrouter: connected via OPENROUTER_API_KEY
  [ok  ] provider:openai: connected via OPENAI_API_KEY
  [ok  ] provider:huggingface: connected via HF_TOKEN
  [ok  ] db_path: $REPO/backend/swarm.db (existing file, writable)
  [ok  ] sandbox: $TEMP/swarm-sandbox (default (TEMP/swarm-sandbox))
  [warn] host_tools: host-system tools are ON: bots can run commands on this machine
          -> Set SWARM_SYSTEM=0 unless you deliberately want host command execution.
  [ok  ] secret: SWARM_SECRET is set
  [ok  ] browser_tools: playwright browser tools are on (set SWARM_BROWSER=0 to disable)
  [ok  ] declared_bind: declared bind: loopback (uvicorn default)
  [skip] connectivity: not tested (pass --connectivity to probe the provider host)
```

(The only text change from the raw output is redaction of absolute paths and
the arrow character in the `host_tools` fix line.)

## 3. Findings (confirmed, with evidence)

### F1 — pytest failure is environment-dependent, not a code regression

- Failing test: `tests/test_model_catalog.py::test_list_provider_models_reports_a_boolean_connected[openai]`
- Assertion: `assert row["connected"] is False`, with the comment
  *"No key in the test environment, so it must be explicitly False"*.
- Observed: `assert True is False` — the provider reported `connected=True`.
- Cause: `OPENAI_API_KEY` **is set in the ambient process environment** on this
  machine (presence confirmed; value never read or recorded). `conftest.py`
  sets `PYTHON_DOTENV_DISABLED=1`, which disables `.env` file loading but does
  **not** unset inherited process variables, so `catalog.list_provider_models`
  correctly sees a key and reports `connected=True`.
- Proof: re-running just that parametrized test with `OPENAI_API_KEY` removed
  from the process environment gives **3 passed in 2.41 s, exit 0**.
- Classification: **pre-existing test hermeticity defect** (the test assumes a
  clean environment). It does not indicate a product regression. Baseline is
  recorded as `486 passed / 1 failed (environment-dependent)`.
- Proposed fix (for a downstream slice, **not** applied here): make the test
  hermetic with `monkeypatch.delenv` for all provider key env vars, or mock the
  auth resolver. Severity: low. Risk: low.

### F2 — `backend.doctor` violates its own "never raises" contract on Windows

- Default invocation crashes before printing the report:
  `UnicodeEncodeError: 'charmap' codec can't encode character '\u2192' in position 761`.
  The report contains a `→` (U+2192) in the `host_tools` fix line; the default
  Windows console codec is `cp1252`, which cannot encode it.
- Exit code 1 despite the box being usable — the exact opposite of doctor's
  documented "exit 0 when the box is usable."
- With `PYTHONIOENCODING=utf-8` the same command exits 0 and produces the full
  report in §2 (13 ok / 1 warn / 0 fail / 1 skip).
- Classification: **confirmed bug**, Windows-specific, pre-existing. Severity:
  medium (the primary first-run diagnostic is unusable by default on the
  Windows-first target platform). Risk: low to fix.
- Proposed fix (downstream slice, not applied here): reconfigure stdout to
  UTF-8 at `main()` entry, or replace non-ASCII report glyphs with ASCII, and
  add a regression test that renders the report under a `cp1252`-style codec.

### F3 — `GET /api/status` is slow on every anonymous call (performance observation)

- Median 190.4 ms over 3 runs (vs 2.63 ms for `/health`), because the handler
  resolves all 46 provider specs and reads the credential store per request.
- Not a failure. Recorded so Gate 4 has a before-number to compare against.

### F4 — hygiene note: stale test bytecode

- `tests/__pycache__/` contains `.pyc` files for test modules with no matching
  `.py` source (e.g. `test_v3`, `test_zzdebug`, `test_debug_handoff`,
  `test_ws_heartbeat_tmp`, `test_system_root_authz`, `test_delegation_handoff`).
- These are **not collected** by pytest (which collects `.py` files) and do not
  affect the 487-test count. Recorded for the hygiene worker; no action here.

## 4. Practical measurements

Demo mode + throwaway DB (`SWARM_DEMO=1`, `PYTHON_DOTENV_DISABLED=1`,
`SWARM_DB_PATH` pointed into the gitignored backup folder). These are warm
in-process `TestClient` timings, not network/WS E2E timings.

| Measurement | Value |
| --- | --- |
| `import backend.main` (cold, includes first import) | 1.715 s |
| `TestClient` startup context manager | ~0 s (startup ran on context enter) |
| `GET /health` median (3 runs) | 2.63 ms (2.26–4.80 ms) |
| `GET /api/status` anonymous median (3 runs) | 190.40 ms (174.23–194.30 ms) |
| `POST /api/register` (first admin, setup token) | 121.59 ms — status 200 |
| Frontend bundle | see §2 table; `index + vendor` JS ≈ 395.76 kB raw / 122.47 kB gzip |
| Peak memory footprint | **not reliably measured** — the ctypes `GetProcessMemoryInfo` probe returned 0 in this environment; `psutil` and `resource` are unavailable. No value is claimed. |
| WS E2E / 3 real agent-task latencies / UX timings | **not measured** in Gate 0; deferred to the eval harness (Gate 4) and the UX worker. No numbers are invented. |

`GET /api/status` in demo mode returned `demo: true`, `llm_ready: true`, with
no groq/openrouter keys — confirming **the deterministic demo path launches
without a paid model**, as Gate 0 requires.

A `GET /api/messages` probe returned 404 (the path needs different parameters);
it is excluded from the latency table rather than reported as a broken endpoint.

## 5. Gate 0 verdict

| Gate 0 requirement | Status | Evidence |
| --- | --- | --- |
| Existing functionality runs | Met | 486 passing backend tests + 98 passing frontend tests; app imports and boots under `TestClient` |
| Tests pass, or real failures catalogued | Met (with catalogued failures) | 1 environment-dependent test failure (F1) reproduced and root-caused; 0 frontend failures |
| Demo launchable without a paid model | Met | `status_body_demo`: `demo=true`, `llm_ready=true` |
| No data touched without backup | Met | Hash-verified backups taken first; tests use a temp DB; doctor is read-only |
| Documentation of truthful results | Met | This file + `PHASE_LOG.md` |

**Gate 0 baseline is established.** Two pre-existing issues are now catalogued
(F1 test hermeticity, F2 doctor Windows encoding) but neither blocks the
baseline or the start of Gate 1/S01 work.

## 6. Scope boundary of this worker

This worker owned **Gate 0 baseline and setup only**. No source code, tests,
database, migration, or UI was changed. The GAP audit, UX spec, threat model,
and implementation slices belong to other workers / later phases. No push was
performed.

## 7. Evidence log locations (all gitignored, under `$REPO/.scratch/`)

```
.scratch/baseline-backups/20261116-221103/
  swarm.db, _test.db, hallmark-preview.db, openrouter-browser-smoke.db   (backups)
  pytest.log, bun-test.log, bun-build.log, doctor.log, doctor-utf8.log
  measure.py, measure.log, measure.db
```

## 8. Phase log

See `docs/product-upgrade/PHASE_LOG.md` for the chronological change record,
commit hash, remaining risks, and the next recommended slice.
