"""
swarm — a mini Buzz. One relay, channels, humans and N agents in the
same room, everything's an event in SQLite. Run:
`uvicorn backend.main:app --reload` from the project root.

Auth model (Phase 1): register a handle, get back a composite token
"<handle>:<raw>". Send it as `Authorization: Bearer <handle>:<raw>` on
every write. WS clients send `{"token": "<handle>:<raw>"}` as the
first frame after connecting, before any message frames.
"""
from __future__ import annotations

import asyncio
import time
from pathlib import Path

from fastapi import Depends, FastAPI, Header, HTTPException, Response, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from . import agent, db
from .models import AgentCreate, ChannelCreate, MessageCreate, ReactionCreate, RegisterRequest

app = FastAPI(title="swarm")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

FRONTEND_DIR = Path(__file__).parent.parent / "frontend"

RATE_LIMIT_SECONDS = 0.5
_last_write: dict[str, float] = {}


def _rate_limited(handle: str) -> bool:
    now = time.time()
    last = _last_write.get(handle, 0.0)
    if now - last < RATE_LIMIT_SECONDS:
        return True
    _last_write[handle] = now
    return False


def parse_token(raw: str) -> tuple[str, str] | None:
    if ":" not in raw:
        return None
    handle, _, token = raw.partition(":")
    if not handle or not token:
        return None
    return handle, token


async def require_auth(authorization: str | None = Header(default=None)) -> str:
    """Returns the authenticated handle or raises 401."""
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(401, "missing bearer token")
    parsed = parse_token(authorization.removeprefix("Bearer ").strip())
    if parsed is None:
        raise HTTPException(401, "malformed token")
    handle, token = parsed
    if not await db.verify_token(handle, token):
        raise HTTPException(401, "invalid token")
    return handle


class Hub:
    """In-memory WebSocket fanout, keyed by channel. No history here —
    that's the DB's job."""

    def __init__(self) -> None:
        self._rooms: dict[str, set[WebSocket]] = {}

    async def join(self, channel_id: str, ws: WebSocket) -> None:
        await ws.accept()
        self._rooms.setdefault(channel_id, set()).add(ws)

    def leave(self, channel_id: str, ws: WebSocket) -> None:
        self._rooms.get(channel_id, set()).discard(ws)

    async def broadcast(self, channel_id: str, payload: dict) -> None:
        dead = []
        for ws in self._rooms.get(channel_id, set()):
            try:
                await ws.send_json(payload)
            except Exception:  # noqa: BLE001
                dead.append(ws)
        for ws in dead:
            self.leave(channel_id, ws)


hub = Hub()


@app.on_event("startup")
async def on_startup() -> None:
    await db.init_db()


# ---------------------------------------------------------------- auth ----

@app.post("/api/register")
async def api_register(payload: RegisterRequest):
    if await db.user_exists(payload.handle):
        raise HTTPException(409, "handle already registered")
    raw = db.generate_token()
    await db.create_user(payload.handle, raw)
    return {"handle": payload.handle, "token": f"{payload.handle}:{raw}"}


# ---------------------------------------------------------------- REST ----

@app.get("/api/channels")
async def api_list_channels():
    return await db.list_channels()


@app.post("/api/channels")
async def api_create_channel(payload: ChannelCreate, handle: str = Depends(require_auth)):
    if _rate_limited(handle):
        raise HTTPException(429, "slow down")
    channel_id = payload.name.strip().lower().replace(" ", "-")
    if await db.channel_exists(channel_id):
        raise HTTPException(409, "channel already exists")
    return await db.create_channel(channel_id, payload.name, payload.topic)


@app.get("/api/channels/{channel_id}/messages")
async def api_get_history(channel_id: str, limit: int = 50):
    if not await db.channel_exists(channel_id):
        raise HTTPException(404, "no such channel")
    messages = await db.get_history(channel_id, limit)
    for m in messages:
        m["reactions"] = await db.get_reactions(m["id"])
    return messages


@app.post("/api/channels/{channel_id}/messages")
async def api_post_message(
    channel_id: str, payload: MessageCreate, handle: str = Depends(require_auth)
):
    if payload.author != handle:
        raise HTTPException(403, "author must match the authenticated handle")
    if _rate_limited(handle):
        raise HTTPException(429, "slow down")
    if not await db.channel_exists(channel_id):
        raise HTTPException(404, "no such channel")
    if payload.parent_id is not None and not await db.message_exists(payload.parent_id):
        raise HTTPException(404, "parent message does not exist")

    msg = await db.add_message(
        channel_id, payload.author, payload.body, payload.author_kind, payload.parent_id
    )
    await hub.broadcast(channel_id, {"type": "message", "message": msg})
    await _maybe_trigger_agents(channel_id, msg)
    return msg


