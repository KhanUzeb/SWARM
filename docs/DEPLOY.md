# DEPLOY.md

This deployment targets one VPS. It does not use Kubernetes or split the
database, cache, and object storage into separate services — single-host
simplicity is the point (see docs/PRODUCT.md for scope).

## Build & run

The daemon has to be running first (Docker Desktop on Windows/macOS,
or `dockerd` on a VPS). `docker compose` talks to it; if the engine
isn't up you'll get a pipe/socket error, not a compose YAML error.

```bash
cp .env.example .env
# edit .env: set GROQ_API_KEY, optionally OPENROUTER_API_KEY, LANGFUSE_PUBLIC_KEY / LANGFUSE_SECRET_KEY

docker compose pull
docker compose up -d
```

That pulls the published image, `ghcr.io/khanuzeb/swarm:latest`, which
CI builds for `linux/amd64` and `linux/arm64` and pushes on every `v*`
tag and on `latest`. Docker picks the right architecture; a native
arm64 host (Apple Silicon, Ampere, Graviton) does not run under
emulation. The image is public, so a VPS needs no registry login.

Pin a version rather than tracking `latest` on anything you care about:

```bash
# docker-compose.yml
image: ghcr.io/khanuzeb/swarm:v1.5.0
```

### Build it locally instead

To run an unpushed change, comment out the `image:` line in
`docker-compose.yml` and uncomment the `build:` stanza next to it, then:

```bash
docker compose build
docker compose up -d
```

The image has two stages. Bun builds the React UI, and the Python image
serves `frontend/dist` through uvicorn. Bun is not needed on the VPS.

App is on `http://<host>:8000`. `GET /health` answers `{"status": "ok",
"service": "swarm", "revision": "..."}`, where `revision` is the build's
git SHA when something sets `SWARM_REVISION` (or `GIT_SHA`) at build or
run time, and `dev` when nothing does — which is the case for the
published image today. Pass `SWARM_REVISION=$(git rev-parse HEAD)` in
`docker-compose.yml` if you want a running container to report the
commit it came from.

## Local development

Always use `python -m uvicorn` (not bare `uvicorn`) so the working directory
stays the repo root and `backend` is importable:

```powershell
cd swarm
.\.venv\Scripts\Activate.ps1
python -m uvicorn backend.main:app --reload
```

## First login

Register the first handle in the UI — it becomes **admin**. You can
optionally set a password at creation; it is stored as a PBKDF2 hash
(100k rounds), never plain text. If you set one, reclaiming that admin
handle later requires the password. A browser on the server can register
the first admin and later members from loopback. Remote callers need
`SWARM_SETUP_TOKEN` to bootstrap the first admin. Once an admin exists, a
remote caller reclaiming that handle needs its password, or the setup token
if no password was set; new remote members need `SWARM_INVITE_CODE`.
Without those values remote registration is closed.

If a Bot reply fails (provider down, rate limit, timeout), use **Retry**
on the error bubble. If your own message fails to send, use the banner
above the composer.

## When it won't start: `swarm doctor`

The backend validates its `SWARM_*` variables at startup and **refuses to
boot** on an unusable one, naming the variable and what it expected:

```
invalid configuration: 1 variable(s) cannot be used: SWARM_SYSTEM
  SWARM_SYSTEM='enabled' — not a boolean — use 1/0, true/false, yes/no, on/off
```

To ask the install what is wrong without reading logs, run the doctor. It
checks the interpreter, the package imports, every `SWARM_*` value, whether
provider keys resolve, whether the database path and sandbox directory are
writable, and which risky settings are on.

```bash
# inside the container
docker compose exec swarm python -m backend.doctor

# from a checkout, no server needed
python -m backend.doctor
python cli/swarm_cli.py doctor --json     # same report, machine-readable
```

