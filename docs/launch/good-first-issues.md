# Good first issues — drafts for the maintainer to file

These are written to be **filed**, not worked: drafts for the maintainer to
post when there is room, deliberately small and self-contained. Each is
one evening for a first-time contributor, and every file named below
exists in the tree today.

House rules for any of these PRs (from `AGENTS.md` and
`CONTRIBUTING.md`):

- `pytest -q` green; `cd frontend && bun run build` green if the frontend
  is touched.
- Tests stay deterministic and offline. Groq is never called.
- No orchestration logic in JSX; the backend owns it.
- Quote any new user-facing copy in the PR and say why progressive
  disclosure would not work instead.
- Nothing machine-specific in the diff: no local paths, usernames,
  hostnames, or real keys.

---

### 1. Make `docker compose up` inherit the safe host-tools default

The backend now defaults `SWARM_SYSTEM` to off (`enabled()` treats unset
as off) and the prose docs say so, but **`Dockerfile` still hardcodes
`ENV SWARM_SYSTEM=1`** and `docker-compose.yml` does not override it. So
the one documented way to run Swarm — `docker compose up` — still boots
with host-system tools on, while `python -m uvicorn` boots with them off.
`docs/DEPLOY.md` calls this out in a warning box; that is the drift this
issue closes.

The container case matters more than the local one: the compose file
publishes port `8000`, so "came up with host tools on" is the default
experience for anyone who deploys it.

**Files:** `Dockerfile`, `docker-compose.yml`, `docs/DEPLOY.md`

**Accept when:**
- `Dockerfile` no longer sets `SWARM_SYSTEM=1`. If it is changed to `0`,
  an operator who genuinely wants host tools can set `SWARM_SYSTEM=1` in
  `.env` — say in the PR that this path was checked, since compose's
  `env_file` does override image `ENV`.
- `README.md` and `.env.example` stay consistent with whatever is chosen;
  `.env.example` currently ships `SWARM_SYSTEM=0`, which should keep
  agreeing with the effective default.
- The warning box in `docs/DEPLOY.md` is rewritten to describe the new
  behaviour rather than a caveat that no longer applies, or removed if
  the drift it described is gone.
- `tests/test_system_default.py` still passes unchanged.
- No change to what `system_run` is allowed to do — only to whether it
  is available by default.

---

### 2. Send the auth header in the CLI's read-only subcommands

`cli/swarm_cli.py` calls `_request(..., auth=True)` for the writes
(`post`, `react`, `create-agent`, `patch-agent`) but not for the reads:
`channels`, `history`, `agents`, and `agent` all send no `Authorization`
header, and every one of those endpoints requires a bearer token. Run
them and you get a 401 and `{"error": 401, ...}` on stderr.

**Files:** `cli/swarm_cli.py`

**Accept when:**
- The four read subcommands pass `auth=True`, or a deliberate
  explanation is left in a comment for any that must stay anonymous
  (none should — all four targets are `require_auth`).
- `SWARM_TOKEN` unset produces the existing friendly message rather than
  a traceback.
- `channels`, `history`, `agents`, and `agent` each work end-to-end
  against a local server with a token exported, and the JSON on stdout is
  unchanged.
- Nothing else in the file changes shape — no new dependencies.

---

### 3. Unit-test the origin allowlist and client-marker helpers

`backend/security.py` decides whether a browser may call `/api/*`: an
`Origin` outside `SWARM_ALLOWED_ORIGINS` gets 403, and a browser-origin
call without `X-Swarm-Client: web` gets 403. `tests/test_api.py` covers
the middleware end-to-end for exactly one hostile `Origin`, but **no test
calls `allowed_origins()`, `origin_allowed()`, or exercises a custom
`SWARM_ALLOWED_ORIGINS`** — which is the branch an operator has to touch
to deploy behind TLS, so it is the branch most likely to be wrong.

**Files:** `backend/security.py` (read only unless a bug turns up), new
`tests/test_security.py`

**Accept when:**
- `allowed_origins()` returns the four localhost defaults when the env
  var is unset, and exactly the comma-split set when it is set
  (whitespace and trailing slashes tolerated).
- `origin_allowed(None)` is true, a configured origin is true, and an
  unlisted one is false.
- Through the `client` fixture from `tests/conftest.py`, with
  `SWARM_ALLOWED_ORIGINS` set to a non-localhost value: that origin gets
  through, an origin outside it still gets 403, and an allowed origin
  **without** `X-Swarm-Client: web` still gets 403.
