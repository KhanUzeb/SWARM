"""Swarm v2 run/workflow primitives.

This module is intentionally independent from the legacy chat-triggered agent
loop.  It gives the new UI a durable, event-backed contract while the legacy
surface remains available during migration.
"""
from __future__ import annotations

import json
import time
import uuid
import asyncio
import base64
import hashlib
import os
import secrets
from urllib.parse import urlencode
from typing import Any
from pathlib import Path

import aiosqlite

from . import db

_run_tasks: dict[str, asyncio.Task] = {}
_oauth_states: dict[str, dict[str, str]] = {}
_event_lock = asyncio.Lock()


async def shutdown() -> None:
    tasks = list(_run_tasks.values())
    for task in tasks:
        task.cancel()
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)
    _run_tasks.clear()
    _oauth_states.clear()


def oauth_start(provider_id: str, owner: str, spec: dict[str, Any]) -> str:
    client_id = (os.environ.get(f"SWARM_{provider_id.upper()}_OAUTH_CLIENT_ID") or "").strip()
    authorize_url = (spec.get("oauth_authorize_url") or "").strip()
    if not client_id or not authorize_url:
        raise ValueError("OAuth is not configured for this provider; connect with an API key")
    verifier = base64.urlsafe_b64encode(secrets.token_bytes(32)).decode().rstrip("=")
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
    state = secrets.token_urlsafe(32)
    _oauth_states[state] = {"provider_id": provider_id, "owner": owner, "verifier": verifier}
    redirect_uri = os.environ.get("SWARM_OAUTH_REDIRECT_URI", "http://localhost:8000/api/v2/oauth/callback")
    params = {"client_id": client_id, "redirect_uri": redirect_uri, "response_type": "code", "state": state, "code_challenge": challenge, "code_challenge_method": "S256", "scope": spec.get("oauth_scope", "")}
    return f"{authorize_url}?{urlencode({k: v for k, v in params.items() if v})}"


async def oauth_callback(state: str, code: str) -> dict[str, Any]:
    context = _oauth_states.pop(state, None)
    if not context:
        raise ValueError("invalid or expired OAuth state")
    provider_id = context["provider_id"]
    from .ai_support.providers import get_provider
    spec = get_provider(provider_id) or {}
    client_id = (os.environ.get(f"SWARM_{provider_id.upper()}_OAUTH_CLIENT_ID") or "").strip()
    client_secret = (os.environ.get(f"SWARM_{provider_id.upper()}_OAUTH_CLIENT_SECRET") or "").strip()
    token_url = (spec.get("oauth_token_url") or "").strip()
    if not client_id or not token_url:
        raise ValueError("OAuth is not configured for this provider")
    import httpx
    redirect_uri = os.environ.get("SWARM_OAUTH_REDIRECT_URI", "http://localhost:8000/api/v2/oauth/callback")
    async with httpx.AsyncClient(timeout=20) as client:
        response = await client.post(token_url, data={"grant_type": "authorization_code", "code": code, "client_id": client_id, "client_secret": client_secret, "redirect_uri": redirect_uri, "code_verifier": context["verifier"]})
        response.raise_for_status()
        body = response.json()
    access_token = str(body.get("access_token") or "").strip()
    if len(access_token) < 8:
        raise ValueError("OAuth provider returned no access token")
    from .ai_support import store
    saved = await store.connect_oauth(provider_id, access_token, body.get("refresh_token"), body.get("expires_in"), model=spec.get("default_model"))
    return {"owner": context["owner"], "provider_id": provider_id, "connected": True, "expires_in": body.get("expires_in"), "connection": saved}