Exit code is 0 when the install is usable and 1 when something is actually
broken. Warnings (demo mode on, host tools on a public bind, `SWARM_SECRET`
unset) are legitimate local choices and do **not** fail the run — they are
listed under the relevant check so you can decide. It makes no network call
unless you pass `--connectivity`, so it works offline. If **Connect → AI**
returns 409 and names `SWARM_SECRET`, set a private random value in `.env`,
restart, and try again; the same value must be kept to read saved keys.
`SWARM_DEMO=1` does not need one.

Boolean flags accept one vocabulary everywhere: `1`/`true`/`t`/`yes`/`y`/`on`
and `0`/`false`/`f`/`no`/`n`/`off`. `SWARM_BROWSER` is the one exception and
keeps its inverted contract — unset means on.

## Where the data lives

The SQLite file lives at `/app/data/swarm.db` inside the container,
backed by the named volume `swarm-data`. It survives `docker compose
down` / `up`. To actually delete all data:

```bash
docker compose down -v
```

`python cli/swarm_cli.py backup` and `python cli/swarm_cli.py restore` are local
CLI commands (there is no installed `swarm` launcher); they need neither a
server nor a token. Make a backup before any upgrade. `backup` is an **online**
SQLite snapshot and is safe while Swarm is running. `restore` requires a
stopped instance and an explicit operator confirmation.

Swarm uses SQLite in WAL mode (`backend/db.py` sets `PRAGMA journal_mode = WAL`).
A committed change can live in `swarm.db-wal` until SQLite checkpoints it, and a
running process can change the files while you read them. Copying only
`swarm.db` from a live container is not a backup. In a WAL database even the
table definitions may still be in the `-wal` file, so a main-file-only copy can
open as an empty or malformed database.

### Back up (online, preferred)

From a checkout, with a timestamp so two backups on the same day do not
collide:

```bash
python cli/swarm_cli.py backup "./swarm-backup-$(date +%F-%H%M%S).db"
# PowerShell: python cli\swarm_cli.py backup ".\swarm-backup-$(Get-Date -Format yyyyMMdd-HHmmss).db"
# To replace the same artifact deliberately: add --overwrite
```

The container image does not include `cli/`, so call the module directly. Keep
the artifact in `/tmp` (not `/app/data`, which is the named volume that
`down -v` deletes) and copy it to the host:

```bash
docker compose exec swarm python -c "from backend.backup import backup, resolve_db_path; print(backup(resolve_db_path(), '/tmp/swarm-backup.db'))"
docker compose cp swarm:/tmp/swarm-backup.db ./swarm-backup.db
```

JSON in, JSON out. `--db PATH` selects a database other than this install's
`SWARM_DB_PATH`; `--overwrite` replaces an existing destination file. The
command uses SQLite's online backup API, so it reads through the WAL and
captures committed rows even while the server is writing. It does not
checkpoint or delete the source's `-wal`/`-shm` files. `backup` refuses a
destination that is the database itself or one of its `-wal`/`-shm`/`-journal`
sidecars, even with `--overwrite`; `restore` refuses a target that is the backup
file itself or its sidecar. Writing an artifact over a live WAL set can break
the running instance, and a refused operation changes no files. The source is
only ever opened read-only: if SQLite cannot read it through the WAL, the
backup fails rather than opening it read-write and checkpointing the source.
Artifacts are staged under unique names and published without clobbering an
existing file unless `--overwrite` is given.

The artifact is a **single self-contained file** converted out of WAL mode. The
report includes `integrity`, `row_counts` for the four core Swarm tables
(`channels`, `messages`, `users`, `workspace_meta`), a SHA-256 hash and the
byte size. A non-`ok` integrity value or a missing table is a failed backup.
`integrity_check` proves the file is intact, not that it holds the work you
expect — check a known row or a recent run as well:

```bash
BACKUP=./swarm-backup-2026-11-26-171500.db
python -c "import sqlite3,sys; c=sqlite3.connect('file:'+sys.argv[1]+'?mode=ro&immutable=1',uri=True); print(c.execute('PRAGMA integrity_check').fetchone()[0]); print('messages:', c.execute('SELECT COUNT(*) FROM messages').fetchone()[0]); print('latest:', c.execute('SELECT body FROM messages ORDER BY id DESC LIMIT 1').fetchone())" "$BACKUP"
```

