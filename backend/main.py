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
import csv
import io
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Query, Response, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from . import agent, db
from .ai_support import store as ai_store
from .ai_support.providers import get_provider, list_providers, providers_by_priority
from .jobs import JOB_TEMPLATES
from .models import (
    AgentCreate, AgentPatch, AiProviderConnect, ApprovalResolve, ChannelCreate,
    CustomToolCreate, CustomToolPatch, DirectMessageCreate, MessageCreate,
    ReactionCreate, RegisterRequest, RoutineCreate, RoutinePatch, SkillCreate,
    SkillPatch, TeamCreate, TeamPatch,
)
from .security import (
    allowed_origins, install_api_guard, optional_auth, parse_token, require_admin,
    require_auth,
)
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
        self._presence: dict[str, int] = {}

    def join(self, channel_id: str, ws: WebSocket) -> None:
        self._rooms.setdefault(channel_id, set()).add(ws)

    def leave(self, channel_id: str, ws: WebSocket) -> None:
        self._rooms.get(channel_id, set()).discard(ws)

    def mark_online(self, handle: str) -> None:
        self._presence[handle] = self._presence.get(handle, 0) + 1

    def mark_offline(self, handle: str) -> None:
        remaining = self._presence.get(handle, 0) - 1
        if remaining <= 0:
            self._presence.pop(handle, None)
        else:
            self._presence[handle] = remaining

    def online_handles(self) -> set[str]:
        return set(self._presence)

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
        user = await db.get_user(handle)
        if user:
            body["me"] = {"handle": user["handle"], "role": user["role"]}
    return body


# ---------------------------------------------------------------- auth ----

@app.post("/api/register")
async def api_register(payload: RegisterRequest):
    if await db.user_exists(payload.handle):
        raise HTTPException(409, "handle already registered")
    raw = db.generate_token()
    created = await db.create_user(payload.handle, raw)
    return {
        "handle": payload.handle,
        "token": f"{payload.handle}:{raw}",
        "created": True,
        "role": created["role"],
    }


# ---------------------------------------------------------------- REST ----

async def _require_channel(channel_id: str, handle: str) -> dict[str, Any]:
    channel = await db.get_channel(channel_id)
    if channel is None:
        raise HTTPException(404, "no such channel")
    if not db.can_view_channel(channel, handle):
        raise HTTPException(403, "private conversation")
    return channel


@app.get("/api/me")
async def api_me(handle: str = Depends(require_auth)):
    user = await db.get_user(handle)
    if user is None:
        raise HTTPException(401, "missing or invalid bearer token")
    return user


@app.get("/api/people")
async def api_list_people(handle: str = Depends(require_auth)):
    online = hub.online_handles()
    people = await db.list_people()
    return [{**p, "online": p["handle"] in online} for p in people]


@app.get("/api/channels")
async def api_list_channels(handle: str = Depends(require_auth)):
    return await db.list_channels(viewer=handle)


@app.post("/api/channels")
async def api_create_channel(payload: ChannelCreate, handle: str = Depends(require_auth)):
    if _rate_limited(handle):
        raise HTTPException(429, "slow down")
    channel_id = payload.name.strip().lower().replace(" ", "-")
    if await db.channel_exists(channel_id):
        raise HTTPException(409, "channel already exists")
    kind = payload.kind if payload.kind in ("room", "group") else "room"
    members = payload.members if kind == "group" else []
    if kind == "group":
        if not members:
            raise HTTPException(400, "group needs at least one bot")
        missing = [n for n in members if not await db.agent_exists(n)]
        if missing:
            raise HTTPException(404, f"no such agent: {missing[0]}")
    return await db.create_channel(
        channel_id, payload.name, payload.topic, kind=kind, members=members,
    )


@app.get("/api/channels/{channel_id}/messages")
async def api_get_history(
    channel_id: str,
    limit: int = 50,
    before_id: int | None = None,
    handle: str = Depends(require_auth),
):
    await _require_channel(channel_id, handle)
    limit = max(1, min(limit, HISTORY_LIMIT_MAX))
    messages = await db.get_history(channel_id, limit, before_id)
    return await _with_reactions(messages)


