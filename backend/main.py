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
import logging
import os
import time
from contextlib import asynccontextmanager, suppress
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

from . import agent, context, db, knowledge, v2, work
from .ai_support import store as ai_store
from .ai_support.agent_templates import get_agent_templates
from .ai_support.catalog import list_all_models, list_provider_models
from .ai_support.providers import get_provider, list_providers, providers_by_priority
from .jobs import JOB_TEMPLATES
from .profiles import list_profiles, load_job_profile
from .models import (
    AgentCreate, AgentPatch, AiModelsPreview, AiProviderConnect, AiProviderModel,
    ApprovalResolve, ChannelCreate, ComposioToolkitConnect, CustomToolCreate,
    CustomToolPatch, DirectMessageCreate, MessageCreate, ReactionCreate,
    RegisterRequest, RoutineCreate, RoutinePatch, SkillCreate, SkillPatch,
    SystemRootSet, TeamCreate, TeamPatch, RunCreate, WorkflowCreate, WorkflowPatch,
    ComputerRunRequest,
)
from .security import (
    allowed_origins, install_api_guard, is_loopback, optional_auth, parse_token,
    require_admin, require_auth,
)
from .tools.registry import get_registry, reload_registry

app = FastAPI(title="swarm")
install_api_guard(app)


@app.exception_handler(StarletteHTTPException)
async def _http_exception_json(request: Request, exc: StarletteHTTPException) -> JSONResponse:
    # Keep the default {"detail": ...} shape (tests + UI read it) and add
    # ok:false so clients can branch without sniffing status codes.
    return JSONResponse(status_code=exc.status_code, content={"ok": False, "detail": exc.detail})


@app.exception_handler(Exception)
async def _unhandled_exception_json(request: Request, exc: Exception) -> JSONResponse:
    _logger = logging.getLogger("swarm.backend")
    _logger.error("unhandled error on %s %s: %s", request.method, request.url.path, exc)
    return JSONResponse(status_code=500, content={"ok": False, "message": "internal error"})

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
_background_tasks: set[asyncio.Task] = set()
_active_routines: set[int] = set()
_logger = logging.getLogger("swarm.backend")


def _rate_limited(handle: str) -> bool:
    now = time.time()
    last = _last_write.get(handle, 0.0)
    if now - last < RATE_LIMIT_SECONDS:
        return True
    _last_write[handle] = now
    return False