Compare the printed count and latest row with what you expect in the live
workspace; `ok` on its own only proves the bytes are structurally intact.

A backup contains the full message history and provider secrets: keep it where
you would keep the original database, and never commit it to a repository.

### Back up a stopped instance (manual fallback)

Set the folder name once and use the same value in every command below. Running
`date +%F` again in a later step can cross midnight and point the verify step at
a different folder.

```bash
STAMP=$(date +%F)
```

1. Stop the service so no process holds the database open:

   ```bash
   docker compose stop swarm
   ```

   Do not use `docker compose down -v` here; `-v` deletes the volume.

2. Copy the whole data directory, not just `swarm.db`:

   ```bash
   docker compose cp swarm:/app/data "./swarm-backup-$STAMP"
   ```

   The copy should contain `swarm.db` and, if they exist at that moment,
   `swarm.db-wal` and `swarm.db-shm`. Keep all files together. A clean stop
   usually checkpoints the WAL and removes those two files, which is fine. What
   matters is that you stopped first and copied every database file present.

3. Verify the copy. The image has no `sqlite3` CLI, so use Python on the host.
   The read-only URI matters: a plain `connect()` creates an empty database at a
   mistyped path, and `integrity_check` still prints `ok`.

   ```bash
   python -c "import sqlite3,sys; c=sqlite3.connect('file:'+sys.argv[1]+'?mode=ro', uri=True); print(c.execute('PRAGMA integrity_check').fetchone()[0])" "./swarm-backup-$STAMP/swarm.db"
   ```

   Expect `ok` with a zero exit code. A missing path fails with "unable to open
   database file" and creates nothing; treat that as a failed backup. Run this
   on a throwaway copy if the backup must stay bit-for-bit unchanged, because
   opening a WAL database can checkpoint it on close. Then check a known row or
   a recent run: `integrity_check` proves the file is intact, not that it holds
   the work you expect.

If you run these commands in separate shells, write the value of `$STAMP` down
and paste the same literal date into each command instead of relying on the
shell variable.

### Restore (stopped instance only)

From a checkout, stop uvicorn first:

```bash
python cli/swarm_cli.py restore ./swarm-backup-2026-11-26-171500.db --confirm-stopped
# PowerShell: python cli\swarm_cli.py restore .\swarm-backup-2026-11-26-171500.db --confirm-stopped
```

For Docker, `exec` cannot run against a stopped service, so stop it, copy the
backup into the named volume, and use a one-off container with that same
volume:

```bash
docker compose stop swarm
docker compose cp ./swarm-backup.db swarm:/app/data/restore.db
docker compose run --rm swarm python -c "from backend.backup import restore, resolve_db_path; print(restore('/app/data/restore.db', resolve_db_path(), confirm_stopped=True))"
```

`--confirm-stopped` is mandatory and is an **attestation, not a detection**. No
SQLite lock can prove that no process will reopen the file a moment later, so
the command does not pretend to detect a stop: check the process yourself.
Restoring over a live database can corrupt it. `--db PATH` targets a database
other than this install's `SWARM_DB_PATH`.

Before changing the target, the command verifies the backup read-only
(`integrity_check` plus the required Swarm tables), then applies these rules:

1. A backup with its own `-wal`/`-shm`/`-journal` sidecars is refused — the
   bytes are ambiguous. Swarm's own backups never have sidecars.
2. A target with any such sidecar is refused. A clean stop normally checkpoints
   and removes them, so leftovers mean "investigate"; Swarm **never deletes**
   them for you.
3. The backup is copied to a uniquely named temp file and verified there. An
   existing target is preserved under `<name>.pre-restore-<timestamp>-<pid>`
   — never deleted — by a hard link (or a verified copy where hard links are
   unavailable) **before** the verified temp file replaces it, so a crash at
   that moment cannot leave the target path with no database.