@app.get("/api/channels/{channel_id}/export")
async def api_export_channel(
    channel_id: str,
    fmt: str = Query("json", alias="format", pattern=r"^(json|csv)$"),
    handle: str = Depends(require_auth),
):
    channel = await _require_channel(channel_id, handle)
    rows = await db.export_messages(channel_id)
    if fmt == "csv":
        buf = io.StringIO()
        writer = csv.writer(buf)
        writer.writerow(["id", "created_at", "author", "author_kind", "parent_id", "body"])
        for row in rows:
            created = datetime.fromtimestamp(float(row["created_at"]), tz=timezone.utc).isoformat()
            writer.writerow([
                row["id"], created, row["author"], row["author_kind"],
                row.get("parent_id") or "", row["body"],
            ])
        filename = f"{channel_id}-audit.csv"
        return Response(
            content=buf.getvalue(),
            media_type="text/csv; charset=utf-8",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )
    return {
        "channel": {
            "id": channel["id"],
            "name": channel["name"],
            "kind": channel.get("kind") or "room",
            "topic": channel.get("topic") or "",
        },
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "messages": rows,
    }


@app.post("/api/channels/{channel_id}/messages")
async def api_post_message(
    channel_id: str, payload: MessageCreate, handle: str = Depends(require_auth)
):
    if payload.author != handle:
        raise HTTPException(403, "author must match the authenticated handle")
    if _rate_limited(handle):
        raise HTTPException(429, "slow down")
    await _require_channel(channel_id, handle)
    if payload.parent_id is not None and not await db.message_exists(payload.parent_id):
        raise HTTPException(404, "parent message does not exist")

    msg = await db.add_message(
        channel_id, payload.author, payload.body, payload.author_kind, payload.parent_id
    )
    await hub.broadcast(channel_id, {"type": "message", "message": msg})
    await _maybe_trigger_agents(channel_id, msg)
    return msg


@app.post("/api/dms")
async def api_open_dm(payload: DirectMessageCreate, handle: str = Depends(require_auth)):
    if _rate_limited(handle):
        raise HTTPException(429, "slow down")
    other = payload.handle.strip()
    if other.lower() == handle.lower():
        raise HTTPException(400, "cannot DM yourself")
    if not await db.user_exists(other):
        raise HTTPException(404, "no such person")
    try:
        return await db.ensure_people_dm(handle, other)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@app.get("/api/search")
async def api_search(
    q: str = "",
    limit: int = 30,
    handle: str = Depends(require_auth),
):
    needle = (q or "").strip()
    if len(needle) < 2:
        raise HTTPException(400, "query must be at least 2 characters")
    return await db.search_workspace(needle, viewer=handle, limit=limit)


@app.delete("/api/channels/{channel_id}")
async def api_delete_channel(channel_id: str, handle: str = Depends(require_auth)):
    if _rate_limited(handle):
        raise HTTPException(429, "slow down")
    channel = await _require_channel(channel_id, handle)
    if channel.get("kind") == "dm":
        raise HTTPException(400, "cannot delete a bot 1:1")
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
    await _require_channel(msg["channel_id"], handle)
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
    await _require_channel(parent["channel_id"], handle)
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
    if channel_id:
        await _require_channel(channel_id, handle)
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
    if row is None or row.get("archived"):
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
async def api_create_agent(payload: AgentCreate, handle: str = Depends(require_admin)):
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
        payload.display_name,
    )


@app.patch("/api/agents/{name}")
async def api_patch_agent(name: str, payload: AgentPatch, handle: str = Depends(require_admin)):
    if _rate_limited(handle):
        raise HTTPException(429, "slow down")
    row = await db.fetch_agent(name)
    if row is None or row.get("archived"):
        raise HTTPException(404, "no such agent")
    fields = payload.model_dump(exclude_unset=True)
    if "channel_scope" in fields and fields["channel_scope"] and not await db.channel_exists(fields["channel_scope"]):
        raise HTTPException(404, "no such channel")
    await _validate_tool_names(fields.get("tools"))
    updated = await db.update_agent(name, fields)
    if updated is None:
        raise HTTPException(404, "no such agent")
    return updated


