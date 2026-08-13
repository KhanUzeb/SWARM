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
# edit .env: set GROQ_API_KEY, optionally LANGFUSE_PUBLIC_KEY / LANGFUSE_SECRET_KEY

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
| `GROQ_API_KEY` | yes, for agents to respond | Groq inference |
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

The volume persists across rebuilds — no migration step exists yet
because the schema hasn't needed one. If a future phase changes the
schema, add a migration step here before it's needed, not after
something breaks in prod.
