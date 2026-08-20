# swarm

A mini Buzz that grew a Grok Bot layer. One FastAPI relay, humans and
named LLM teammates in the same workspace. Each Bot has a job, a 1:1,
memory, skills, optional routines, and a shared sandbox they all treat
as their computer. SQLite instead of a Nostr event log, no signing, no
cloud VM — the thing kept from both Buzz and Grok Bot is **the agent is
a teammate, not a chatbot sidebar**.

```
swarm/
  backend/          FastAPI relay: REST + WebSocket + auth + agents
  frontend/         React 19 + Vite 8 (bun). Production build in frontend/dist
  cli/swarm_cli.py  JSON in / JSON out, for scripts and other agents
  tests/            pytest suite (Groq is mocked)
  docs/DEPLOY.md    docker compose
```

**Status: V2 + Phase 10.** Auth, rooms, 1:1 Bot DMs, job templates,
skills, routines, approvals, shared workspace, threads, reactions,
tool calling, per-agent memory, Langfuse, Docker, streaming replies.
See `PROJECT.md` and `SPEC.md` for the contract.

## Run it

Uses [uv](https://docs.astral.sh/uv/) for the venv and [bun](https://bun.sh) for the UI.

```powershell
cd swarm

uv venv .venv
.\.venv\Scripts\Activate.ps1
uv pip install -r requirements.txt

cd frontend
bun install
bun run build
cd ..

cp .env.example .env
# edit .env, set GROQ_API_KEY (free tier at console.groq.com)
# backend/.env also works — the app loads both on startup

uvicorn backend.main:app --reload
```

On macOS/Linux the activate line is `source .venv/bin/activate`; the rest is
the same.

Open `http://localhost:8000`. Pick a handle, then talk to `swarm` in
their 1:1 — no `@mention` needed there. In a room, `@swarm <task>`
still works. Create more Bots from job templates (Sales Outbound,
Product Performance, Chief of Staff, …). `/skill` invokes a saved
process; the computer panel shows the shared workspace, routines, and
approvals.

Docker: see `docs/DEPLOY.md`. For a live UI while uvicorn is running,
`cd frontend && bun run dev` — Vite is on `:5173` and proxies `/api` and `/ws`.

## CLI

```bash
python cli/swarm_cli.py register uzeb
export SWARM_TOKEN='uzeb:...'   # printed on stderr after register

python cli/swarm_cli.py channels
python cli/swarm_cli.py history dm-swarm --limit 20
python cli/swarm_cli.py post dm-swarm uzeb "summarize this week"
python cli/swarm_cli.py post general uzeb "hey @swarm shipping the fix"
python cli/swarm_cli.py react 1 uzeb "🔥"
python cli/swarm_cli.py agents
python cli/swarm_cli.py agent swarm
python cli/swarm_cli.py create-agent piper --job "Product Performance" --prompt "Investigate latency. Never change production."
python cli/swarm_cli.py patch-agent piper --window 20
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

## What's actually missing vs. Grok Bot / Buzz

No cloud VM, no browser computer-use, no teach-by-demonstration, no
Salesforce/Slack connectors, no iOS app. No canvases, git events, or
multi-tenant hosting. 1:1s exist for Bots; there are no human DMs.
No admin role on agent creation, no semantic search. This is the slice
that makes "named teammates with a job" legible.

Still gated (see `PROMPTS.md`):
1. **Admin role** for `POST /api/agents` — only if more than one person
   should be allowed to register personas.
2. **Semantic history** — only if keyword search has actually proven
   insufficient.
3. **Agent delete** — only if dangling history is actually a problem.
4. **Real computer-use** — only if the sandbox workspace is actually
   insufficient.