def validate_graph(graph: dict[str, Any]) -> list[str]:
    nodes = graph.get("nodes") or []
    edges = graph.get("edges") or []
    ids = [str(node.get("id") or "") for node in nodes]
    errors: list[str] = []
    if any(not node_id for node_id in ids):
        errors.append("every node needs an id")
    if len(set(ids)) != len(ids):
        errors.append("node ids must be unique")
    known = set(ids)
    adjacency: dict[str, list[str]] = {node_id: [] for node_id in ids}
    for edge in edges:
        if not isinstance(edge, (list, tuple)) or len(edge) != 2 or str(edge[0]) not in known or str(edge[1]) not in known:
            errors.append("edges must reference existing node ids")
            continue
        adjacency[str(edge[0])].append(str(edge[1]))
    visiting: set[str] = set()
    visited: set[str] = set()
    def visit(node_id: str) -> None:
        if node_id in visiting:
            errors.append("workflow graph cannot contain cycles")
            return
        if node_id in visited:
            return
        visiting.add(node_id)
        for child in adjacency.get(node_id, []):
            visit(child)
        visiting.remove(node_id)
        visited.add(node_id)
    for node_id in ids:
        visit(node_id)
    return list(dict.fromkeys(errors))


def order_nodes(nodes: list[dict[str, Any]], edges: list[Any]) -> list[dict[str, Any]]:
    """Stable topological ordering; declaration order breaks ties."""
    by_id = {str(node.get("id")): node for node in nodes if node.get("id")}
    order = {node_id: index for index, node_id in enumerate(by_id)}
    children = {node_id: [] for node_id in by_id}
    indegree = {node_id: 0 for node_id in by_id}
    for edge in edges:
        if isinstance(edge, (list, tuple)) and len(edge) == 2 and str(edge[0]) in by_id and str(edge[1]) in by_id:
            children[str(edge[0])].append(str(edge[1]))
            indegree[str(edge[1])] += 1
    ready = sorted((node_id for node_id, count in indegree.items() if count == 0), key=order.get)
    result: list[dict[str, Any]] = []
    while ready:
        current = ready.pop(0)
        result.append(by_id[current])
        for child in children[current]:
            indegree[child] -= 1
            if indegree[child] == 0:
                ready.append(child)
        ready.sort(key=order.get)
    return result if len(result) == len(by_id) else nodes


SCHEMA = """
CREATE TABLE IF NOT EXISTS workflows (
    id TEXT PRIMARY KEY, owner TEXT NOT NULL, name TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '', graph TEXT NOT NULL DEFAULT '{}',
    created_at REAL NOT NULL, updated_at REAL NOT NULL, archived_at REAL
);
CREATE INDEX IF NOT EXISTS idx_workflows_owner ON workflows(owner, updated_at);
CREATE TABLE IF NOT EXISTS runs (
    id TEXT PRIMARY KEY, workflow_id TEXT, owner TEXT NOT NULL,
    objective TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'draft',
    policy TEXT NOT NULL DEFAULT 'supervised', report TEXT, created_at REAL NOT NULL,
    started_at REAL, finished_at REAL, FOREIGN KEY(workflow_id) REFERENCES workflows(id)
);
CREATE INDEX IF NOT EXISTS idx_runs_owner ON runs(owner, created_at);
CREATE TABLE IF NOT EXISTS run_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT NOT NULL, seq INTEGER NOT NULL,
    event_type TEXT NOT NULL, step_id TEXT, payload TEXT NOT NULL DEFAULT '{}',
    created_at REAL NOT NULL, UNIQUE(run_id, seq), FOREIGN KEY(run_id) REFERENCES runs(id)
);
CREATE INDEX IF NOT EXISTS idx_run_events_run ON run_events(run_id, seq);
CREATE TABLE IF NOT EXISTS run_artifacts (
    id TEXT PRIMARY KEY, run_id TEXT NOT NULL, step_id TEXT, name TEXT NOT NULL,
    uri TEXT NOT NULL, mime_type TEXT NOT NULL DEFAULT 'application/octet-stream',
    metadata TEXT NOT NULL DEFAULT '{}', created_at REAL NOT NULL,
    FOREIGN KEY(run_id) REFERENCES runs(id)
);
"""