async def _with_reactions(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped = await db.get_reactions_many([m["id"] for m in messages])
    for m in messages:
        m["reactions"] = grouped.get(m["id"], [])
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


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _routine_task
    await db.init_db()
    await v2.init_db()
    await work.init_db()
    await knowledge.init_db()
    for recoverable in await v2.recoverable_runs():
        v2.start_run(recoverable["id"], recoverable["owner"])
    recovered_work = await work.recover_interrupted()
    if recovered_work:
        _logger.info("work sessions settled after restart: %d", recovered_work)
    from .tools import system as system_mod
    await system_mod.hydrate_root()
    await reload_registry()
    _routine_task = _track_task(_routine_loop(), "routine scheduler")
    yield
    if _routine_task is not None:
        _routine_task.cancel()
        with suppress(asyncio.CancelledError):
            await _routine_task
        _routine_task = None
    pending = [task for task in _background_tasks if not task.done()]
    for task in pending:
        task.cancel()
    if pending:
        await asyncio.gather(*pending, return_exceptions=True)
    _background_tasks.clear()
    await v2.shutdown()


app.router.lifespan_context = lifespan


def _track_task(coro: Any, label: str) -> asyncio.Task:
    task = asyncio.create_task(coro, name=label)
    _background_tasks.add(task)

    def finished(done: asyncio.Task) -> None:
        _background_tasks.discard(done)
        if done.cancelled():
            return
        exc = done.exception()
        if exc is not None:
            _logger.error(
                "background task failed: %s",
                label,
                exc_info=(type(exc), exc, exc.__traceback__),
            )

    task.add_done_callback(finished)
    return task


# --------------------------------------------------------------- status ---

# --------------------------------------------------------------- v2 workflows/runs ---

@app.get("/api/v2/workflows")
async def api_v2_workflows(handle: str = Depends(require_auth)):
    return await v2.list_workflows(handle)


@app.get("/api/v2/providers")
async def api_v2_providers(handle: str = Depends(require_auth)):
    catalog = list_providers()
    connections = {c["provider_id"]: c for c in await ai_store.status()}
    for provider in catalog:
        spec = get_provider(provider["id"]) or {}
        conn = connections.get(provider["id"])
        env_name = spec.get("env_fallback")
        env_ok = _key_set(env_name) if env_name else False
        provider["connected"] = conn is not None or env_ok
        provider["via"] = "stored" if conn else ("env" if env_ok else None)
        if conn:
            provider["model"] = conn.get("model")
            provider["key_hint"] = conn.get("key_hint")
            provider["connected_at"] = conn.get("connected_at")
        elif env_ok:
            provider["model"] = spec.get("default_model")
        provider["capabilities"] = {"streaming": True, "tool_calling": spec.get("kind") == "openai_compatible", "vision": False}
        provider["oauth_configured"] = bool(spec.get("oauth_authorize_url") and os.environ.get(f"SWARM_{provider['id'].upper()}_OAUTH_CLIENT_ID"))
    return catalog


@app.get("/api/v2/providers/{provider_id}/oauth/start")
async def api_v2_oauth_start(provider_id: str, handle: str = Depends(require_auth)):
    spec = get_provider(provider_id)
    if not spec:
        raise HTTPException(status_code=404, detail="provider not found")
    try:
        return {"provider_id": provider_id, "authorization_url": v2.oauth_start(provider_id, handle, spec)}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.get("/api/v2/oauth/callback")
async def api_v2_oauth_callback(code: str, state: str):
    try:
        return await v2.oauth_callback(state, code)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:  # noqa: BLE001
        _logger.warning("provider OAuth callback failed: %s", exc)
        raise HTTPException(status_code=502, detail="provider OAuth exchange failed")


@app.get("/api/v2/providers/{provider_id}/models")
async def api_v2_provider_models(provider_id: str, handle: str = Depends(require_auth)):
    try:
        return await list_provider_models(provider_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="provider not found")


@app.get("/api/v2/models/connected")
async def api_v2_connected_models(handle: str = Depends(require_auth)):
    """Return live models from the first connected provider, for dynamic UI defaults."""
    from .ai_support.providers import providers_by_priority
    from .ai_support.resolver import resolve_runtime_auth

    for spec in providers_by_priority():
        auth = await resolve_runtime_auth(spec["id"])
        if auth is None:
            continue
        body = await list_provider_models(spec["id"])
        models = body.get("models") or []
        if not models:
            continue
        preferred = auth.default_model or body.get("default_model")
        if preferred and not any(m.get("id") == preferred for m in models):
            preferred = models[0]["id"]
        preferred = preferred or (models[0]["id"] if models else None)
        return {
            "provider_id": spec["id"],
            "provider_name": spec.get("name") or spec["id"],
            "live": bool(body.get("live")),
            "models": models,
            "default_model": preferred,
            "note": body.get("note"),
        }
    merged = await list_all_models()
    models = merged.get("models") or []
    return {
        "provider_id": None,
        "provider_name": None,
        "live": False,
        "models": models,
        "default_model": models[0]["id"] if models else None,
        "note": "Connect a provider to fetch live models from its API.",
    }


@app.get("/api/v2/model-routing/validate")
async def api_v2_validate_model(provider_id: str, model: str, requires_tools: bool = False, handle: str = Depends(require_auth)):
    spec = get_provider(provider_id)
    if not spec:
        raise HTTPException(status_code=404, detail="provider not found")
    connected = await ai_store.resolve_key(provider_id, env_fallback=spec.get("env_fallback"))
    supported = not requires_tools or spec.get("kind") == "openai_compatible"
    return {"provider_id": provider_id, "model": model, "connected": bool(connected), "supported": supported, "reason": None if supported else "This provider adapter does not support tool calling yet."}


@app.post("/api/v2/workflows")
async def api_v2_create_workflow(payload: WorkflowCreate, handle: str = Depends(require_auth)):
    errors = v2.validate_graph(payload.graph)
    if errors:
        raise HTTPException(status_code=422, detail={"message": "invalid workflow graph", "errors": errors})
    return await v2.create_workflow(handle, payload.name, payload.description, payload.graph)


@app.get("/api/v2/workflows/{workflow_id}")
async def api_v2_get_workflow(workflow_id: str, handle: str = Depends(require_auth)):
    row = await v2.get_workflow(workflow_id, handle)
    if not row:
        raise HTTPException(status_code=404, detail="workflow not found")
    return row


@app.patch("/api/v2/workflows/{workflow_id}")
async def api_v2_patch_workflow(workflow_id: str, payload: WorkflowPatch, handle: str = Depends(require_auth)):
    if payload.graph is not None:
        errors = v2.validate_graph(payload.graph)
        if errors:
            raise HTTPException(status_code=422, detail={"message": "invalid workflow graph", "errors": errors})
    row = await v2.update_workflow(workflow_id, handle, payload.model_dump(exclude_unset=True))
    if not row:
        raise HTTPException(status_code=404, detail="workflow not found")
    return row


@app.get("/api/v2/runs")
async def api_v2_runs(limit: int = Query(default=50, ge=1, le=100), handle: str = Depends(require_auth)):
    return await v2.list_runs(handle, limit)


@app.post("/api/v2/runs")
async def api_v2_create_run(payload: RunCreate, handle: str = Depends(require_auth)):
    if payload.workflow_id and not await v2.get_workflow(payload.workflow_id, handle):
        raise HTTPException(status_code=404, detail="workflow not found")
    run = await v2.create_run(handle, payload.objective, payload.workflow_id, payload.policy, payload.model)
    session = await work.create_session(
        handle, payload.objective, source="run", run_id=run["id"])
    await work.append_event(session["id"], "work_started", {"objective": payload.objective})
    v2.start_run(run["id"], handle)
    return run


@app.get("/api/v2/runs/{run_id}")
async def api_v2_get_run(run_id: str, handle: str = Depends(require_auth)):
    row = await v2.get_run(run_id, handle)
    if not row:
        raise HTTPException(status_code=404, detail="run not found")
    return row


@app.get("/api/v2/runs/{run_id}/events")
async def api_v2_run_events(run_id: str, after: int = Query(default=0, ge=0), handle: str = Depends(require_auth)):
    if not await v2.get_run(run_id, handle):
        raise HTTPException(status_code=404, detail="run not found")
    return await v2.list_events(run_id, handle, after)


@app.get("/api/v2/runs/{run_id}/report")
async def api_v2_run_report(run_id: str, handle: str = Depends(require_auth)):
    run = await v2.get_run(run_id, handle)
    if not run:
        raise HTTPException(status_code=404, detail="run not found")
    if not run.get("report"):
        raise HTTPException(status_code=409, detail="run report is not ready")
    return {"run_id": run_id, "status": run["status"], "report": run["report"]}


@app.get("/api/v2/runs/{run_id}/artifacts")
async def api_v2_run_artifacts(run_id: str, handle: str = Depends(require_auth)):
    if not await v2.get_run(run_id, handle):
        raise HTTPException(status_code=404, detail="run not found")
    rows = await v2.list_artifacts(run_id, handle)
    for row in rows:
        row["download_url"] = f"/api/v2/artifacts/{row['id']}"
        row.pop("uri", None)
    return rows


@app.get("/api/v2/artifacts/{artifact_id}")
async def api_v2_download_artifact(artifact_id: str, handle: str = Depends(require_auth)):
    row = await v2.get_artifact(artifact_id, handle)
    if not row:
        raise HTTPException(status_code=404, detail="artifact not found")
    target = Path(str(row["uri"])).resolve()
    from .agent import SANDBOX_DIR
    root = Path(SANDBOX_DIR).resolve()
    if root not in target.parents or not target.is_file():
        raise HTTPException(status_code=404, detail="artifact file not found")
    return FileResponse(target, media_type=row.get("mime_type") or "application/octet-stream", filename=row.get("name") or target.name)


@app.post("/api/v2/runs/{run_id}/start")
async def api_v2_start_run(run_id: str, handle: str = Depends(require_auth)):
    run = await v2.get_run(run_id, handle)
    if not run:
        raise HTTPException(status_code=404, detail="run not found")
    v2.start_run(run_id, handle)
    return await v2.get_run(run_id, handle)


@app.post("/api/v2/runs/{run_id}/cancel")
async def api_v2_cancel_run(run_id: str, handle: str = Depends(require_auth)):
    if not await v2.cancel_run(run_id, handle):
        raise HTTPException(status_code=404, detail="run is not active")
    return await v2.get_run(run_id, handle)


@app.post("/api/v2/runs/{run_id}/approvals/{step_id}")
async def api_v2_resolve_run_approval(run_id: str, step_id: str, payload: ApprovalResolve, handle: str = Depends(require_auth)):
    run = await v2.get_run(run_id, handle)
    if not run:
        raise HTTPException(status_code=404, detail="run not found")
    if run["status"] != "waiting_for_approval":
        raise HTTPException(status_code=409, detail="run is not waiting for approval")
    await v2.append_event(run_id, "approval_resolved", {"decision": payload.status}, step_id)
    session = await work.find_by_run(run_id, handle)
    if session:
        await work.append_event(
            session["id"], "approval_resolved",
            {"decision": payload.status, "step_id": step_id}, step_id)
    if payload.status == "approved":
        v2.start_run(run_id, handle)
    else:
        await v2.update_run(run_id, handle, "cancelled")
        await v2.append_event(run_id, "run_denied", {"step_id": step_id})
        if session:
            await work.append_event(session["id"], "work_cancelled", {"step_id": step_id}, step_id)
    return await v2.get_run(run_id, handle)


# --------------------------------------------------------------- work sessions ---
# Normalized interface unifying chat replies, v2 runs, routines and handoffs.

async def _sync_work_with_run(session: dict[str, Any], owner: str) -> dict[str, Any]:
    """Mirror a linked run's terminal/waiting state into the work session."""
    run_id = session.get("run_id")
    if not run_id or session.get("status") in work.TERMINAL:
        return session
    run = await v2.get_run(run_id, owner)
    if not run:
        return session
    status = run.get("status")
    if status == "waiting_for_approval" and session.get("status") != "waiting_for_approval":
        await work.append_event(session["id"], "approval_requested",
                                {"label": "Workflow checkpoint requires review"}, "approval")
    elif status == "completed" and session.get("status") != "completed":
        report = run.get("report") or {}
        await work.append_event(session["id"], "work_completed",
                                {"summary": str(report.get("summary") or "")[:1000]})
    elif status in {"failed", "cancelled"} and session.get("status") not in work.TERMINAL:
        if status == "failed":
            report = run.get("report") or {}
            await work.append_event(session["id"], "work_failed",
                                    {"error": str(report.get("error") or "run failed")[:500]})
        else:
            await work.append_event(session["id"], "work_cancelled", {})
    return await work.get_session(session["id"], owner) or session


@app.get("/api/work")
async def api_list_work(limit: int = Query(default=50, ge=1, le=100), handle: str = Depends(require_auth)):
    sessions = await work.list_sessions(handle, limit)
    synced = []
    for session in sessions:
        synced.append(await _sync_work_with_run(session, handle))
    return synced


@app.get("/api/work/{work_id}")
async def api_get_work(work_id: str, handle: str = Depends(require_auth)):
    session = await work.get_session(work_id, handle)
    if not session:
        raise HTTPException(status_code=404, detail="work not found")
    session = await _sync_work_with_run(session, handle)
    session["messages"] = await work.linked_messages(work_id, handle)
    return session


@app.get("/api/work/{work_id}/messages")
async def api_work_messages(work_id: str, handle: str = Depends(require_auth)):
    """Full message objects linked to a session, for the detail panel."""
    if not await work.get_session(work_id, handle):
        raise HTTPException(status_code=404, detail="work not found")
    out = []
    for msg_id in await work.linked_messages(work_id, handle):
        row = await db.get_message(msg_id)
        if not row:
            continue
        channel = await db.get_channel(row.get("channel_id"))
        if not db.can_view_channel(channel, handle):
            continue
        row["reactions"] = await db.get_reactions(msg_id)
        out.append(row)
    return out


@app.get("/api/work/{work_id}/events")
async def api_work_events(
    work_id: str, after: int = Query(default=0, ge=0), handle: str = Depends(require_auth)
):
    session = await work.get_session(work_id, handle)
    if not session:
        raise HTTPException(status_code=404, detail="work not found")
    await _sync_work_with_run(session, handle)
    return await work.list_events(work_id, handle, after)


@app.post("/api/work/{work_id}/cancel")
async def api_cancel_work(work_id: str, handle: str = Depends(require_auth)):
    session = await work.cancel_session(work_id, handle)
    if not session:
        raise HTTPException(status_code=404, detail="work not found")
    return session


@app.websocket("/api/v2/ws/runs/{run_id}")
async def ws_v2_run(websocket: WebSocket, run_id: str):
    await websocket.accept()
    try:
        first = await websocket.receive_json()
        parsed = parse_token(first.get("token", "")) if isinstance(first, dict) else None
        if parsed is None or not await db.verify_token(parsed[0], parsed[1]):
            await websocket.close(code=4001)
            return
        owner = parsed[0]
        if not await v2.get_run(run_id, owner):
            await websocket.close(code=4004)
            return
        cursor = int(first.get("after", 0) or 0)
        while True:
            events = await v2.list_events(run_id, owner, cursor)
            for event in events:
                await websocket.send_json(event)
                cursor = max(cursor, int(event["seq"]))
            run = await v2.get_run(run_id, owner)
            if run and run["status"] in {"completed", "failed", "cancelled"}:
                await websocket.send_json({"type": "run_terminal", "status": run["status"], "run_id": run_id})
                break
            await asyncio.sleep(0.75)
    except WebSocketDisconnect:
        pass
    except Exception:
        with suppress(Exception):
            await websocket.close(code=1011)

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
    from .tools import browser as browser_mod
    from .tools import connectors
    from .tools import system as system_mod
    body: dict[str, Any] = {
        "groq": providers_ready.get("groq", False),
        "openrouter": providers_ready.get("openrouter", False),
        "demo": demo,
        "llm_ready": llm_ready,
        "providers_ready": providers_ready,
        "browser": browser_mod.enabled(),
        "system": system_mod.enabled(),
    }
    for row in await connectors.catalog_status():
        body[row["id"]] = bool(row.get("connected"))
    body["onboarded"] = await db.workspace_onboarded()
    body["admins"] = await db.list_admin_handles()
    if handle:
        body["ai_providers"] = connections
        user = await db.get_user(handle)
        if user:
            body["me"] = {"handle": user["handle"], "role": user["role"]}
    return body


# ---------------------------------------------------------------- auth ----

MIN_ADMIN_PASSWORD = 4


def _session_body(handle: str, raw: str, role: str, *, created: bool, onboarded: bool) -> dict[str, Any]:
    return {
        "handle": handle,
        "token": f"{handle}:{raw}",
        "created": created,
        "role": role,
        "onboarded": onboarded,
    }


@app.post("/api/register")
async def api_register(payload: RegisterRequest, request: Request):
    local = is_loopback(request)
    password = payload.password
    existing = await db.get_user(payload.handle)
    onboarded = await db.workspace_onboarded()

    if existing is not None:
        if existing["role"] == "admin":
            has_pw = await db.user_has_password(payload.handle)
            if has_pw:
                if not await db.admin_password_ok(payload.handle, password):
                    raise HTTPException(403, "wrong or missing admin password")
            elif not local:
                raise HTTPException(409, "handle already registered")
        elif not local:
            raise HTTPException(409, "handle already registered")
        raw = db.generate_token()
        user = await db.rotate_user_token(payload.handle, raw)
        if user is None:
            raise HTTPException(409, "handle already registered")
        return _session_body(
            payload.handle, raw, user["role"], created=False, onboarded=onboarded,
        )

    if await db.user_count() == 0:
        if password is not None and len(password) < MIN_ADMIN_PASSWORD:
            raise HTTPException(400, f"admin password must be at least {MIN_ADMIN_PASSWORD} characters")
        raw = db.generate_token()
        created = await db.create_user(payload.handle, raw, role="admin", password=password)
        return _session_body(
            payload.handle, raw, created["role"], created=True, onboarded=False,
        )

    raw = db.generate_token()
    created = await db.create_user(payload.handle, raw, role="member")
    return _session_body(
        payload.handle, raw, created["role"], created=True, onboarded=onboarded,
    )


@app.post("/api/workspace/onboarded")
async def api_mark_onboarded(handle: str = Depends(require_admin)):
    await db.mark_workspace_onboarded()
    return {"onboarded": True}


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
    if payload.parent_id is not None and not await db.message_in_channel(
        payload.parent_id, channel_id
    ):
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
    if msg.get("author") != handle and await db.get_user_role(handle) != "admin":
        raise HTTPException(403, "only the author or an admin can delete this message")
    ids = await db.delete_message(message_id)
    await hub.broadcast(msg["channel_id"], {
        "type": "message_deleted",
        "message_id": message_id,
        "ids": ids,
    })
    return {"ok": True, "ids": ids}


@app.post("/api/messages/{message_id}/retry")
async def api_retry_agent_message(message_id: int, handle: str = Depends(require_auth)):
    """Re-run an agent after a provider/network error message."""
    if _rate_limited(handle):
        raise HTTPException(429, "slow down")
    msg = await db.get_message(message_id)
    if msg is None:
        raise HTTPException(404, "no such message")
    channel_id = msg["channel_id"]
    await _require_channel(channel_id, handle)
    if msg.get("author_kind") != "agent" or not agent.is_agent_error(msg.get("body") or ""):
        raise HTTPException(400, "only agent error messages can be retried")

    agent_row = await db.fetch_agent(msg["author"])
    if agent_row is None or agent_row.get("archived"):
        raise HTTPException(404, "agent not found")

    parent_id = msg.get("parent_id")
    history = await db.get_history(channel_id, limit=120, before_id=message_id)
    trigger = None
    for candidate in reversed(history):
        if candidate.get("parent_id") != parent_id:
            continue
        kind = candidate.get("author_kind")
        body = candidate.get("body") or ""
        if kind == "human":
            trigger = candidate
            break
        if kind == "system" and body.startswith("[routine:"):
            trigger = candidate
            break
    if trigger is None:
        raise HTTPException(400, "could not find the message that triggered this agent")

    ids = await db.delete_message(message_id)
    await hub.broadcast(channel_id, {
        "type": "message_deleted",
        "message_id": message_id,
        "ids": ids,
    })
    _track_task(_run_agent(channel_id, agent_row), "agent retry")
    return {"ok": True, "trigger_id": trigger["id"], "agent": agent_row["name"]}


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


@app.delete("/api/messages/{message_id}/reactions")
async def api_remove_reaction(
    message_id: int, emoji: str = Query(min_length=1, max_length=8),
    handle: str = Depends(require_auth),
):
    if not await db.message_exists(message_id):
        raise HTTPException(404, "no such message")
    removed = await db.remove_reaction(message_id, handle, emoji)
    if removed:
        await hub.broadcast(await db.channel_of_message(message_id), {
            "type": "reaction_removed", "message_id": message_id,
            "author": handle, "emoji": emoji,
        })
    return {"ok": True, "removed": removed}


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


@app.get("/api/agent-templates")
async def api_agent_templates():
    return get_agent_templates()


@app.post("/api/agents")
async def api_create_agent(payload: AgentCreate, handle: str = Depends(require_admin)):
    if _rate_limited(handle):
        raise HTTPException(429, "slow down")
    if await db.agent_exists(payload.name):
        raise HTTPException(409, "agent name already registered")
    if payload.channel_scope and not await db.channel_exists(payload.channel_scope):
        raise HTTPException(404, "no such channel")
    await _validate_tool_names(payload.tools)
    created = await db.create_agent(
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
    await db.mark_workspace_onboarded()
    return created


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
    return [{**job, "profile": load_job_profile(job["id"]) or ""} for job in JOB_TEMPLATES]


@app.get("/api/profiles")
async def api_list_profiles(handle: str = Depends(require_auth)):
    return list_profiles()


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
        spec = get_provider(p["id"]) or {}
        conn = connections.get(p["id"])
        env_name = spec.get("env_fallback")
        env_ok = _key_set(env_name) if env_name else False
        p["connected"] = conn is not None or env_ok
        p["via"] = "stored" if conn else ("env" if env_ok else None)
        if conn:
            p["model"] = conn.get("model")
            p["key_hint"] = conn.get("key_hint")
            p["connected_at"] = conn.get("connected_at")
        elif env_ok:
            p["model"] = spec.get("default_model")
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


@app.patch("/api/ai-support/connect/{provider_id}")
async def api_ai_set_model(
    provider_id: str, payload: AiProviderModel, handle: str = Depends(require_admin)
):
    if _rate_limited(handle):
        raise HTTPException(429, "slow down")
    if get_provider(provider_id) is None:
        raise HTTPException(404, "unknown provider")
    row = await ai_store.set_model(provider_id, payload.model.strip())
    if row is None:
        raise HTTPException(404, "not connected")
    return row


@app.get("/api/ai-support/models")
async def api_ai_models(handle: str = Depends(require_auth)):
    return await list_all_models()


@app.get("/api/ai-support/providers/{provider_id}/models")
async def api_ai_provider_models(provider_id: str, handle: str = Depends(require_auth)):
    if get_provider(provider_id) is None:
        raise HTTPException(404, "unknown provider")
    return await list_provider_models(provider_id)


@app.post("/api/ai-support/providers/{provider_id}/models")
async def api_ai_provider_models_preview(
    provider_id: str, payload: AiModelsPreview, handle: str = Depends(require_auth)
):
    if get_provider(provider_id) is None:
        raise HTTPException(404, "unknown provider")
    return await list_provider_models(provider_id, api_key=payload.api_key)


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
    _track_task(_execute_routine(row, test_run=True), f"routine {routine_id} manual run")
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
    if channel_id is not None:
        await _require_channel(channel_id, handle)
        return await db.list_approvals(channel_id=channel_id, status=status)
    rows = await db.list_approvals(status=status)
    visible: list[dict[str, Any]] = []
    for row in rows:
        channel = await db.get_channel(row["channel_id"])
        if db.can_view_channel(channel, handle):
            visible.append(row)
    return visible


@app.post("/api/approvals/{approval_id}/resolve")
async def api_resolve_approval(
    approval_id: int, payload: ApprovalResolve, handle: str = Depends(require_auth)
):
    if _rate_limited(handle):
        raise HTTPException(429, "slow down")
    row = await db.get_approval(approval_id)
    if row is None:
        raise HTTPException(404, "no such approval")
    await _require_channel(row["channel_id"], handle)
    if row["status"] != "pending":
        raise HTTPException(409, "already resolved")
    updated = await db.resolve_approval(approval_id, payload.status)
    if updated is None or updated.get("_already_resolved"):
        raise HTTPException(409, "already resolved")
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
    for candidate in await work.list_sessions(handle, 50):
        if (candidate.get("channel_id") == row["channel_id"]
                and candidate.get("status") == "waiting_for_approval"):
            await _emit_work_event(
                row["channel_id"], candidate, "approval_resolved",
                {"decision": payload.status, "approval_id": approval_id},
                candidate.get("active_step"))
            break
    await _maybe_trigger_agents(row["channel_id"], msg)
    return updated


# --------------------------------------------------------------- memory ---
@app.get("/api/agents/{agent_name}/memory")
async def api_list_memory(agent_name: str, limit: int = Query(default=20, ge=1, le=100),
                          handle: str = Depends(require_auth)):
    if not await db.fetch_agent(agent_name):
        raise HTTPException(status_code=404, detail="agent not found")
    return await db.list_memories(agent_name, limit)


@app.delete("/api/agents/{agent_name}/memory/{memory_id}")
async def api_forget_memory(agent_name: str, memory_id: int, handle: str = Depends(require_auth)):
    if _rate_limited(handle):
        raise HTTPException(429, "slow down")
    if not await db.fetch_agent(agent_name):
        raise HTTPException(status_code=404, detail="agent not found")
    if not await db.delete_memory(memory_id, agent_name):
        raise HTTPException(status_code=404, detail="no such memory")
    return {"ok": True, "id": memory_id}


# --------------------------------------------------------------- knowledge ---
@app.get("/api/knowledge")
async def api_list_knowledge(limit: int = Query(default=50, ge=1, le=100),
                             handle: str = Depends(require_auth)):
    return await knowledge.list_docs(handle, limit)


@app.post("/api/knowledge")
async def api_create_knowledge(payload: dict, handle: str = Depends(require_auth)):
    if _rate_limited(handle):
        raise HTTPException(429, "slow down")
    try:
        return await knowledge.create_doc(
            handle, str(payload.get("title") or ""), str(payload.get("body") or ""),
            tags=str(payload.get("tags") or ""))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.get("/api/knowledge/search")
async def api_search_knowledge(q: str = "", limit: int = Query(default=5, ge=1, le=20),
                               handle: str = Depends(require_auth)):
    if len((q or "").strip()) < 2:
        raise HTTPException(status_code=400, detail="query must be at least 2 characters")
    return await knowledge.search_docs(q, owners=[handle], limit=limit)


@app.get("/api/knowledge/{doc_id}")
async def api_get_knowledge(doc_id: str, handle: str = Depends(require_auth)):
    row = await knowledge.get_doc(doc_id, handle)
    if not row:
        raise HTTPException(status_code=404, detail="knowledge doc not found")
    return row


@app.patch("/api/knowledge/{doc_id}")
async def api_patch_knowledge(doc_id: str, payload: dict, handle: str = Depends(require_auth)):
    if _rate_limited(handle):
        raise HTTPException(429, "slow down")
    row = await knowledge.update_doc(doc_id, handle, payload or {})
    if not row:
        raise HTTPException(status_code=404, detail="knowledge doc not found")
    return row


@app.delete("/api/knowledge/{doc_id}")
async def api_delete_knowledge(doc_id: str, handle: str = Depends(require_auth)):
    if _rate_limited(handle):
        raise HTTPException(429, "slow down")
    if not await knowledge.delete_doc(doc_id, handle):
        raise HTTPException(status_code=404, detail="knowledge doc not found")
    return {"ok": True, "id": doc_id}


# --------------------------------------------------------------- context ---
@app.get("/api/channels/{channel_id}/context")
async def api_context_stats(channel_id: str, agent: str | None = None,
                            handle: str = Depends(require_auth)):
    await _require_channel(channel_id, handle)
    agent_name = agent
    if agent_name and not await db.fetch_agent(agent_name):
        raise HTTPException(status_code=404, detail="agent not found")
    return await context.context_stats(channel_id, agent_name)


@app.post("/api/channels/{channel_id}/compact")
async def api_compact_channel(channel_id: str, payload: dict | None = None,
                              handle: str = Depends(require_auth)):
    if _rate_limited(handle):
        raise HTTPException(429, "slow down")
    await _require_channel(channel_id, handle)
    agent_name = (payload or {}).get("agent") if payload else None
    if agent_name and not await db.fetch_agent(agent_name):
        raise HTTPException(status_code=404, detail="agent not found")
    if not agent_name:
        channel = await db.get_channel(channel_id)
        agent_name = (channel or {}).get("owner_agent") or "swarm"
    summary = await context.compact_channel(channel_id, agent_name)
    if not summary:
        raise HTTPException(status_code=409, detail="not enough history to compact yet")
    return summary


@app.get("/api/computer")
async def api_computer(handle: str = Depends(require_auth)):
    from .tools import computer as computer_mod
    from .tools import system as system_mod
    await system_mod.hydrate_root()
    system_info = system_mod.listing("")
    return {
        "workspace": agent.SANDBOX_DIR,
        "shared": True,
        "files": agent.list_workspace_files(),
        "activity": await db.get_recent_system_messages(20),
        "computer": computer_mod.status(),
        "system": system_info,
        "note": (
            "Bots share two places on this host: the sandbox (Sandbox tab) and "
            f"the machine folder {system_info.get('root')} (System tab, system_run). "
            "Change that folder from System → Places. Optional Playwright, Browser Use "
            "CLI, CUA, Exa/Tavily/Firecrawl, and Composio apps live in the other tabs."
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


@app.post("/api/computer/run")
async def api_computer_run(payload: ComputerRunRequest, handle: str = Depends(require_auth)):
    if _rate_limited(handle):
        raise HTTPException(429, "slow down")
    from .tools import computer as computer_mod
    output = computer_mod.computer_run(payload.command.strip())
    return {"command": payload.command.strip(), "output": output}


@app.get("/api/computer/system")
async def api_computer_system(path: str = "", handle: str = Depends(require_auth)):
    from .tools import system as system_mod
    await system_mod.hydrate_root()
    return system_mod.listing(path)


@app.post("/api/computer/system/root")
async def api_computer_system_root(
    payload: SystemRootSet, handle: str = Depends(require_auth)
):
    if _rate_limited(handle):
        raise HTTPException(429, "slow down")
    from .tools import system as system_mod
    result = await system_mod.set_root(payload.path)
    if not result.get("ok"):
        raise HTTPException(400, result.get("error") or "invalid path")
    return system_mod.listing("")


@app.get("/api/computer/system/file")
async def api_computer_system_file(path: str, handle: str = Depends(require_auth)):
    from .tools import system as system_mod
    if not system_mod.enabled():
        raise HTTPException(403, "system tools disabled")
    await system_mod.hydrate_root()
    target = system_mod.safe_path(path)
    if target is None or not target.is_file():
        raise HTTPException(404, "no such file")
    if target.stat().st_size > system_mod.API_PREVIEW_BYTES:
        raise HTTPException(413, "file too large to preview")
    try:
        raw = target.read_bytes()
    except OSError as exc:
        raise HTTPException(400, str(exc)) from exc
    if b"\x00" in raw[:1024]:
        raise HTTPException(415, "binary file")
    return {
        "path": system_mod.rel_to_root(target) or path,
        "content": raw.decode("utf-8", errors="replace"),
    }


@app.get("/api/browser/status")
async def api_browser_status(handle: str = Depends(require_auth)):
    from .tools import browser as browser_mod
    return browser_mod.status()


@app.post("/api/browser/close")
async def api_browser_close(handle: str = Depends(require_admin)):
    if _rate_limited(handle):
        raise HTTPException(429, "slow down")
    from .tools import browser as browser_mod
    message = await browser_mod.close()
    return {"ok": True, "message": message}


@app.get("/api/connectors")
async def api_list_connectors(handle: str = Depends(require_auth)):
    from .tools import connectors
    return await connectors.catalog_status()


@app.get("/api/connectors/{connector_id}")
async def api_get_connector(connector_id: str, handle: str = Depends(require_auth)):
    from .tools import connectors
    if connectors.get_connector(connector_id) is None:
        raise HTTPException(404, "unknown connector")
    return await connectors.connector_status(connector_id)


@app.post("/api/connectors/{connector_id}/connect")
async def api_connector_connect(
    connector_id: str, payload: AiProviderConnect, handle: str = Depends(require_admin)
):
    if _rate_limited(handle):
        raise HTTPException(429, "slow down")
    from .tools import connectors
    spec = connectors.get_connector(connector_id)
    if spec is None:
        raise HTTPException(404, "unknown connector")
    if spec.get("kind") == "cli":
        raise HTTPException(400, "this connector uses a local CLI, not an API key")
    return await ai_store.connect(connector_id, payload.api_key)


@app.delete("/api/connectors/{connector_id}/connect")
async def api_connector_disconnect(
    connector_id: str, handle: str = Depends(require_admin)
):
    if _rate_limited(handle):
        raise HTTPException(429, "slow down")
    from .tools import connectors
    if connectors.get_connector(connector_id) is None:
        raise HTTPException(404, "unknown connector")
    if not await ai_store.disconnect(connector_id):
        raise HTTPException(404, "not connected")
    return {"ok": True}


@app.get("/api/composio/status")
async def api_composio_status(handle: str = Depends(require_auth)):
    from .tools import composio_client
    return await composio_client.status()


@app.post("/api/composio/connect")
async def api_composio_connect(
    payload: AiProviderConnect, handle: str = Depends(require_admin)
):
    if _rate_limited(handle):
        raise HTTPException(429, "slow down")
    return await ai_store.connect("composio", payload.api_key)


@app.delete("/api/composio/connect")
async def api_composio_disconnect(handle: str = Depends(require_admin)):
    if _rate_limited(handle):
        raise HTTPException(429, "slow down")
    if not await ai_store.disconnect("composio"):
        raise HTTPException(404, "not connected")
    return {"ok": True}


@app.get("/api/composio/toolkits")
async def api_composio_toolkits(
    q: str = "", handle: str = Depends(require_auth)
):
    from .tools import composio_client
    return {"text": await composio_client.list_toolkits(q)}


@app.post("/api/composio/connect-toolkit")
async def api_composio_connect_toolkit(
    payload: ComposioToolkitConnect, handle: str = Depends(require_admin)
):
    if _rate_limited(handle):
        raise HTTPException(429, "slow down")
    from .tools import composio_client
    return {"text": await composio_client.connect_toolkit(payload.toolkit)}


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
            if parent_id is not None:
                try:
                    parent_id = int(parent_id)
                except (TypeError, ValueError):
                    await websocket.send_json({"type": "error", "detail": "invalid parent message"})
                    continue
                if not await db.message_in_channel(parent_id, channel_id):
                    await websocket.send_json({"type": "error", "detail": "parent message does not exist"})
                    continue
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
        _track_task(_run_agents_in_order(channel_id, mentioned, depth=depth + 1), "agent handoff")
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
        _track_task(_run_agents_in_order(channel_id, to_run, depth=depth), "agent reply batch")


async def _run_agents_in_order(channel_id: str, agents: list[dict], *, depth: int = 0) -> None:
    for a in agents:
        await _run_agent(channel_id, a, depth=depth)


async def _set_status(name: str, status: str) -> None:
    await db.set_agent_status(name, status)
    await hub.broadcast_all({"type": "bot_status", "name": name, "status": status})


async def _emit_work_event(
    channel_id: str | None, session: dict[str, Any],
    event_type: str, payload: dict[str, Any] | None = None,
    step_id: str | None = None,
) -> dict[str, Any]:
    """Append a work event and fan it out on the channel socket (compat adapter).

    Existing chat WS clients ignore unknown `type` values, so broadcasting
    `{"type": "work", "event": {...}}` preserves their behavior while the new
    workspace rail gets live updates without a second socket.
    """
    event = await work.append_event(session["id"], event_type, payload, step_id)
    if channel_id:
        with suppress(Exception):
            await hub.broadcast(channel_id, {"type": "work", "event": event})
    return event


def _work_owner_from_history(history: list[dict[str, Any]]) -> tuple[str, str]:
    objective = ""
    owner = "system"
    for entry in reversed(history):
        if entry.get("author_kind") == "human" and (entry.get("body") or "").strip():
            owner = str(entry.get("author") or "system")
            objective = str(entry.get("body") or "")[:500]
            break
    if not objective:
        for entry in reversed(history):
            if (entry.get("body") or "").strip():
                objective = str(entry.get("body") or "")[:500]
                break
    return owner, objective


async def _run_agent(channel_id: str, agent_row: dict, *, depth: int = 0,
                   source: str | None = None) -> dict:
    if agent_row.get("archived"):
        return {"reply": "", "tool_events": []}
    name = agent_row["name"]
    await _set_status(name, "working")
    await hub.broadcast(channel_id, {"type": "typing", "author": name})
    window = agent.history_window_of(agent_row)
    history = await db.get_history(channel_id, limit=max(window * 2, window))
    tools_posted = False
    pending_approvals: list[dict] = []
    owner, objective = _work_owner_from_history(history)
    session = await work.create_session(
        owner, objective or f"@{name}",
        source=source or ("handoff" if depth > 0 else "chat"), channel_id=channel_id)
    await _emit_work_event(channel_id, session, "work_started",
                           {"objective": objective or f"@{name}", "agent": name})
    await _emit_work_event(channel_id, session, "agent_started",
                           {"agent": name, "role": agent_row.get("role") or "agent"}, name)

    async def persist_tools(events: list[dict]) -> None:
        nonlocal tools_posted
        if tools_posted:
            return
        tools_posted = True
        for event in events:
            note = f"{name} ran: {event['tool']}({event['args']}) -> {event['result'][:200]}"
            sys_msg = await db.add_message(channel_id, name, note, "system")
            await hub.broadcast(channel_id, {"type": "message", "message": sys_msg})
            tool_name = str(event.get("tool") or "tool")
            await _emit_work_event(channel_id, session, "tool_started",
                                   {"tool": tool_name}, tool_name)
            await _emit_work_event(channel_id, session, "tool_finished",
                                   {"tool": tool_name,
                                    "result": str(event.get("result") or "")[:500]}, tool_name)
            if event["tool"] == "request_approval":
                pending = await db.list_approvals(channel_id=channel_id, status="pending")
                for row in pending:
                    if row["agent_name"] == name and row not in pending_approvals:
                        pending_approvals.append(row)
                        await hub.broadcast_all({"type": "approval", "approval": row})
                        await _emit_work_event(
                            channel_id, session, "approval_requested",
                            {"label": row.get("action") or "Approval requested",
                             "approval_id": row.get("id")}, tool_name)

    async def on_stream_start() -> None:
        await hub.broadcast(channel_id, {"type": "agent_stream_start", "author": name})

    async def on_token(delta: str) -> None:
        await hub.broadcast(channel_id, {"type": "agent_token", "author": name, "delta": delta})

    try:
        result = await agent.generate_reply(
            agent_row, channel_id, history,
            on_tools_ready=persist_tools,
            on_stream_start=on_stream_start,
            on_token=on_token,
        )
        await persist_tools(result["tool_events"])
        if any(e["tool"] == "create_agent" and "created bot" in str(e.get("result") or "")
               for e in result["tool_events"]):
            # A need-based bot (and its DM) just appeared — tell every
            # client to refresh its sidebar lists.
            await hub.broadcast_all({"type": "agents_changed"})

        msg = await db.add_message(channel_id, name, result["reply"], "agent")
        await hub.broadcast(channel_id, {"type": "message", "message": msg})
        await work.link_message(session["id"], int(msg["id"]))
        await _emit_work_event(channel_id, session, "message_linked",
                               {"message_id": msg["id"], "agent": name}, name)
        asked = any(e["tool"] == "request_approval" for e in result["tool_events"])
        await _set_status(name, "needs_approval" if asked else "idle")
        if not asked:
            completed_payload: dict[str, Any] = {
                "summary": str(result["reply"] or "")[:1000]}
            if isinstance(result.get("context"), dict):
                completed_payload["context"] = result["context"]
            await _emit_work_event(channel_id, session, "work_completed",
                                   completed_payload, name)
        if depth < HANDOFF_DEPTH:
            await _maybe_trigger_agents(channel_id, msg, depth=depth + 1)
        return result
    except asyncio.CancelledError:
        with suppress(Exception):
            await _emit_work_event(channel_id, session, "work_cancelled", {}, name)
        raise
    except Exception as exc:  # noqa: BLE001
        _logger.error(
            "agent run failed for %s",
            name,
            exc_info=(type(exc), exc, exc.__traceback__),
        )
        detail = agent.classify_error(exc)
        try:
            failure = await db.add_message(channel_id, name, detail, "agent")
            await hub.broadcast(channel_id, {"type": "message", "message": failure})
            await work.link_message(session["id"], int(failure["id"]))
            await _emit_work_event(channel_id, session, "work_failed",
                                   {"error": str(detail)[:500]}, name)
        finally:
            await _set_status(name, "idle")
        return {"reply": detail, "tool_events": [], "usage": {}}


async def _execute_routine(row: dict, *, test_run: bool = False) -> None:
    try:
        routine_id = int(row["id"])
        if routine_id in _active_routines:
            _logger.info("skipping already active routine %s", routine_id)
            return
        _active_routines.add(routine_id)
        agent_row = await db.fetch_agent(row["agent_name"])
        if agent_row is None or agent_row.get("archived"):
            raise RuntimeError("bot missing")
        channel_id = db.dm_channel_id(row["agent_name"])
        if not await db.channel_exists(channel_id):
            raise RuntimeError("no 1:1 channel")
        prefix = "[routine-test:" if test_run else "[routine:"
        body = (
            f"{prefix}{row['title']}] {row['instructions']}\n"
            "Do this job now. Stop for approval before any external send/publish/delete."
        )
        msg = await db.add_message(channel_id, "routine", body, "system")
        await hub.broadcast(channel_id, {"type": "message", "message": msg})
        result = await _run_agent(channel_id, agent_row, source="routine")
        excerpt = (result.get("reply") or "")[:240]
        await db.mark_routine_run(
            row["id"], status="ok", excerpt=excerpt,
            interval_minutes=row["interval_minutes"],
            advance_schedule=not test_run,
        )
    except Exception as exc:  # noqa: BLE001
        _logger.error(
            "routine %s failed",
            row.get("id"),
            exc_info=(type(exc), exc, exc.__traceback__),
        )
        try:
            await db.mark_routine_run(
                row["id"], status="failed", excerpt=str(exc)[:240],
                interval_minutes=row["interval_minutes"],
                advance_schedule=not test_run,
            )
        except Exception as persist_exc:  # noqa: BLE001
            _logger.error(
                "could not persist routine failure %s",
                row.get("id"),
                exc_info=(type(persist_exc), persist_exc, persist_exc.__traceback__),
            )
    finally:
        _active_routines.discard(int(row["id"]))


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