4. The placed file is verified again. If that check fails, the previous target
   is moved back and the newly written file is removed. A rollback failure is
   reported with the preserved path instead of being swallowed.

Copying a saved `swarm.db` over a volume by hand remains unsafe even when the
service is stopped: a newer `swarm.db-wal`/`swarm.db-shm` left from the old
database can survive the copy, and SQLite replays those sidecars on open, so the
result can look healthy while returning data from before the restore. Ownership
matters too — the container runs as `swarm` — and a database from another Swarm
version runs additive migrations at startup (`ensure_schema()`).

**Verification and limits.** Restore is exercised on disposable databases for
WAL-resident rows, sidecar refusal on both sides, roundtrip with the previous
file preserved, rollback when the final check fails, and a simulated failure at
the publish step that must leave the original target readable. It has **not**
been run against a disposable Docker volume, followed by a boot and migration
smoke test. There is no multi-process kill test: nothing here proves the server
stayed stopped. A `<target>.swarm-lock` file serialises backup/restore per
target; a second operation refuses with the lock path rather than racing.
A lock left by a crashed process is **not** auto-broken — confirm nothing is
running, then remove it yourself. A backup covers the SQLite database only —
sandbox files and anything else outside it are not included. Test on a
disposable volume before touching a workspace whose work you need, and keep
the `pre-restore` file until the restored server has been running and you have
checked a known row or recent run.

For a local checkout without Docker, the default database is
`backend/swarm.db` when `SWARM_DB_PATH` is unset; stop uvicorn first. The
stopped-instance copy in the previous section is a manual alternative, but it
copies a set of files and is not the verified restore path.

## Env vars

| var | required | purpose |
|---|---|---|
| `SWARM_SECRET` | no (required to connect) | seals stored provider keys; set a stable private value before Connect → AI or the connect attempt returns 409. `SWARM_DEMO=1` needs none; rotating it makes saved keys need reconnecting |
| `SWARM_SETUP_TOKEN` | no | remote bootstrap proof for the first admin; loopback setup does not need it |
| `SWARM_INVITE_CODE` | no | remote member registration; without it remote registration is closed |
| `GROQ_API_KEY` | yes (or OpenRouter) | Groq inference |
| `OPENROUTER_API_KEY` | no | fallback if Groq is down or unset |
| `OPENROUTER_MODEL` | no | OpenRouter model slug; mapped from the agent model if unset |
| `SWARM_AGENT_MODEL` | no | overrides every agent; use `openai/gpt-oss-120b` or `openai/gpt-oss-20b` |
| `SWARM_SANDBOX_DIR` | no | default `/tmp/swarm-sandbox` (POSIX) / `%TEMP%\swarm-sandbox` (Windows); rebind at runtime from Places → Sandbox (`POST /api/computer/sandbox/root`, admin) |
| `SWARM_SYSTEM` | no | defaults to `0`; set `1` to enable host-system tools (see below) |
| `SWARM_SYSTEM_ROOT` | no | host path Bots may read/write (Dockerfile sets `/app`) |
| `SWARM_SYSTEM_PLACES` | no | extra paths offered as Places in Places → System; any folder on the machine, read-only entries |
| `SWARM_SYSTEM_UNRESTRICTED` | no | set `1` to allow filesystem root as System or Sandbox root (entire PC) |
| `SWARM_DB_PATH` | no | set by the Dockerfile to `/app/data/swarm.db` |
| `SWARM_HOST` / `SWARM_BIND` | no | informational bind address; only a declared non-loopback bind trips the startup exposure warning and `swarm doctor` |
| `SWARM_OAUTH_REDIRECT_URI` | no | OAuth callback for Connect → AI; defaults to `http://localhost:8000/api/v2/oauth/callback` |
| `SWARM_GOOGLE_OAUTH_CLIENT_ID` / `SWARM_GOOGLE_OAUTH_CLIENT_SECRET` | no | OAuth client for Google Gemini (PKCE); without them the OAuth button stays hidden |
| `SWARM_SQLITE_BUSY_TIMEOUT_MS` | no | SQLite busy timeout; defaults to 5000, never negative |
| `OPENAI_API_KEY` | no | OpenAI direct |
| `ANTHROPIC_API_KEY` | no | Anthropic direct |
| `GOOGLE_API_KEY` | no | Google Gemini |
| `MISTRAL_API_KEY` | no | Mistral |
| `DEEPSEEK_API_KEY` | no | DeepSeek |
| `XAI_API_KEY` | no | xAI (Grok) |
| `FIREWORKS_API_KEY` | no | Fireworks AI |
| `PERPLEXITY_API_KEY` | no | Perplexity |
| `NVIDIA_API_KEY` | no | NVIDIA NIM |
| `OPENCODE_API_KEY` | no | OpenCode |
| `ZEN_API_KEY` | no | Zen |
| `GITHUB_TOKEN` | no | GitHub Models |
| `AZURE_OPENAI_API_KEY` | no | Azure OpenAI (use Custom with your deployment URL) |
| `LLAMA_API_KEY` | no | Meta Llama API (llama.cpp server shares this name in code — prefer Custom or a distinct key for one of them) |
| `CLOUDFLARE_API_KEY` | no | Cloudflare Workers AI |
| `VERCEL_AI_GATEWAY_API_KEY` | no | Vercel AI Gateway |
| `OLLAMA_CLOUD_API_KEY` | no | Ollama Cloud |
| `MINIMAX_API_KEY` / `QIANFAN_API_KEY` / `TENCENT_HUNYUAN_API_KEY` / `ARK_API_KEY` / `YI_API_KEY` / `STEPFUN_API_KEY` | no | China / regional (MiniMax, Baidu Qianfan, Tencent Hunyuan, Doubao Ark, 01.AI Yi, StepFun) |
| `UPSTAGE_API_KEY` / `WATSONX_API_KEY` | no | Upstage Solar / IBM watsonx.ai |
| `SWARM_GC_MAX_AGE_HOURS` / `SWARM_GC_MAX_MB` | no | sandbox GC bounds (72h / 512MB); refuses filesystem roots, home, repo checkouts |
| `LANGFUSE_PUBLIC_KEY` / `LANGFUSE_SECRET_KEY` | no | tracing degrades silently if unset |

