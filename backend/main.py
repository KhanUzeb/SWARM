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

from fastapi import Depends, FastAPI, HTTPException, Response, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from . import agent, db
from .ai_support import store as ai_store
from .ai_support.providers import get_provider, list_providers, providers_by_priority
from .jobs import JOB_TEMPLATES
from .models import (
    AgentCreate, AgentPatch, AiProviderConnect, ApprovalResolve, ChannelCreate,
    CustomToolCreate, CustomToolPatch, MessageCreate,
    ReactionCreate, RegisterRequest, RoutineCreate, RoutinePatch, SkillCreate,
    SkillPatch,
)
from .security import allowed_origins, install_api_guard, optional_auth, parse_token, require_auth
from .tools.registry import get_registry, reload_registry

app = FastAPI(title="swarm")
install_api_guard(app)

app.add_middleware(
    CORSMiddleware,
    allow_origins=list(allowed_origins()),
    allow_credentials=True,
    allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", "X-Swarm-Client"],
)

FRONTEND_DIR = Path(__file__).parent.parent / "frontend"
DIST_DIR = FRONTEND_DIR / "dist"  # Vite production output

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

    async def broadcast_all(self, payload: dict) -> None:
        for channel_id in list(self._rooms):
            await self.broadcast(channel_id, payload)


hub = Hub()
_routine_task: asyncio.Task | None = None
ROUTINE_TICK_SECONDS = 20
HANDOFF_DEPTH = 2


@app.on_event("startup")
async def on_startup() -> None:
    global _routine_task
    await db.init_db()
    await reload_registry()
    _routine_task = asyncio.create_task(_routine_loop())


@app.on_event("shutdown")
async def on_shutdown() -> None:
    if _routine_task is not None:
        _routine_task.cancel()


# --------------------------------------------------------------- status ---

def _key_set(name: str) -> bool:
    return bool((os.environ.get(name) or "").strip().strip('"').strip("'"))


@app.get("/api/status")
async def api_status(handle: str | None = Depends(optional_auth)):
    """Boolean-only — never returns raw keys. Connection details require auth."""
    demo = agent.demo_mode_enabled()
    connections = await ai_store.status()
    connected_ids = {c["provider_id"] for c in connections}
    providers_ready: dict[str, bool] = {}
    for spec in providers_by_priority():
        pid = spec["id"]
        env_name = spec.get("env_fallback")
        env_ok = _key_set(env_name) if env_name else False
        providers_ready[pid] = env_ok or pid in connected_ids
    llm_ready = demo or any(providers_ready.values())
    body: dict[str, Any] = {
        "groq": providers_ready.get("groq", False),
        "openrouter": providers_ready.get("openrouter", False),
        "demo": demo,
        "llm_ready": llm_ready,
        "providers_ready": providers_ready,
    }
    if handle:
        body["ai_providers"] = connections
    return body


# ---------------------------------------------------------------- auth ----

@app.post("/api/register")
async def api_register(payload: RegisterRequest):
    if await db.user_exists(payload.handle):
        raise HTTPException(409, "handle already registered")
    raw = db.generate_token()
    await db.create_user(payload.handle, raw)
    return {
        "handle": payload.handle,
        "token": f"{payload.handle}:{raw}",
        "created": True,
    }


# ---------------------------------------------------------------- REST ----

@app.get("/api/channels")
async def api_list_channels(handle: str = Depends(require_auth)):
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
    channel_id: str,
    limit: int = 50,
    before_id: int | None = None,
    handle: str = Depends(require_auth),
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


@app.delete("/api/channels/{channel_id}")
async def api_delete_channel(channel_id: str, handle: str = Depends(require_auth)):
    if _rate_limited(handle):
        raise HTTPException(429, "slow down")
    channel = await db.get_channel(channel_id)
    if channel is None:
        raise HTTPException(404, "no such channel")
    if channel.get("kind") == "dm":
        raise HTTPException(400, "cannot delete a 1:1")
    await db.delete_channel(channel_id)
    await hub.broadcast(channel_id, {"type": "channel_deleted", "channel_id": channel_id})
    return {"ok": True, "id": channel_id}


