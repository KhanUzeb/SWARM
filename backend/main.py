"""
swarm — a mini Buzz. One relay, channels, humans and N agents in the
same room, everything's an event in SQLite. Run:
`uvicorn backend.main:app --reload` from the project root.

Auth model (Phase 1): register a handle, get back a composite token
"<handle>:<raw>". Send it as `Authorization: Bearer <handle>:<raw>` on
every write. WS clients send `{"token": "<handle>:<raw>", "last_seen_id": N}`
as the first frame after connecting, before any message frames.
"""
from __future__ import annotations

import asyncio
import os
import time
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, Header, HTTPException, Response, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from . import agent, db
from .models import AgentCreate, AgentPatch, ChannelCreate, MessageCreate, ReactionCreate, RegisterRequest

app = FastAPI(title="swarm")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

FRONTEND_DIR = Path(__file__).parent.parent / "frontend"

RATE_LIMIT_SECONDS = 0.5
HISTORY_LIMIT_MAX = 100
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


async def _with_reactions(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    for m in messages:
        m["reactions"] = await db.get_reactions(m["id"])
    return messages


class Hub:
    """In-memory WebSocket fanout, keyed by channel. No history here —
    that's the DB's job. join() registers an already-accepted socket;
    accept happens in the WS handler so the handshake frame can arrive."""

    def __init__(self) -> None:
        self._rooms: dict[str, set[WebSocket]] = {}

    def join(self, channel_id: str, ws: WebSocket) -> None:
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


# --------------------------------------------------------------- status ---

def _key_set(name: str) -> bool:
    return bool((os.environ.get(name) or "").strip().strip('"').strip("'"))


@app.get("/api/status")
async def api_status():
    """Boolean-only — never returns the keys themselves."""
    return {"groq": _key_set("GROQ_API_KEY"), "openrouter": _key_set("OPENROUTER_API_KEY")}


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
async def api_get_history(
    channel_id: str, limit: int = 50, before_id: int | None = None
):
    if not await db.channel_exists(channel_id):
        raise HTTPException(404, "no such channel")
    limit = max(1, min(limit, HISTORY_LIMIT_MAX))
    messages = await db.get_history(channel_id, limit, before_id)
    return await _with_reactions(messages)


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


@app.get("/api/messages/{message_id}/thread")
async def api_get_thread(message_id: int):
    parent = await db.get_message(message_id)
    if parent is None:
        raise HTTPException(404, "no such message")
    replies = await db.get_replies(message_id)
    await _with_reactions([parent, *replies])
    return {"parent": parent, "replies": replies}


@app.post("/api/messages/{message_id}/reactions", status_code=204)
async def api_add_reaction(
    message_id: int, payload: ReactionCreate, handle: str = Depends(require_auth)
):
    if payload.author != handle:
        raise HTTPException(403, "author must match the authenticated handle")
    if not await db.message_exists(message_id):
        raise HTTPException(404, "no such message")
    channel_id = await db.channel_of_message(message_id)
    inserted = await db.add_reaction(message_id, payload.author, payload.emoji)
    if inserted:
        await hub.broadcast(channel_id, {
            "type": "reaction", "message_id": message_id,
            "author": payload.author, "emoji": payload.emoji,
        })
    return Response(status_code=204)


# --------------------------------------------------------------- agents ---

@app.get("/api/agents")
async def api_list_agents(channel_id: str | None = None):
    return await db.list_agents(channel_id)


@app.get("/api/agents/{name}")
async def api_get_agent(name: str):
    row = await db.get_agent(name)
    if row is None:
        raise HTTPException(404, "no such agent")
    return row


@app.post("/api/agents")
async def api_create_agent(payload: AgentCreate, handle: str = Depends(require_auth)):
    # NOTE: no admin role check yet — any authenticated user can register
    # a persona. Named gap, see SPEC.md. Fine for a single-tenant
    # portfolio deployment, not fine past that.
    if _rate_limited(handle):
        raise HTTPException(429, "slow down")
    if await db.agent_exists(payload.name):
        raise HTTPException(409, "agent name already registered")
    if payload.channel_scope and not await db.channel_exists(payload.channel_scope):
        raise HTTPException(404, "no such channel")
    return await db.create_agent(
        payload.name,
        payload.system_prompt,
        payload.model,
        payload.channel_scope,
        payload.history_window,
        payload.max_tool_calls,
        payload.tools,
    )


@app.patch("/api/agents/{name}")
async def api_patch_agent(name: str, payload: AgentPatch, handle: str = Depends(require_auth)):
    if _rate_limited(handle):
        raise HTTPException(429, "slow down")
    if not await db.agent_exists(name):
        raise HTTPException(404, "no such agent")
    fields = payload.model_dump(exclude_unset=True)
    if "channel_scope" in fields and fields["channel_scope"] and not await db.channel_exists(fields["channel_scope"]):
        raise HTTPException(404, "no such channel")
    updated = await db.update_agent(name, fields)
    if updated is None:
        raise HTTPException(404, "no such agent")
    return updated


# ---------------------------------------------------------------- WS ------

@app.websocket("/ws/{channel_id}")
async def ws_channel(websocket: WebSocket, channel_id: str):
    if not await db.channel_exists(channel_id):
        await websocket.close(code=4004)
        return

    await websocket.accept()

    # auth handshake — first frame must be {"token": "handle:raw", "last_seen_id": N?}
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

    last_seen_id = first.get("last_seen_id") if isinstance(first, dict) else None
    hub.join(channel_id, websocket)

    if last_seen_id is not None:
        try:
            after = int(last_seen_id)
        except (TypeError, ValueError):
            after = None
        if after is not None:
            missed = await db.get_history_after(channel_id, after)
            missed = await _with_reactions(missed)
            for msg in missed:
                await websocket.send_json({"type": "message", "message": msg})

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
        pass
    finally:
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
    name = agent_row["name"]
    await hub.broadcast(channel_id, {"type": "typing", "author": name})
    window = agent.history_window_of(agent_row)
    history = await db.get_history(channel_id, limit=max(window * 2, window))
    tools_posted = False

    async def persist_tools(events: list[dict]) -> None:
        nonlocal tools_posted
        if tools_posted:
            return
        tools_posted = True
        for event in events:
            note = f"{name} ran: {event['tool']}({event['args']}) -> {event['result'][:200]}"
            sys_msg = await db.add_message(channel_id, name, note, "system")
            await hub.broadcast(channel_id, {"type": "message", "message": sys_msg})

    async def on_stream_start() -> None:
        await hub.broadcast(channel_id, {"type": "agent_stream_start", "author": name})

    async def on_token(delta: str) -> None:
        await hub.broadcast(channel_id, {"type": "agent_token", "author": name, "delta": delta})

    result = await agent.generate_reply(
        agent_row, channel_id, history,
        on_tools_ready=persist_tools,
        on_stream_start=on_stream_start,
        on_token=on_token,
    )
    await persist_tools(result["tool_events"])

    msg = await db.add_message(channel_id, name, result["reply"], "agent")
    await hub.broadcast(channel_id, {"type": "message", "message": msg})


# ------------------------------------------------------------ frontend ----

app.mount("/static", StaticFiles(directory=FRONTEND_DIR), name="static")


@app.get("/")
async def index():
    return FileResponse(FRONTEND_DIR / "index.html")