async def init_db() -> None:
    async with aiosqlite.connect(db.DB_PATH) as conn:
        await conn.executescript(SCHEMA)
        cur = await conn.execute("PRAGMA table_info(ai_providers)")
        columns = {row[1] for row in await cur.fetchall()}
        if "auth_method" not in columns:
            await conn.execute("ALTER TABLE ai_providers ADD COLUMN auth_method TEXT NOT NULL DEFAULT 'api_key'")
        if "refresh_secret" not in columns:
            await conn.execute("ALTER TABLE ai_providers ADD COLUMN refresh_secret TEXT")
        if "expires_at" not in columns:
            await conn.execute("ALTER TABLE ai_providers ADD COLUMN expires_at REAL")
        cur = await conn.execute("PRAGMA table_info(runs)")
        run_columns = {row[1] for row in await cur.fetchall()}
        if "model" not in run_columns:
            await conn.execute("ALTER TABLE runs ADD COLUMN model TEXT")
        await conn.commit()


def _row(row: aiosqlite.Row | None) -> dict[str, Any] | None:
    return dict(row) if row else None


def _decode(value: str | None, fallback: Any) -> Any:
    try:
        return json.loads(value or "")
    except (TypeError, json.JSONDecodeError):
        return fallback


async def list_workflows(owner: str) -> list[dict[str, Any]]:
    async with aiosqlite.connect(db.DB_PATH) as conn:
        conn.row_factory = aiosqlite.Row
        cur = await conn.execute(
            "SELECT * FROM workflows WHERE owner = ? AND archived_at IS NULL ORDER BY updated_at DESC",
            (owner,),
        )
        rows = [dict(r) for r in await cur.fetchall()]
    for row in rows:
        row["graph"] = _decode(row.get("graph"), {})
    return rows


async def create_workflow(owner: str, name: str, description: str, graph: dict[str, Any]) -> dict[str, Any]:
    now = time.time()
    workflow_id = f"wf_{uuid.uuid4().hex[:12]}"
    async with aiosqlite.connect(db.DB_PATH) as conn:
        await conn.execute(
            "INSERT INTO workflows(id, owner, name, description, graph, created_at, updated_at) VALUES(?,?,?,?,?,?,?)",
            (workflow_id, owner, name, description, json.dumps(graph), now, now),
        )
        await conn.commit()
    return {"id": workflow_id, "owner": owner, "name": name, "description": description, "graph": graph, "created_at": now, "updated_at": now}


async def get_workflow(workflow_id: str, owner: str) -> dict[str, Any] | None:
    async with aiosqlite.connect(db.DB_PATH) as conn:
        conn.row_factory = aiosqlite.Row
        cur = await conn.execute("SELECT * FROM workflows WHERE id = ? AND owner = ? AND archived_at IS NULL", (workflow_id, owner))
        row = _row(await cur.fetchone())
    if row:
        row["graph"] = _decode(row.get("graph"), {})
    return row


async def update_workflow(workflow_id: str, owner: str, patch: dict[str, Any]) -> dict[str, Any] | None:
    current = await get_workflow(workflow_id, owner)
    if not current:
        return None
    name = str(patch.get("name", current["name"]))
    description = str(patch.get("description", current["description"]))
    graph = patch.get("graph", current["graph"])
    now = time.time()
    async with aiosqlite.connect(db.DB_PATH) as conn:
        await conn.execute("UPDATE workflows SET name=?, description=?, graph=?, updated_at=? WHERE id=? AND owner=?", (name, description, json.dumps(graph), now, workflow_id, owner))
        await conn.commit()
    current.update(name=name, description=description, graph=graph, updated_at=now)
    return current


async def create_run(
    owner: str,
    objective: str,
    workflow_id: str | None,
    policy: str = "supervised",
    model: str | None = None,
) -> dict[str, Any]:
    run_id = f"run_{uuid.uuid4().hex[:12]}"
    now = time.time()
    async with aiosqlite.connect(db.DB_PATH) as conn:
        await conn.execute(
            "INSERT INTO runs(id, workflow_id, owner, objective, status, policy, model, created_at) VALUES(?,?,?,?,?,?,?,?)",
            (run_id, workflow_id, owner, objective, "queued", policy, model, now),
        )
        await conn.commit()
    await append_event(run_id, "run_queued", {"objective": objective, "model": model})
    return {
        "id": run_id,
        "workflow_id": workflow_id,
        "owner": owner,
        "objective": objective,
        "status": "queued",
        "policy": policy,
        "model": model,
        "created_at": now,
    }