- Requests with no `Origin` header (the CLI, curl, the WebSocket) are
  unaffected — the guard keys on the header being present.
- Tests restore any env var they set, and no test hits the network.

---

### 4. Resolve the duplicated tool list in the frontend

`frontend/src/lib.js` exports `ALL_TOOLS`, a hand-maintained list of 33
tool names. The backend's `BUILTIN_SCHEMAS` in
`backend/tools/registry.py` has 35 — the frontend copy is missing
`create_agent` and `delegate_task`. `AGENTS.md` §2 ("one source of
truth") and the comment at the top of `ui.jsx` (which exists because two
API clients had already drifted) both point at exactly this failure mode.
Meanwhile `ALL_TOOLS` is imported by `ui.jsx` and used by nothing, so the
copy is also dead weight.

**Files:** `frontend/src/lib.js`, `frontend/src/ui.jsx`

**Accept when:**
- `ALL_TOOLS` is either deleted (along with its re-export) or reduced to
  something the UI genuinely reads from `GET /api/tools`, which already
  returns `name`, `kind`, and `description` per tool. Pick one and say
  why in the PR.
- No component loses a tool name it was relying on — `bun run build`
  green, and the tool pickers in `frontend/src/ai-support/ToolsPanel.jsx`
  and `frontend/src/ui.jsx` still render the full catalog.
- No new copy in the diff.

---

### 5. Cover the frontend's pure formatters with `bun test`

`frontend/src/lib.js` exports pure helpers — `fmtTime`, `fmtBytes`,
`initials`, `slugFromName`, `shortModel`, `statusLabel`,
`isAgentError`, `isResumable`, `groupedWith` — and not one of them has a
test. `isAgentError` and `isResumable` decide whether a failed reply shows
a **Retry** affordance, which is the difference between a user fixing a
provider outage in ten seconds and filing a bug. `frontend/package.json`
already has a `test` script and a precedent in
`frontend/src/work/workUtils.test.js`.

**Files:** `frontend/src/lib.js` (read only unless a bug turns up), new
`frontend/src/lib.test.js`, `frontend/package.json`

**Accept when:**
- `bun test` runs the new file as well as the existing one (update the
  `test` script rather than replacing it).
- At least: `fmtBytes` boundaries (0, 1023, 1024, a megabyte), `shortModel`
  truncation and empty input, `statusLabel` for all three agent statuses
  plus an unknown one, and `isAgentError` / `isResumable` on both the
  `[agent error: …]` and cutoff-marker shapes.
- The tests import from `lib.js` directly and touch no DOM, no React, and
  no network — same rule as `workUtils.test.js`.

---

### 6. Warn when `SWARM_COMPUTER_PROVIDER` is set to an unknown value

`get_provider()` in `backend/computer_providers.py` reads the env var,
and any value outside `("local", "none", "fake")` silently becomes
`local`. A typo like `locla` therefore boots the workspace with host
computer tools on and no indication anything was wrong — the opposite of
what the operator asked for. The status payload does report the resolved
provider, but only to someone who knows to look.

**Files:** `backend/computer_providers.py`,
`tests/test_rakazo_parity.py` (the existing `bogus-value` assertion lives
there, not in `test_computer.py`)

**Accept when:**
- An unrecognised value still resolves to `local` (the existing test
  `test_computer_provider_helpers_offline` in `tests/test_rakazo_parity.py`
  asserts this and must keep passing — the fallback is the behaviour,
  only the silence changes).
- The module logs a warning naming the offending value and the three
  valid options, once, and never raises.
- `status()` is unchanged; it must still never raise, including when the
  sandbox directory cannot be created.
- New coverage for the warning, added alongside the existing bogus-value
  case rather than replacing it.

---

### 7. Pin the builtin tool count so the docs cannot drift

`docs/CHANGELOG.md` says "34 builtins". `BUILTIN_SCHEMAS` in
`backend/tools/registry.py` has 35, and `ALL_TOOLS` on the frontend has
33. Three numbers, no test. `DEFAULT_BUILTIN_TOOLS` is already defined as
`list(BUILTIN_TOOL_NAMES)`, so the internal invariant is trivially
assertable — only the prose needs a human.

**Files:** `backend/tools/registry.py` (read only), `tests/test_tools_ai.py`,
`docs/CHANGELOG.md`

**Accept when:**
- A test asserts `DEFAULT_BUILTIN_TOOLS == list(BUILTIN_SCHEMAS)` and
  `set(DEFAULT_BUILTIN_TOOLS) == set(BUILTIN_SCHEMAS)`, so a schema added
  without updating the default list fails loudly.