@app.post("/api/messages/{message_id}/reactions", status_code=204)
async def api_add_reaction(
    message_id: int, payload: ReactionCreate, handle: str = Depends(require_auth)
):
    if payload.author != handle:
        raise HTTPException(403, "author must match the authenticated handle")
    if not await db.message_exists(message_id):
        raise HTTPException(404, "no such message")
    channel_id = await _channel_of_message(message_id)
    await db.add_reaction(message_id, payload.author, payload.emoji)
    await hub.broadcast(channel_id, {
        "type": "reaction", "message_id": message_id,
        "author": payload.author, "emoji": payload.emoji,
    })
    return Response(status_code=204)


async def _channel_of_message(message_id: int) -> str:
    import aiosqlite
    async with aiosqlite.connect(db.DB_PATH) as conn:
        cur = await conn.execute("SELECT channel_id FROM messages WHERE id = ?", (message_id,))
        row = await cur.fetchone()
        return row[0]


# --------------------------------------------------------------- agents ---

@app.get("/api/agents")
async def api_list_agents(channel_id: str | None = None):
    return await db.list_agents(channel_id)


@app.post("/api/agents")
async def api_create_agent(payload: AgentCreate, handle: str = Depends(require_auth)):
    # NOTE: no admin role check yet — any authenticated user can register
    # a persona. Named gap, see SPEC.md Phase 4. Fine for a single-tenant
    # portfolio deployment, not fine past that.
    if await db.agent_exists(payload.name):
        raise HTTPException(409, "agent name already registered")
    return await db.create_agent(payload.name, payload.system_prompt, payload.model, payload.channel_scope)


# ---------------------------------------------------------------- WS ------

@app.websocket("/ws/{channel_id}")
async def ws_channel(websocket: WebSocket, channel_id: str):
    if not await db.channel_exists(channel_id):
        await websocket.close(code=4004)
        return

    await websocket.accept()

    # auth handshake — first frame must be {"token": "handle:raw"}
    try:
        first = await websocket.receive_json()
    except Exception:  # noqa: BLE001
        await websocket.close(code=4001)
        return

    parsed = parse_token(first.get("token", "")) if isinstance(first, dict) else None
    if parsed is None:
        await websocket.close(code=4001)
        return
    handle, token = parsed
    if not await db.verify_token(handle, token):
        await websocket.close(code=4001)
        return

    hub._rooms.setdefault(channel_id, set()).add(websocket)
    try:
        while True:
            data = await websocket.receive_json()
            body = (data.get("body") or "").strip()
            if not body:
                continue
            if _rate_limited(handle):
                await websocket.send_json({"type": "error", "detail": "slow down"})
                continue
            parent_id = data.get("parent_id")
            msg = await db.add_message(channel_id, handle, body, "human", parent_id)
            await hub.broadcast(channel_id, {"type": "message", "message": msg})
            await _maybe_trigger_agents(channel_id, msg)
    except WebSocketDisconnect:
        hub.leave(channel_id, websocket)


async def _maybe_trigger_agents(channel_id: str, msg: dict) -> None:
    if msg["author_kind"] != "human":
        return
    scoped_agents = await db.list_agents(channel_id)
    mentioned = agent.find_mentioned_agents(msg["body"], scoped_agents)
    if mentioned:
        # one task for the whole batch, agents run in order inside it —
        # asyncio.create_task per-agent would race and violate FR4.2's
        # "in the order mentioned" guarantee.
        asyncio.create_task(_run_agents_in_order(channel_id, mentioned))


async def _run_agents_in_order(channel_id: str, agents: list[dict]) -> None:
    for a in agents:
        await _run_agent(channel_id, a)


async def _run_agent(channel_id: str, agent_row: dict) -> None:
    await hub.broadcast(channel_id, {"type": "typing", "author": agent_row["name"]})
    history = await db.get_history(channel_id, limit=agent.HISTORY_WINDOW)
    result = await agent.generate_reply(agent_row, channel_id, history)

    for event in result["tool_events"]:
        note = f"{agent_row['name']} ran: {event['tool']}({event['args']}) -> {event['result'][:200]}"
        sys_msg = await db.add_message(channel_id, agent_row["name"], note, "system")
        await hub.broadcast(channel_id, {"type": "message", "message": sys_msg})

    msg = await db.add_message(channel_id, agent_row["name"], result["reply"], "agent")
    await hub.broadcast(channel_id, {"type": "message", "message": msg})


# ------------------------------------------------------------ frontend ----

app.mount("/static", StaticFiles(directory=FRONTEND_DIR), name="static")


@app.get("/")
async def index():
    return FileResponse(FRONTEND_DIR / "index.html")