An unusable value in `SWARM_SYSTEM`, `SWARM_DEMO`, `SWARM_SYSTEM_UNRESTRICTED`
or `SWARM_COMPUTER_PROVIDER` stops the boot with the variable named; run
`python -m backend.doctor` to see them all at once. The rest fall back to
their documented defaults — in particular a nonsense `SWARM_RETENTION_DAYS`
means "keep everything", never "delete everything".

### Host-system tools

`SWARM_SYSTEM` defaults to `0` in the backend: host-system tools
(`system_run`, `system_read`, `system_write`) are **off**, and Bots work
only inside their sandbox. They run commands on the machine that runs the
backend, so opting in is deliberate. Set `SWARM_SYSTEM=1` to enable them,
and point `SWARM_SYSTEM_ROOT` at the directory Bots may touch (it defaults
to the app directory). Leave it off on anything reachable from a LAN or
the internet — see SECURITY.md.

The image agrees with that default: the `Dockerfile` sets
`ENV SWARM_SYSTEM=0`, so a plain `docker compose up` comes up with
host tools off. Compose's `env_file` overrides image `ENV`, so setting
`SWARM_SYSTEM=1` in `.env` is all it takes to turn them on.

When host tools are enabled *and* the server binds to anything other than
loopback, startup logs a warning naming the bind address and the effective
`SWARM_SYSTEM_ROOT`. That is a reminder, not a control.

## Updating

```bash
git pull
docker compose pull
docker compose up -d
```

If you switched to local builds, `docker compose build` replaces the
`pull` instead.

The volume persists across rebuilds. On startup, `ensure_schema()` adds
missing agent harness columns and creates `agent_memory` when needed. There
is no separate migration command.
