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

docker compose build
docker compose up -d
```

The image has two stages. Bun builds the React UI, and the Python image
serves `frontend/dist` through uvicorn. Bun is not needed on the VPS.

App is on `http://<host>:8000`.

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
| `SWARM_SYSTEM` | no | `1` by default; set `0` to disable host-system tools |
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

## Updating

```bash
git pull
docker compose build
docker compose up -d
```

The volume persists across rebuilds. On startup, `ensure_schema()` adds
missing agent harness columns and creates `agent_memory` when needed. There
is no separate migration command.