async def list_runs(owner: str, limit: int = 50) -> list[dict[str, Any]]:
    async with aiosqlite.connect(db.DB_PATH) as conn:
        conn.row_factory = aiosqlite.Row
        cur = await conn.execute("SELECT * FROM runs WHERE owner=? ORDER BY created_at DESC LIMIT ?", (owner, min(max(limit, 1), 100)))
        rows = [dict(r) for r in await cur.fetchall()]
    for row in rows:
        row["report"] = _decode(row.get("report"), None)
    return rows


async def recoverable_runs() -> list[dict[str, Any]]:
    async with aiosqlite.connect(db.DB_PATH) as conn:
        conn.row_factory = aiosqlite.Row
        cur = await conn.execute("SELECT * FROM runs WHERE status IN ('queued', 'running') ORDER BY created_at")
        return [dict(row) for row in await cur.fetchall()]


async def get_run(run_id: str, owner: str) -> dict[str, Any] | None:
    async with aiosqlite.connect(db.DB_PATH) as conn:
        conn.row_factory = aiosqlite.Row
        cur = await conn.execute("SELECT * FROM runs WHERE id=? AND owner=?", (run_id, owner))
        row = _row(await cur.fetchone())
    if row:
        row["report"] = _decode(row.get("report"), None)
    return row


async def get_run_any_owner(run_id: str) -> dict[str, Any] | None:
    """Owner-blind run lookup for startup recovery only (never exposed via API)."""
    async with aiosqlite.connect(db.DB_PATH) as conn:
        conn.row_factory = aiosqlite.Row
        cur = await conn.execute("SELECT * FROM runs WHERE id=?", (run_id,))
        row = _row(await cur.fetchone())
    if row:
        row["report"] = _decode(row.get("report"), None)
    return row


async def update_run(run_id: str, owner: str, status: str, *, report: dict[str, Any] | None = None) -> dict[str, Any] | None:
    now = time.time()
    started = now if status == "running" else None
    finished = now if status in {"completed", "failed", "cancelled"} else None
    async with aiosqlite.connect(db.DB_PATH) as conn:
        if report is None:
            cur = await conn.execute("UPDATE runs SET status=?, started_at=COALESCE(started_at, ?), finished_at=COALESCE(?, finished_at) WHERE id=? AND owner=?", (status, started, finished, run_id, owner))
        else:
            cur = await conn.execute("UPDATE runs SET status=?, report=?, started_at=COALESCE(started_at, ?), finished_at=? WHERE id=? AND owner=?", (status, json.dumps(report), started, finished or now, run_id, owner))
        await conn.commit()
    return await get_run(run_id, owner) if cur.rowcount else None


async def claim_run(run_id: str, owner: str) -> bool:
    """Atomically claim queued/running work for one executor."""
    async with aiosqlite.connect(db.DB_PATH) as conn:
        cur = await conn.execute("UPDATE runs SET status='running', started_at=COALESCE(started_at, ?) WHERE id=? AND owner=? AND status IN ('queued', 'running', 'waiting_for_approval')", (time.time(), run_id, owner))
        await conn.commit()
        return cur.rowcount > 0


async def append_event(run_id: str, event_type: str, payload: dict[str, Any] | None = None, step_id: str | None = None) -> dict[str, Any]:
    async with _event_lock:
        async with aiosqlite.connect(db.DB_PATH) as conn:
            cur = await conn.execute("SELECT COALESCE(MAX(seq), 0) + 1 FROM run_events WHERE run_id=?", (run_id,))
            (seq,) = await cur.fetchone()
            now = time.time()
            await conn.execute("INSERT INTO run_events(run_id, seq, event_type, step_id, payload, created_at) VALUES(?,?,?,?,?,?)", (run_id, seq, event_type, step_id, json.dumps(payload or {}), now))
            await conn.commit()
    return {"run_id": run_id, "seq": seq, "event_type": event_type, "step_id": step_id, "payload": payload or {}, "created_at": now}