- A test asserts no name in `BUILTIN_SCHEMAS` is missing from
  `SPAWN_DEFAULT_TOOLS` unless it is in the documented exclusion set
  (`create_agent` plus the `system_*` tools).
- `docs/CHANGELOG.md`'s count matches the code.
- The comment on `SPAWN_DEFAULT_TOOLS` in the source (it explains the
  anti-loop and host-access reasoning) is left intact or improved, not
  deleted.

---

### 8. Test that every bundled skill has a file behind it

`BUNDLED_SKILL_NAMES` in `backend/bundled_skills.py` is a hand-kept tuple
of ten names that the module promises to load from `skills/<name>.md`. A
silent miss would mean `/digest` simply not existing, with no error
anywhere — `load_bundled_skills` already skips a missing file with a bare
`continue`. It also has no test at all.

**Files:** `backend/bundled_skills.py` (read only), new
`tests/test_bundled_skills.py`

**Accept when:**
- Every name in `BUNDLED_SKILL_NAMES` has a non-empty
  `skills/<name>.md` in the repository.
- `load_bundled_skills()` returns one entry per name, in tuple order, each
  body non-empty and within the 8000-character cap the module applies.
- The test uses a path relative to the module (`ROOT`), so it does not
  depend on the working directory.
- No skill body is edited as part of this PR.

---

### 9. Pin the relationship between job templates and job profiles

`JOB_TEMPLATES` in `backend/jobs.py` has nine entries; each one's `id`
has a matching file in `profiles/jobs/` (verified: all nine resolve), and
`GET /api/jobs` already attaches the profile body via
`load_job_profile`. But that relationship is held together by convention
— one dict in one module, nine filenames in one directory — and
`tests/test_profiles.py` only checks the seeded bots and one job lookup.
Rename one `id` and the job silently ships with an empty profile and a
`200` that looks fine.

Note that `AGENT_TEMPLATES` in `backend/ai_support/agent_templates.py`
is a **separate** list that `job_id_for_title` does *not* consult — it
matches against `JOB_TEMPLATES`. Worth a comment in the test saying so,
because the two lists look interchangeable and are not.

**Files:** `backend/jobs.py` (read only), `backend/profiles.py` (read
only), `tests/test_profiles.py`

**Accept when:**
- For every entry in `JOB_TEMPLATES`, `load_job_profile(id)` returns a
  non-empty body, and `profiles/jobs/<id>.md` exists in the repository.
- The test fails with the offending job id named in the message.
- A test notes (in a comment or an explicit case) that
  `job_id_for_title` resolves against `JOB_TEMPLATES`, not
  `AGENT_TEMPLATES`, and asserts a title that only appears in
  `AGENT_TEMPLATES` does **not** resolve — pinning the current behaviour
  so a future change is deliberate.
- `list_profiles()` returns exactly one `kind: "job"` row per file in
  `profiles/jobs/`.
- No job id is renamed and no profile body is edited as part of this PR.

---

### 10. Document the env vars the code reads but the deploy guide omits

`docs/DEPLOY.md` has an env-var table, and several knobs an operator will
hit are missing from it: `SWARM_GC_MAX_AGE_HOURS` and `SWARM_GC_MAX_MB`
(the sandbox sweeper's retention and size caps, read by `backend/gc.py`
and never documented anywhere outside that file's docstring),
`SWARM_BROWSER` (the Playwright kill switch), `SWARM_ALLOWED_ORIGINS`,
and `SWARM_COMPUTER_PROVIDER`. Conversely, `OPENROUTER_MODEL` and
`BROWSER_USE_BIN` appear in `.env.example` but are read nowhere in the
code — the browser-use binary is resolved with `shutil.which` in
`backend/tools/connectors.py`, not from the env var.

**Files:** `docs/DEPLOY.md`, `.env.example`

**Accept when:**
- Every var named in the issue above has a row with required/no and a
  one-line purpose, matching the existing table's format.
- `OPENROUTER_MODEL` and `BROWSER_USE_BIN` are either implemented
  (small, and called out in the PR) or removed from `.env.example` with a
  note in the PR explaining which. Leaving a var documented but unread is
  the exact failure this issue is about.
- `SWARM_SYSTEM_ROOT` keeps its "do not override unless you know why"
  warning, and the filesystem-root refusal stays documented.
- No code behaviour changes as a side effect.
