# swarm

A mini Buzz. One FastAPI relay, channels, humans and an AI agent in the
same room. SQLite instead of a Nostr event log, no signing, no git
hosting, no workflows — the one thing kept from Buzz's actual idea is
**the agent is a member of the channel, not a bot bolted on the side**.

```
swarm/
  backend/
    main.py     # FastAPI app: REST + WebSocket hub + agent trigger
    db.py       # SQLite schema + async queries (aiosqlite)
    agent.py    # Groq-backed responder, triggered on @swarm
    models.py   # pydantic request bodies
  frontend/
    index.html  # single-page Slack/Discord-style UI, vanilla JS + WS
  cli/
    swarm_cli.py  # JSON in / JSON out, for scripts and other agents
  requirements.txt
  .env.example
```

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

uvicorn backend.main:app --reload
```

On macOS/Linux the activate line is `source .venv/bin/activate`; the rest is
the same.

Open `http://localhost:8000`. Pick a handle, pick a channel, talk. Say
`@swarm <anything>` in a message and the agent reads the last 12
messages in that channel and replies.

## CLI

```bash
python cli/swarm_cli.py channels
python cli/swarm_cli.py history general --limit 20
python cli/swarm_cli.py post general uzeb "shipping the coverage fix"
echo "long message" | python cli/swarm_cli.py post general uzeb --stdin
```

Set `SWARM_URL` if the relay isn't on `localhost:8000`. This is the
part that matters if you want another agent (or a script) posting into
the workspace — it never needs to touch the WebSocket.

## What's actually missing vs. Buzz

No auth (anyone can claim any handle), no threads, no DMs, no
canvases, no git events, no workflows, no multi-agent orchestration,
no persistence beyond one SQLite file. This is the 20% that makes the
"agent in the room" idea legible in ~350 lines, not the 80% that makes
it a product.

Natural next steps, roughly in order of leverage:
1. **Tool calling for the agent** — give it a read-only shell or a
   search tool via Groq's function calling, so `@swarm` can actually
   *do* something instead of just talk.
2. **Threads** — reply-to on messages, rendered as a side panel.
3. **Multiple agent personas** — swap `agent.py`'s system prompt per
   channel, like Buzz's persona packs.
4. **Auth** — even a dumb shared-secret header beats none.