async def list_events(run_id: str, owner: str, after: int = 0) -> list[dict[str, Any]]:
    if not await get_run(run_id, owner):
        return []
    async with aiosqlite.connect(db.DB_PATH) as conn:
        conn.row_factory = aiosqlite.Row
        cur = await conn.execute("SELECT e.* FROM run_events e JOIN runs r ON r.id=e.run_id WHERE e.run_id=? AND r.owner=? AND e.seq>? ORDER BY e.seq", (run_id, owner, after))
        rows = [dict(r) for r in await cur.fetchall()]
    for row in rows:
        row["payload"] = _decode(row.get("payload"), {})
    return rows


async def create_artifact(run_id: str, owner: str, name: str, content: str, mime_type: str = "text/plain", step_id: str | None = None) -> dict[str, Any] | None:
    if not await get_run(run_id, owner):
        return None
    safe_name = Path(name).name or "artifact.txt"
    from .agent import SANDBOX_DIR
    root = Path(SANDBOX_DIR).resolve() / "runs" / run_id
    root.mkdir(parents=True, exist_ok=True)
    target = (root / safe_name).resolve()
    if root not in target.parents:
        raise ValueError("invalid artifact name")
    target.write_text(content, encoding="utf-8")
    artifact_id = f"art_{uuid.uuid4().hex[:12]}"
    now = time.time()
    async with aiosqlite.connect(db.DB_PATH) as conn:
        await conn.execute("INSERT INTO run_artifacts(id, run_id, step_id, name, uri, mime_type, metadata, created_at) VALUES(?,?,?,?,?,?,?,?)", (artifact_id, run_id, step_id, safe_name, str(target), mime_type, json.dumps({"size": target.stat().st_size}), now))
        await conn.commit()
    return {"id": artifact_id, "run_id": run_id, "step_id": step_id, "name": safe_name, "uri": str(target), "mime_type": mime_type, "created_at": now}


async def list_artifacts(run_id: str, owner: str) -> list[dict[str, Any]]:
    if not await get_run(run_id, owner):
        return []
    async with aiosqlite.connect(db.DB_PATH) as conn:
        conn.row_factory = aiosqlite.Row
        cur = await conn.execute("SELECT id, run_id, step_id, name, uri, mime_type, metadata, created_at FROM run_artifacts WHERE run_id=? ORDER BY created_at", (run_id,))
        rows = [dict(row) for row in await cur.fetchall()]
    for row in rows:
        row["metadata"] = _decode(row.get("metadata"), {})
    return rows


async def get_artifact(artifact_id: str, owner: str) -> dict[str, Any] | None:
    async with aiosqlite.connect(db.DB_PATH) as conn:
        conn.row_factory = aiosqlite.Row
        cur = await conn.execute("SELECT a.* FROM run_artifacts a JOIN runs r ON r.id=a.run_id WHERE a.id=? AND r.owner=?", (artifact_id, owner))
        row = _row(await cur.fetchone())
    if row:
        row["metadata"] = _decode(row.get("metadata"), {})
    return row