@app.delete("/api/agents/{name}")
async def api_archive_agent(name: str, handle: str = Depends(require_admin)):
    if _rate_limited(handle):
        raise HTTPException(429, "slow down")
    row = await db.fetch_agent(name)
    if row is None or row.get("archived"):
        raise HTTPException(404, "no such agent")
    archived = await db.archive_agent(name)
    await hub.broadcast_all({"type": "bot_archived", "name": name})
    return archived


@app.get("/api/teams")
async def api_list_teams(handle: str = Depends(require_auth)):
    return await db.list_teams()


@app.post("/api/teams")
async def api_create_team(payload: TeamCreate, handle: str = Depends(require_admin)):
    if _rate_limited(handle):
        raise HTTPException(429, "slow down")
    if await db.get_team(payload.id):
        raise HTTPException(409, "team already exists")
    if await db.agent_exists(payload.id):
        raise HTTPException(409, "team id collides with a bot handle")
    missing = [n for n in payload.members if not await db.agent_exists(n)]
    if missing:
        raise HTTPException(404, f"no such agent: {missing[0]}")
    return await db.create_team(payload.id, payload.name, payload.members, payload.description)


@app.patch("/api/teams/{team_id}")
async def api_patch_team(
    team_id: str, payload: TeamPatch, handle: str = Depends(require_admin),
):
    if _rate_limited(handle):
        raise HTTPException(429, "slow down")
    if await db.get_team(team_id) is None:
        raise HTTPException(404, "no such team")
    fields = payload.model_dump(exclude_unset=True)
    if "members" in fields and fields["members"] is not None:
        if not fields["members"]:
            raise HTTPException(400, "team needs at least one bot")
        missing = [n for n in fields["members"] if not await db.agent_exists(n)]
        if missing:
            raise HTTPException(404, f"no such agent: {missing[0]}")
    updated = await db.update_team(team_id, fields)
    if updated is None:
        raise HTTPException(404, "no such team")
    return updated


@app.delete("/api/teams/{team_id}")
async def api_delete_team(team_id: str, handle: str = Depends(require_admin)):
    if _rate_limited(handle):
        raise HTTPException(429, "slow down")
    if not await db.delete_team(team_id):
        raise HTTPException(404, "no such team")
    return {"ok": True, "id": team_id}


@app.get("/api/jobs")
async def api_list_jobs(handle: str = Depends(require_auth)):
    return JOB_TEMPLATES


@app.get("/api/tools")
async def api_list_tools(handle: str = Depends(require_auth)):
    reg = get_registry()
    return {"tools": reg.list_catalog(), "plugins": reg.plugins()}


@app.post("/api/tools/custom")
async def api_create_custom_tool(payload: CustomToolCreate, handle: str = Depends(require_admin)):
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
    tool_id: int, payload: CustomToolPatch, handle: str = Depends(require_admin),
):
    if _rate_limited(handle):
        raise HTTPException(429, "slow down")
    updated = await db.update_custom_tool(tool_id, payload.model_dump(exclude_unset=True))
    if updated is None:
        raise HTTPException(404, "no such custom tool")
    await reload_registry()
    return updated


@app.delete("/api/tools/custom/{tool_id}")
async def api_delete_custom_tool(tool_id: int, handle: str = Depends(require_admin)):
    if _rate_limited(handle):
        raise HTTPException(429, "slow down")
    if not await db.delete_custom_tool(tool_id):
        raise HTTPException(404, "no such custom tool")
    await reload_registry()
    return {"ok": True}


@app.post("/api/plugins/reload")
async def api_reload_plugins(handle: str = Depends(require_admin)):
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
    provider_id: str, payload: AiProviderConnect, handle: str = Depends(require_admin)
):
    if _rate_limited(handle):
        raise HTTPException(429, "slow down")
    if get_provider(provider_id) is None:
        raise HTTPException(404, "unknown provider")
    return await ai_store.connect(provider_id, payload.api_key, model=payload.model)