@app.delete("/api/messages/{message_id}")
async def api_delete_message(message_id: int, handle: str = Depends(require_auth)):
    if _rate_limited(handle):
        raise HTTPException(429, "slow down")
    msg = await db.get_message(message_id)
    if msg is None:
        raise HTTPException(404, "no such message")
    ids = await db.delete_message(message_id)
    await hub.broadcast(msg["channel_id"], {
        "type": "message_deleted",
        "message_id": message_id,
        "ids": ids,
    })
    return {"ok": True, "ids": ids}


@app.get("/api/messages/{message_id}/thread")
async def api_get_thread(message_id: int, handle: str = Depends(require_auth)):
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
async def api_list_agents(channel_id: str | None = None, handle: str = Depends(require_auth)):
    return await db.list_agents(channel_id)


@app.get("/api/agents/{name}")
async def api_get_agent(name: str, handle: str = Depends(require_auth)):
    row = await db.get_agent(name)
    if row is None:
        raise HTTPException(404, "no such agent")
    return row


async def _validate_tool_names(names: list[str] | None) -> None:
    if not names:
        return
    reg = get_registry()
    invalid = [n for n in names if n not in reg.all_names()]
    if invalid:
        raise HTTPException(400, detail=f"unknown tools: {invalid}")


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
    await _validate_tool_names(payload.tools)
    return await db.create_agent(
        payload.name,
        payload.system_prompt,
        payload.model,
        payload.channel_scope,
        payload.history_window,
        payload.max_tool_calls,
        payload.tools,
        payload.job,
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
    await _validate_tool_names(fields.get("tools"))
    updated = await db.update_agent(name, fields)
    if updated is None:
        raise HTTPException(404, "no such agent")
    return updated


@app.get("/api/jobs")
async def api_list_jobs(handle: str = Depends(require_auth)):
    return JOB_TEMPLATES


@app.get("/api/tools")
async def api_list_tools(handle: str = Depends(require_auth)):
    reg = get_registry()
    return {"tools": reg.list_catalog(), "plugins": reg.plugins()}


@app.post("/api/tools/custom")
async def api_create_custom_tool(payload: CustomToolCreate, handle: str = Depends(require_auth)):
    if _rate_limited(handle):
        raise HTTPException(429, "slow down")
    reg = get_registry()
    if payload.name in reg.all_names():
        raise HTTPException(409, "tool name already exists")
    row = await db.create_custom_tool(
        payload.name, payload.description,
        parameters=payload.parameters,
        handler_type=payload.handler_type,
        handler_config=payload.handler_config,
        enabled=payload.enabled,
    )
    await reload_registry()
    return row


@app.patch("/api/tools/custom/{tool_id}")
async def api_patch_custom_tool(
    tool_id: int, payload: CustomToolPatch, handle: str = Depends(require_auth)
):
    if _rate_limited(handle):
        raise HTTPException(429, "slow down")
    updated = await db.update_custom_tool(tool_id, payload.model_dump(exclude_unset=True))
    if updated is None:
        raise HTTPException(404, "no such custom tool")
    await reload_registry()
    return updated


@app.delete("/api/tools/custom/{tool_id}")
async def api_delete_custom_tool(tool_id: int, handle: str = Depends(require_auth)):
    if _rate_limited(handle):
        raise HTTPException(429, "slow down")
    if not await db.delete_custom_tool(tool_id):
        raise HTTPException(404, "no such custom tool")
    await reload_registry()
    return {"ok": True}


@app.post("/api/plugins/reload")
async def api_reload_plugins(handle: str = Depends(require_auth)):
    if _rate_limited(handle):
        raise HTTPException(429, "slow down")
    reg = await reload_registry()
    return {"ok": True, "plugins": reg.plugins(), "tool_count": len(reg.list_catalog())}


@app.get("/api/ai-support/providers")
async def api_ai_providers(handle: str = Depends(require_auth)):
    catalog = list_providers()
    connections = {c["provider_id"]: c for c in await ai_store.status()}
    for p in catalog:
        conn = connections.get(p["id"])
        p["connected"] = conn is not None
        if conn:
            p["model"] = conn.get("model")
            p["key_hint"] = conn.get("key_hint")
            p["connected_at"] = conn.get("connected_at")
    return catalog


@app.get("/api/ai-support/connections")
async def api_ai_connections(handle: str = Depends(require_auth)):
    return await ai_store.status()


@app.post("/api/ai-support/connect/{provider_id}")
async def api_ai_connect(
    provider_id: str, payload: AiProviderConnect, handle: str = Depends(require_auth)
):
    if _rate_limited(handle):
        raise HTTPException(429, "slow down")
    if get_provider(provider_id) is None:
        raise HTTPException(404, "unknown provider")
    return await ai_store.connect(provider_id, payload.api_key, model=payload.model)


@app.delete("/api/ai-support/connect/{provider_id}")
async def api_ai_disconnect(provider_id: str, handle: str = Depends(require_auth)):
    if _rate_limited(handle):
        raise HTTPException(429, "slow down")
    if not await ai_store.disconnect(provider_id):
        raise HTTPException(404, "not connected")
    return {"ok": True}


@app.get("/api/skills")
async def api_list_skills(handle: str = Depends(require_auth)):
    return await db.list_skills()


@app.post("/api/skills")
async def api_create_skill(payload: SkillCreate, handle: str = Depends(require_auth)):
    if _rate_limited(handle):
        raise HTTPException(429, "slow down")
    return await db.upsert_skill(payload.name, payload.body)


@app.patch("/api/skills/{skill_id}")
async def api_patch_skill(
    skill_id: int, payload: SkillPatch, handle: str = Depends(require_auth)
):
    if _rate_limited(handle):
        raise HTTPException(429, "slow down")
    fields = payload.model_dump(exclude_unset=True)
    if not fields:
        row = await db.get_skill_by_id(skill_id)
        if row is None:
            raise HTTPException(404, "no such skill")
        return row
    updated = await db.update_skill(skill_id, fields)
    if updated is None:
        raise HTTPException(404, "no such skill")
    return updated


@app.delete("/api/skills/{skill_id}")
async def api_delete_skill(skill_id: int, handle: str = Depends(require_auth)):
    if _rate_limited(handle):
        raise HTTPException(429, "slow down")
    if not await db.delete_skill(skill_id):
        raise HTTPException(404, "no such skill")
    return {"ok": True}


@app.get("/api/routines")
async def api_list_routines(agent_name: str | None = None, handle: str = Depends(require_auth)):
    return await db.list_routines(agent_name)


@app.post("/api/routines")
async def api_create_routine(payload: RoutineCreate, handle: str = Depends(require_auth)):
    if _rate_limited(handle):
        raise HTTPException(429, "slow down")
    if not await db.agent_exists(payload.agent_name):
        raise HTTPException(404, "no such agent")
    if await db.count_routines(payload.agent_name) >= 50:
        raise HTTPException(409, "this bot already has 50 routines")
    return await db.create_routine(
        payload.agent_name,
        payload.title,
        payload.instructions,
        payload.interval_minutes,
        payload.enabled,
    )


@app.patch("/api/routines/{routine_id}")
async def api_patch_routine(
    routine_id: int, payload: RoutinePatch, handle: str = Depends(require_auth)
):
    if _rate_limited(handle):
        raise HTTPException(429, "slow down")
    fields = payload.model_dump(exclude_unset=True)
    if "interval_minutes" in fields:
        current = await db.get_routine(routine_id)
        if current is None:
            raise HTTPException(404, "no such routine")
        fields["next_run_at"] = time.time() + fields["interval_minutes"] * 60
    updated = await db.update_routine(routine_id, fields)
    if updated is None:
        raise HTTPException(404, "no such routine")
    return updated


@app.delete("/api/routines/{routine_id}")
async def api_delete_routine(routine_id: int, handle: str = Depends(require_auth)):
    if _rate_limited(handle):
        raise HTTPException(429, "slow down")
    if not await db.delete_routine(routine_id):
        raise HTTPException(404, "no such routine")
    return {"ok": True}


@app.post("/api/routines/{routine_id}/run")
async def api_run_routine(routine_id: int, handle: str = Depends(require_auth)):
    if _rate_limited(handle):
        raise HTTPException(429, "slow down")
    row = await db.get_routine(routine_id)
    if row is None:
        raise HTTPException(404, "no such routine")
    asyncio.create_task(_execute_routine(row, test_run=True))
    return {"ok": True, "status": "started"}


@app.get("/api/routines/{routine_id}/runs")
async def api_routine_runs(routine_id: int, handle: str = Depends(require_auth)):
    if await db.get_routine(routine_id) is None:
        raise HTTPException(404, "no such routine")
    return await db.list_routine_runs(routine_id)


@app.get("/api/approvals")
async def api_list_approvals(
    channel_id: str | None = None,
    status: str | None = "pending",
    handle: str = Depends(require_auth),
):
    return await db.list_approvals(channel_id=channel_id, status=status)


@app.post("/api/approvals/{approval_id}/resolve")
async def api_resolve_approval(
    approval_id: int, payload: ApprovalResolve, handle: str = Depends(require_auth)
):
    if _rate_limited(handle):
        raise HTTPException(429, "slow down")
    row = await db.get_approval(approval_id)
    if row is None:
        raise HTTPException(404, "no such approval")
    if row["status"] != "pending":
        raise HTTPException(409, "already resolved")
    updated = await db.resolve_approval(approval_id, payload.status)
    await db.set_agent_status(row["agent_name"], "idle")
    await hub.broadcast_all({
        "type": "bot_status", "name": row["agent_name"], "status": "idle",
    })
    verb = "Approved" if payload.status == "approved" else "Denied"
    body = f"{verb}: {row['action']}"
    if row.get("detail"):
        body += f" — {row['detail']}"
    if payload.status == "approved":
        body += " Continue from here."
    else:
        body += " Do not proceed with that action."
    msg = await db.add_message(row["channel_id"], handle, body, "human")
    await hub.broadcast(row["channel_id"], {"type": "message", "message": msg})
    await hub.broadcast_all({"type": "approval", "approval": updated})
    await _maybe_trigger_agents(row["channel_id"], msg)
    return updated


@app.get("/api/computer")
async def api_computer(handle: str = Depends(require_auth)):
    return {
        "workspace": agent.SANDBOX_DIR,
        "shared": True,
        "files": agent.list_workspace_files(),
        "activity": await db.get_recent_system_messages(20),
        "note": (
            "All Bots share this workspace. It is a local sandbox, not a cloud VM — "
            "files and shell live here; there is no remote desktop or browser session."
        ),
    }


@app.get("/api/computer/file")
async def api_computer_file(path: str, handle: str = Depends(require_auth)):
    target = agent._safe_workspace_path(path)
    if target is None or not target.is_file():
        raise HTTPException(404, "no such file")
    if target.stat().st_size > 64_000:
        raise HTTPException(413, "file too large to preview")
    return {
        "path": path,
        "content": target.read_text(encoding="utf-8", errors="replace"),
    }


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


async def _maybe_trigger_agents(channel_id: str, msg: dict, *, depth: int = 0) -> None:
    if depth > HANDOFF_DEPTH:
        return
    kind = msg.get("author_kind")
    body = msg.get("body") or ""
    if kind == "system" and not body.startswith("[routine:"):
        return
    if kind not in ("human", "agent", "system"):
        return

    to_run: list[dict] = []
    seen: set[str] = set()

    if kind in ("human", "system"):
        channel = await db.get_channel(channel_id)
        owner = (channel or {}).get("owner_agent") if channel and channel.get("kind") == "dm" else None
        if owner:
            row = await db.fetch_agent(owner)
            if row:
                to_run.append(row)
                seen.add(row["name"])

    scoped_agents = await db.list_agents(channel_id)
    mentioned = agent.find_mentioned_agents(body, scoped_agents)
    if kind == "agent":
        mentioned = [a for a in mentioned if a["name"] != msg.get("author")]
        if not mentioned:
            return
        asyncio.create_task(_run_agents_in_order(channel_id, mentioned, depth=depth + 1))
        return

    for a in mentioned:
        if a["name"] not in seen:
            to_run.append(a)
            seen.add(a["name"])

    if to_run:
        # one task for the whole batch, agents run in order inside it —
        # asyncio.create_task per-agent would race and violate FR4.2's
        # "in the order mentioned" guarantee. DM owner is prepended so a
        # 1:1 always hears you without an @mention.
        asyncio.create_task(_run_agents_in_order(channel_id, to_run, depth=depth))


async def _run_agents_in_order(channel_id: str, agents: list[dict], *, depth: int = 0) -> None:
    for a in agents:
        await _run_agent(channel_id, a, depth=depth)


async def _set_status(name: str, status: str) -> None:
    await db.set_agent_status(name, status)
    await hub.broadcast_all({"type": "bot_status", "name": name, "status": status})


async def _run_agent(channel_id: str, agent_row: dict, *, depth: int = 0) -> dict:
    name = agent_row["name"]
    await _set_status(name, "working")
    await hub.broadcast(channel_id, {"type": "typing", "author": name})
    window = agent.history_window_of(agent_row)
    history = await db.get_history(channel_id, limit=max(window * 2, window))
    tools_posted = False
    pending_approvals: list[dict] = []

    async def persist_tools(events: list[dict]) -> None:
        nonlocal tools_posted
        if tools_posted:
            return
        tools_posted = True
        for event in events:
            note = f"{name} ran: {event['tool']}({event['args']}) -> {event['result'][:200]}"
            sys_msg = await db.add_message(channel_id, name, note, "system")
            await hub.broadcast(channel_id, {"type": "message", "message": sys_msg})
            if event["tool"] == "request_approval":
                pending = await db.list_approvals(channel_id=channel_id, status="pending")
                for row in pending:
                    if row["agent_name"] == name and row not in pending_approvals:
                        pending_approvals.append(row)
                        await hub.broadcast_all({"type": "approval", "approval": row})

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
    asked = any(e["tool"] == "request_approval" for e in result["tool_events"])
    await _set_status(name, "needs_approval" if asked else "idle")
    if depth < HANDOFF_DEPTH:
        await _maybe_trigger_agents(channel_id, msg, depth=depth + 1)
    return result


async def _execute_routine(row: dict, *, test_run: bool = False) -> None:
    agent_row = await db.fetch_agent(row["agent_name"])
    if agent_row is None:
        await db.mark_routine_run(
            row["id"], status="failed", excerpt="bot missing",
            interval_minutes=row["interval_minutes"],
        )
        return
    channel_id = db.dm_channel_id(row["agent_name"])
    if not await db.channel_exists(channel_id):
        await db.mark_routine_run(
            row["id"], status="failed", excerpt="no 1:1 channel",
            interval_minutes=row["interval_minutes"],
        )
        return
    prefix = "[routine-test:" if test_run else "[routine:"
    body = (
        f"{prefix}{row['title']}] {row['instructions']}\n"
        "Do this job now. Stop for approval before any external send/publish/delete."
    )
    msg = await db.add_message(channel_id, "routine", body, "system")
    await hub.broadcast(channel_id, {"type": "message", "message": msg})
    try:
        result = await _run_agent(channel_id, agent_row)
        excerpt = (result.get("reply") or "")[:240]
        await db.mark_routine_run(
            row["id"], status="ok", excerpt=excerpt,
            interval_minutes=row["interval_minutes"],
        )
    except Exception as exc:  # noqa: BLE001
        await db.mark_routine_run(
            row["id"], status="failed", excerpt=str(exc)[:240],
            interval_minutes=row["interval_minutes"],
        )


async def run_due_routines() -> int:
    due = await db.due_routines()
    for row in due:
        await _execute_routine(row)
    return len(due)


async def _routine_loop() -> None:
    while True:
        try:
            await run_due_routines()
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 — scheduler must not die
            pass
        await asyncio.sleep(ROUTINE_TICK_SECONDS)


# ------------------------------------------------------------ frontend ----

_assets = DIST_DIR / "assets"
if _assets.is_dir():
    app.mount("/assets", StaticFiles(directory=_assets), name="assets")


@app.get("/")
async def index():
    built = DIST_DIR / "index.html"
    if not built.is_file():
        raise HTTPException(
            status_code=503,
            detail="frontend not built — run `bun run build` in frontend/",
        )
    return FileResponse(built)
