# swarm

A mini Buzz. One FastAPI relay, channels, humans and LLM agents in the
same room. SQLite instead of a Nostr event log, no signing, no git
hosting, no workflows — the one thing kept from Buzz's actual idea is
**the agent is a member of the channel, not a bot bolted on the side**.

```
swarm/
  backend/          FastAPI relay: REST + WebSocket + auth + agents
  frontend/         vanilla HTML/CSS/JS (no build step)
  cli/swarm_cli.py  JSON in / JSON out, for scripts and other agents
  tests/            pytest suite (Groq is mocked)
  docs/DEPLOY.md    docker compose
```

**Status: V2 + Phase 8.** Auth, threads, reactions, multi-agent personas,
tool calling, Langfuse, Docker, WS reconnect/catch-up, streaming agent
replies, and a thread side panel are all built. See `PROJECT.md` and
`SPEC.md` for the contract.

## Run it

Uses [uv](https://docs.astral.sh/uv/) for the venv. If a conda/venv named
`ml_env` is active, deactivate it first so it doesn't shadow the project env.

```powershell
cd swarm

# skip these if ml_env isn't active
if ($env:CONDA_DEFAULT_ENV -eq "ml_env") { conda deactivate }
if ($env:VIRTUAL_ENV -match "ml_env") { deactivate }

uv venv .venv
.\.venv\Scripts\Activate.ps1
uv pip install -r requirements.txt

cp .env.example .env
# edit .env, set GROQ_API_KEY (free tier at console.groq.com)
# backend/.env also works — the app loads both on startup

uvicorn backend.main:app --reload
```

On macOS/Linux the activate line is `source .venv/bin/activate`; the rest is
the same.

Open `http://localhost:8000`. Pick a handle (or reconnect from a saved
session), pick a channel, talk. Say `@swarm <anything>` in a message and
the agent reads the last 12 messages in that channel and replies — tokens
stream into the log as they arrive.

Docker: see `docs/DEPLOY.md`.

## CLI

```bash
python cli/swarm_cli.py register uzeb
export SWARM_TOKEN='uzeb:...'   # printed on stderr after register

python cli/swarm_cli.py channels
python cli/swarm_cli.py history general --limit 20
python cli/swarm_cli.py post general uzeb "shipping the coverage fix"
echo "long message" | python cli/swarm_cli.py post general uzeb --stdin
python cli/swarm_cli.py react 1 uzeb "🔥"
python cli/swarm_cli.py agents
```

Set `SWARM_URL` if the relay isn't on `localhost:8000`. This is the
part that matters if you want another agent (or a script) posting into
the workspace — it never needs to touch the WebSocket.

## Tests

```bash
uv pip install -r requirements-dev.txt
pytest -q
```

Groq is never called — agent generation is mocked.

## What's actually missing vs. Buzz

No DMs, no canvases, no git events, no workflows, no multi-tenant
hosting, no admin role on agent creation, no semantic search. This is
the slice that makes the "agent in the room" idea legible, not the 80%
that makes it a product.

Still gated (see `PROMPTS.md`):
1. **Admin role** for `POST /api/agents` — only if more than one person
   should be allowed to register personas.
2. **Semantic history** — only if keyword search has actually proven
   insufficient.
