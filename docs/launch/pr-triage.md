# Dependency PR triage — launch gate

Four Dependabot bumps were open against `requirements.txt`. This is a written
recommendation only; nothing was merged, closed, commented on, or pushed.

## Summary

| PR | Package | Declared delta | What CI actually installed | CI | Recommendation |
|----|---------|----------------|----------------------------|----|----------------|
| #26 | `uvicorn[standard]` | `>=0.52.4` → `>=0.53.0` | `0.54.0` (same as `main`) | backend + frontend pass | **MERGE** |
| #25 | `openai` | `>=3.11.0` → `>=3.19.2` | `3.19.2` (same as `main`) | backend + frontend pass | **REQUEST CHANGES** |
| #24 | `pydantic` | `>=2.7` → `>=2.13.5` | `2.13.5` (same as `main`) | backend + frontend pass | **MERGE** (with a follow-up ticket) |
| #23 | `langfuse` | `>=4.15.2` → `>=4.15.4` | `4.15.6` (same as `main`) | backend + frontend pass | **MERGE** |

One-line reasons:

- **#26 MERGE** — additive and opt-in only; the HTTP/2 work needs an extra package that is not installed, and the one behavioural default change (`FORWARDED_ALLOW_IPS`) is inert here because the app never enables proxy headers.
- **#25 REQUEST CHANGES** — the largest jump, it lands on the agent's hot path, and the SDK surface we use has zero test coverage, so a green CI is not evidence. The one-line diff also hides a transitive HTTP transport change.
- **#24 MERGE** — pydantic's own policy calls 2.x minors non-breaking, and `main` is already running exactly `2.13.5` green, so this is a documentation-only floor change. Separately, the validator warnings it surfaces are a pre-existing bug in our own models.
- **#23 MERGE** — two patch releases, both fixes, and the affected code is wrapped in `try/except` that already treats tracing as optional.

## Read this before reading the rest

Every pin in `requirements.txt` is an open-ended `>=` floor with no upper bound
and there is no lockfile for the Python side. Consequence: **all four PRs install
byte-identical dependency sets**, and so does `main`. Verified from the install
lines in each run — `main`'s most recent push and all four PR runs resolved to
the same set:

```
pydantic 2.13.5 / pydantic-core 2.46.5   openai 3.19.2
uvicorn 0.54.0 / starlette 1.7.0         langfuse 4.15.6
fastapi 0.141.1 / httpx 0.28.1
```

Two implications that shape every recommendation below:

1. **Green CI on these PRs proves very little.** It proves the resolved versions
   work together, not that the specific version each PR names was validated.
2. **The real blast radius is not what the diff shows.** A floor bump looks like
   one line; what it actually admits is everything above it.

## PR #26 — uvicorn 0.52.4 → 0.53.0

**Change.** `uvicorn[standard]>=0.52.4` → `>=0.53.0`. One line in `requirements.txt`.

**Upstream, 0.53.0.** Two additions and three fixes:

- Experimental HTTP/2 via a new `zttp` integration, enabled with `--http zttp --http2`. Requires installing `zttp` separately.
- Support for a `zuvloop` event loop, selected with `--loop zuvloop` on CPython 3.14+.
- Fix: comma-separated / case-insensitive `Connection: close` tokens.
- Fix: `::1` added to the default `FORWARDED_ALLOW_IPS` value.
- Fix: the HTTP keep-alive timer is now cancelled when a connection upgrades to WebSocket.

**Risk: low.** Both additions are opt-in behind flags we do not pass, and the
package that enables them is not in our dependency set — so the feature is
unreachable by accident. The `FORWARDED_ALLOW_IPS` default change is the only
behavioural default shift, and it is inert here: nothing in the app sets
`proxy_headers`, and the only proxy-adjacent code reads `request.client.host`
directly for a loopback check.

The WebSocket keep-alive fix is in our favour. WebSocket use is concentrated in
the main app module and the recent 0.52.x line was actively fixing
`websockets-sansio` truncation and close-handshake bugs, so this release
continues that repair work rather than adding risk.

**CI:** pass, both jobs. `138 passed, 2 warnings`.

