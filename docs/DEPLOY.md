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
image: ghcr.io/khanuzeb/swarm:v0.1.0
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
handle later requires the password. Later users register as **members**
without a password gate.

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
docker compose exec api python -m backend.doctor

# from a checkout, no server needed
python -m backend.doctor
python cli/swarm_cli.py doctor --json     # same report, machine-readable
```

Exit code is 0 when the install is usable and 1 when something is actually
broken. Warnings (demo mode on, host tools on a public bind, `SWARM_SECRET`
unset) are legitimate local choices and do **not** fail the run — they are
listed under the relevant check so you can decide. It makes no network call
unless you pass `--connectivity`, so it works offline.

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

To back it up:

```bash
docker compose cp swarm:/app/data/swarm.db ./swarm-backup-$(date +%F).db
```

## Env vars

| var | required | purpose |
|---|---|---|
| `GROQ_API_KEY` | yes (or OpenRouter) | Groq inference |
| `OPENROUTER_API_KEY` | no | fallback if Groq is down or unset |
| `OPENROUTER_MODEL` | no | OpenRouter model slug; mapped from the agent model if unset |
| `SWARM_AGENT_MODEL` | no | overrides every agent; use `openai/gpt-oss-120b` or `openai/gpt-oss-20b` |
| `SWARM_SANDBOX_DIR` | no | set by the Dockerfile, don't override unless you know why |
| `SWARM_SYSTEM` | no | defaults to `0`; set `1` to enable host-system tools (see below) |
| `SWARM_SYSTEM_ROOT` | no | host path Bots may read/write (Dockerfile sets `/app`) |
| `SWARM_DB_PATH` | no | set by the Dockerfile to `/app/data/swarm.db` |
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