async def execute_run(run_id: str, owner: str) -> None:
    """Execute a dependency-ordered workflow graph with durable events."""
    run = await get_run(run_id, owner)
    if not run:
        return
    workflow = await get_workflow(run.get("workflow_id"), owner) if run.get("workflow_id") else None
    graph = (workflow or {}).get("graph") or {}
    nodes = order_nodes(graph.get("nodes") or [], graph.get("edges") or [])
    report: dict[str, Any] = {"objective": run["objective"], "summary": "", "steps": [], "artifacts": [], "decisions": []}
    try:
        if not await claim_run(run_id, owner):
            return
        prior_events = await list_events(run_id, owner)
        completed_steps = {e.get("step_id") for e in prior_events if e.get("event_type") == "step_completed"}
        approved_steps = {e.get("step_id") for e in prior_events if e.get("event_type") == "approval_resolved" and (e.get("payload") or {}).get("decision") == "approved"}
        await update_run(run_id, owner, "running")
        await append_event(run_id, "run_started", {"objective": run["objective"]})
        if not nodes:
            nodes = [{"id": "lead", "type": "agent", "label": "Lead agent", "agent": "swarm"}]

        async def run_agent_node(node: dict[str, Any]) -> str:
            if node.get("type") == "agent":
                from . import agent as legacy_agent
                row = await db.fetch_agent(str(node.get("agent") or "swarm"))
                if row:
                    model_override = node.get("model") or run.get("model")
                    result = await legacy_agent.generate_reply(
                        row,
                        f"run-{run_id}",
                        [{"author": owner, "author_kind": "human", "body": run["objective"]}],
                        model_override=str(model_override) if model_override else None,
                    )
                    return result.get("reply") or ""
                return f"Agent {node.get('agent') or 'swarm'} is not configured."
            return f"Completed {node.get('label') or node.get('type') or 'step'}."

        for node in nodes:
            if node.get("type") in {"input", "output"}:
                continue
            if node.get("type") == "conditional":
                condition = str(node.get("when") or "always")
                should_run = condition == "always" or (condition == "has_output" and bool(report["steps"]))
                if not should_run:
                    await append_event(run_id, "step_skipped", {"label": node.get("label") or "conditional", "condition": condition}, str(node.get("id") or "condition"))
                    continue
            if node.get("type") == "parallel":
                children = [child for child in (node.get("children") or []) if isinstance(child, dict)]
                async def run_parallel_child(child: dict[str, Any]) -> dict[str, Any]:
                    child_id = str(child.get("id") or uuid.uuid4().hex[:8])
                    child_label = str(child.get("label") or child.get("agent") or "parallel step")
                    await append_event(run_id, "step_started", {"label": child_label, "parallel": True}, child_id)
                    output = await run_agent_node(child)
                    await append_event(run_id, "step_completed", {"label": child_label, "output": output[:4000], "parallel": True}, child_id)
                    return {"id": child_id, "label": child_label, "output": output}
                parallel_results = await asyncio.gather(*(run_parallel_child(child) for child in children))
                report["steps"].extend(parallel_results)
                if parallel_results:
                    report["summary"] = parallel_results[-1]["output"][:1000]
                continue
            step_id = str(node.get("id") or uuid.uuid4().hex[:8])
            label = str(node.get("label") or node.get("agent") or node.get("type") or "step")
            if step_id in completed_steps or (node.get("type") == "approval" and step_id in approved_steps):
                continue
            await append_event(run_id, "step_started", {"label": label}, step_id)
            if node.get("type") == "approval":
                await update_run(run_id, owner, "waiting_for_approval")
                await append_event(run_id, "approval_requested", {"label": label, "reason": "Workflow checkpoint requires review"}, step_id)
                return
            output = await run_agent_node(node)
            report["steps"].append({"id": step_id, "label": label, "output": output})
            if output:
                report["summary"] = output[:1000]
            await append_event(run_id, "step_completed", {"label": label, "output": output[:4000]}, step_id)
        report_artifact = await create_artifact(run_id, owner, "run-report.json", json.dumps(report, indent=2), "application/json")
        if report_artifact:
            report["artifacts"].append({"id": report_artifact["id"], "name": report_artifact["name"], "mime_type": report_artifact["mime_type"]})
        await update_run(run_id, owner, "completed", report=report)
        await append_event(run_id, "run_completed", {"summary": report["summary"], "step_count": len(report["steps"])})
    except asyncio.CancelledError:
        await update_run(run_id, owner, "cancelled")
        await append_event(run_id, "run_cancelled", {})
        raise
    except Exception as exc:  # noqa: BLE001
        await update_run(run_id, owner, "failed", report={**report, "error": str(exc)[:500]})
        await append_event(run_id, "run_failed", {"error": str(exc)[:500]})
    finally:
        _run_tasks.pop(run_id, None)


def start_run(run_id: str, owner: str) -> None:
    if run_id not in _run_tasks:
        _run_tasks[run_id] = asyncio.create_task(execute_run(run_id, owner), name=f"v2-run-{run_id}")


async def cancel_run(run_id: str, owner: str) -> bool:
    run = await get_run(run_id, owner)
    task = _run_tasks.get(run_id)
    if not run or not task:
        return False
    task.cancel()
    return True