**Recommendation: MERGE.** The change is additive and opt-in, the default that
did move does not affect this deployment, and the version CI exercised is newer
than the one being named, which is the safe direction.

**Maintainer verification (cheap, but do it anyway):** start the container
image and confirm the healthcheck goes green and a WebSocket session both opens
and closes cleanly. That exercises the keep-alive fix, which no test currently
does.

## PR #25 — openai 3.11.0 → 3.19.2

**Change.** `openai>=3.11.0` → `>=3.19.2`. One line in `requirements.txt`.

**Delta.** Eight minor versions. Nothing here is flagged breaking upstream, but
several releases touch code we depend on directly:

- `chat:` preserve single-pass tool iterables (3.19.1)
- `client:` retry only replayable request content (3.19.0) — changes retry
  eligibility, which is on our failure path
- `client:` merge HTTP headers case-insensitively (3.19.1)
- `lib:` treat null message content as empty in `parse_response` (3.17.0)
- `client:` tolerate older optional aiohttp installations; `helpers:` use
  `asyncio.get_running_loop()` inside async methods (3.19.0)
- new model identifiers and assorted `api:` type/doc regenerations

**Risk: this is the highest-risk bump of the four.** The provider layer is the
agent's critical path. It constructs `AsyncOpenAI` clients against several
OpenAI-compatible endpoints and streams completions with tool schemas enabled,
plus two non-standard extras: `reasoning_effort` and `include_reasoning=False`.

The problem is not the changelog, it is the coverage. **No test in this repo
imports `openai` or exercises the streaming path.** There is no stub client, no
recorded fixture, no monkeypatched response — the streaming helper and the client
builder have zero automated coverage. So the 138 passing tests would be equally
green if the SDK had removed `chat.completions.create` entirely. A green check on
this PR is close to zero information about whether the agent still works.

Second, the one-line diff understates the change. The SDK pulls in a
replacement HTTP transport (`httpx2` / `httpcore2`) as a hard dependency of
this range. That is a swap of the layer every provider request goes through,
invisible in a diff that shows only a version floor.

**CI:** pass, both jobs. `138 passed, 2 warnings` — see the coverage caveat above.

**Recommendation: REQUEST CHANGES.** Not because the bump looks wrong, but
because this is the one PR here where the risk is real, the surface is
completely untested, and the green check tells us nothing. Asking for changes is
the honest way to convert an untested 8-minor jump on the hot path into
something with evidence behind it. Concretely, ask for a test that builds the
client through the existing builder and drives one streamed completion through a
stubbed tool-call response, asserting the tool-call reassembly and the usage
totals. If that lands, this merges immediately.

**Maintainer verification before merge:**

1. Smoke the real path, not a mock: one streamed agent turn with tool calls
   against a real provider, confirming token streaming, tool invocation, and the
   follow-up `tool` message.
2. Confirm `include_reasoning=False` still reaches the wire. It is a
   non-standard extra field passed through kwargs; if a newer SDK starts
   validating or stripping unknown fields, reasoning tokens silently leak into
   the billable output instead of failing loudly.
3. Confirm the retry change did not narrow retries for streaming requests. A
   mid-stream failure is handled by returning partial content plus a cutoff
   note, so a narrower retry could turn a recoverable blip into a truncated
   reply.
4. Re-check the `httpx2` / `httpcore2` transport against any proxy or TLS
   interception in the deployment environment.

## PR #24 — pydantic 2.7 → 2.13.5

**Change.** `pydantic>=2.7` → `>=2.13.5`. One line in `requirements.txt`.

**Delta.** The largest *declared* jump on paper — roughly seven minor versions —
which is why it deserves the closest look. Upstream, 2.x minors are explicitly
non-breaking under pydantic's published versioning policy, with a standing
caveat that each release lists a handful of "minor changes" that are also
non-breaking but worth reading. Scanning the 2.11 → 2.13.5 releases for anything
that touches us, I found nothing: no change to `model_validator` /
`field_validator` semantics, no discriminator or union-format change that
bites, no change to `model_copy`, `model_dump`, or JSON Schema generation in the
ways this app uses them.