@app.delete("/api/ai-support/connect/{provider_id}")
async def api_ai_disconnect(provider_id: str, handle: str = Depends(require_admin)):
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
    channel = await db.get_channel(channel_id)
    if channel is None:
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
    if not db.can_view_channel(channel, handle):
        await websocket.close(code=4003)
        return

    last_seen_id = first.get("last_seen_id") if isinstance(first, dict) else None
    hub.join(channel_id, websocket)
    hub.mark_online(handle)

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
        hub.mark_offline(handle)


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
    channel = await db.get_channel(channel_id)
    ch_kind = (channel or {}).get("kind") or "room"

    if kind in ("human", "system"):
        if ch_kind == "dm":
            owner = (channel or {}).get("owner_agent")
            if owner:
                row = await db.fetch_agent(owner)
                if row and not row.get("archived"):
                    to_run.append(row)
                    seen.add(row["name"])

    scoped_agents = await db.list_agents(channel_id)
    mentioned = agent.find_mentioned_agents(body, scoped_agents)
    teams = await db.list_teams()
    mentioned_teams = agent.find_mentioned_teams(body, teams)
    if kind == "agent":
        mentioned = [a for a in mentioned if a["name"] != msg.get("author")]
        team_bots: list[dict] = []
        for team in mentioned_teams:
            for name in team.get("members") or []:
                if name == msg.get("author"):
                    continue
                row = await db.fetch_agent(name)
                if row and not row.get("archived"):
                    team_bots.append(row)
        mentioned = mentioned + [a for a in team_bots if a["name"] not in {x["name"] for x in mentioned}]
        if not mentioned:
            return
        asyncio.create_task(_run_agents_in_order(channel_id, mentioned, depth=depth + 1))
        return

    if kind in ("human", "system") and ch_kind == "group" and not mentioned and not mentioned_teams:
        for name in (channel or {}).get("members") or []:
            if name in seen:
                continue
            row = await db.fetch_agent(name)
            if row and not row.get("archived"):
                to_run.append(row)
                seen.add(row["name"])

    ranked: list[tuple[int, int, dict]] = []
    lowered = body.lower()
    for a in mentioned:
        pos = lowered.find(f"@{a['name'].lower()}")
        ranked.append((pos if pos >= 0 else 10**9, 0, a))
    for team in mentioned_teams:
        pos = lowered.find(f"@{team['id'].lower()}")
        if pos < 0:
            pos = 10**9
        for i, name in enumerate(team.get("members") or []):
            if name in seen:
                continue
            row = await db.fetch_agent(name)
            if row and not row.get("archived"):
                ranked.append((pos, i, row))
    ranked.sort(key=lambda item: (item[0], item[1]))
    for _, _, a in ranked:
        if a["name"] not in seen:
            to_run.append(a)
            seen.add(a["name"])

    if to_run:
        # one task for the whole batch, agents run in order inside it —
        # asyncio.create_task per-agent would race and violate FR4.2's
        # "in the order mentioned" guarantee. DM owner is prepended so a
        # 1:1 always hears you without an @mention. Group members hear
        # the same way unless the message @mentions specific bots.
        # @team-id expands to that team's bots in roster order.
        asyncio.create_task(_run_agents_in_order(channel_id, to_run, depth=depth))


async def _run_agents_in_order(channel_id: str, agents: list[dict], *, depth: int = 0) -> None:
    for a in agents:
        await _run_agent(channel_id, a, depth=depth)


async def _set_status(name: str, status: str) -> None:
    await db.set_agent_status(name, status)
    await hub.broadcast_all({"type": "bot_status", "name": name, "status": status})


async def _run_agent(channel_id: str, agent_row: dict, *, depth: int = 0) -> dict:
    if agent_row.get("archived"):
        return {"reply": "", "tool_events": []}
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
    if agent_row is None or agent_row.get("archived"):
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
