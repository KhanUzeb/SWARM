# DEPLOY.md

Single-VPS deployment. No Kubernetes, no service split — Buzz's
Postgres/Redis/MinIO split solves a scaling problem this project
doesn't have (see `PROJECT.md`).

## Build & run

The daemon has to be running first (Docker Desktop on Windows/macOS,
or `dockerd` on a VPS). `docker compose` talks to it; if the engine
isn't up you'll get a pipe/socket error, not a compose YAML error.

```bash
cp .env.example .env
# edit .env: set GROQ_API_KEY, optionally OPENROUTER_API_KEY, LANGFUSE_PUBLIC_KEY / LANGFUSE_SECRET_KEY

docker compose build
docker compose up -d
```

App is on `http://<host>:8000`.

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
| `SWARM_AGENT_MODEL` | no | overrides per-agent model set in the `agents` table's default |
| `SWARM_SANDBOX_DIR` | no | set by the Dockerfile, don't override unless you know why |
| `SWARM_DB_PATH` | no | set by the Dockerfile to `/app/data/swarm.db` |
| `LANGFUSE_PUBLIC_KEY` / `LANGFUSE_SECRET_KEY` | no | tracing degrades silently if unset |

## Updating

```bash
git pull
docker compose build
docker compose up -d
```

The volume persists across rebuilds. Phase 9 added `ensure_schema()` —
on startup the app `ALTER TABLE`s any missing agent harness columns and
creates `agent_memory` if needed. No separate migrate command.