Empirically it is already settled too. `main` is running `pydantic 2.13.5` today
against a green `138 passed`. This PR changes what we *promise*, not what we
*run*.

**The validator warnings — real, but not this PR's fault.** The test output
carries two `UserWarning: A custom validator is returning a value other than
self`. I traced them. They come from constructing `AgentCreate` in the model
alias test, and the cause is our own code: the after-validators on `AgentCreate`
and `TeamCreate` normalise fields by returning `self.model_copy(update=...)`,
which is a *different* object from `self`. Pydantic warns because it cannot
assume the returned instance is the same one it handed you.

Two things follow, and they are worth being precise about:

- These warnings are **pre-existing**. They appear in `main`'s own most recent
  green run. They are not introduced by this bump.
- They are still a latent bug. The entire slug / display-name normalisation for
  agent handles and team ids depends on that returned copy being honoured. It
  works today. It is a documented-iffy pattern, and if pydantic ever enforces
  returning `self`, normalisation stops applying silently — no exception, just
  un-slugged handles reaching the database.

**Risk: low for the PR, moderate for the code it points at.**

**CI:** pass, both jobs. `138 passed, 2 warnings`.

**Recommendation: MERGE.** The floor change is honest and matches what already
runs green. Do not hold this PR hostage to a pre-existing code issue.

**Follow-up ticket, separate from this PR:** change those two after-validators to
mutate and `return self`, or move the normalisation into a `mode="before"`
validator. Cheap, removes the warnings, and closes a real fragility before
pydantic makes it a bug. Worth doing before the next pydantic minor.

**Maintainer verification:** `pytest -W error::UserWarning` on the models tests
to confirm nothing else is hiding behind warnings that are currently tolerated.

## PR #23 — langfuse 4.15.2 → 4.15.4

**Change.** `langfuse>=4.15.2` → `>=4.15.4`. One line in `requirements.txt`.

**Upstream.** Two patch releases:

- 4.15.4 — `fix(serializer): mask pydantic secret values`
- 4.15.5 — (already installed on `main`)

**Risk: lowest of the four.** Tracing is optional and best-effort by design:
the client is only constructed when tracing keys are present, and both
construction and every trace call are wrapped so that any failure degrades to
"no tracing" instead of breaking the agent. Test setup explicitly unsets the
tracing keys, so CI never constructs the client at all — which also means CI
cannot regress it. The `mask pydantic secret values` fix is a security
improvement, not a behaviour change, and it composes well with the pydantic
models we pass through it.

Worth flagging for accuracy rather than risk: the newest release in the 4.15
line captures reasoning-effort and verbosity in model parameters, which is
observability, and `main` is already on a version past the one this PR names.

**CI:** pass, both jobs.

**Recommendation: MERGE.** Two patches, both fixes, on an optional subsystem
that cannot take down the agent.

**Maintainer verification:** point a staging deployment at a real tracing project
and confirm traces arrive, since no test covers this path at all. Cheap, and the
only way to get any signal here.

## Cross-cutting notes

**The green checks are near-worthless as evidence for this batch.** All four PRs
resolve to the same dependency set as `main`, which is green. Merging them does
not change what gets installed today; it only raises the declared floor. That is
a fine thing to do and worth doing, but it means the four "passing" badges
should not be read as four independent verifications.

**Two hot-path surfaces have no automated coverage**, and both are touched by
this batch:

- the OpenAI-compatible client and streaming path (PR #25)
- the Langfuse tracing path (PR #23)

Anything that changes behaviour in those libraries will be discovered in
production, not in CI. Adding stub-based tests for both is the highest-leverage
follow-up here, and it converts future Dependabot bumps from "click merge and
hope" into something with an actual gate.

**If you would rather not carry four Dependabot PRs per week**, the coherent
alternative to merging all four is: close them, and adopt a lockfile (or
`--upgrade-package` constraints) for the Python side so bumps become reviewable
diffs instead of floor edits, with a single scheduled bump PR per cycle. That is
a policy call, not a correctness one — the individual recommendations above
stand on their own either way.