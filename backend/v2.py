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
import logging
import secrets
from urllib.parse import urlencode
from dataclasses import dataclass
from contextlib import suppress
from typing import Any
from pathlib import Path

import aiosqlite

from . import db
from .db import open_db as _open_db
from .settings import get_settings
from .work_state import event_lock

_DEFAULT_OAUTH_REDIRECT = "http://localhost:8000/api/v2/oauth/callback"


def _oauth_redirect_uri() -> str:
    """Redirect URI via the one settings object; blank falls back to default."""
    return (get_settings().oauth_redirect_uri or "").strip() or _DEFAULT_OAUTH_REDIRECT

_logger = logging.getLogger("swarm.v2")

OAUTH_STATE_TTL_SECONDS = 600

# Run ownership is a renewable lease with a fencing token. Every successful
# claim bumps lease_generation; every mutating write by an executor must carry
# the generation it claimed with. A lease grants execution ownership only —
# it is NOT approval authority. Runs parked at a gate are deliberately not
# claimable (see claim_run) until an exact-bound approval grant exists.
RUN_LEASE_SECONDS = 300.0
LEASE_RENEW_DIVISOR = 3.0

_run_tasks: dict[str, asyncio.Task] = {}
_oauth_states: dict[str, dict[str, Any]] = {}


@dataclass(frozen=True)
class RunClaim:
    """Fenced ownership token returned by a successful :func:`claim_run`."""

    run_id: str
    owner: str
    worker_id: str
    generation: int
    lease_expires_at: float
    lease_seconds: float


class StaleRunClaim(RuntimeError):
    """A fenced write was attempted by a lease that has been taken over."""


class ApprovalBindingError(RuntimeError):
    """A decision does not match the exact gate it was shown for."""


class ApprovalAmbiguous(RuntimeError):
    """Grant and run state disagree after a crash; never replay automatically."""


def _sweep_oauth_states(now: float | None = None) -> None:
    """Drop expired OAuth states so the in-memory map can't grow or replay."""
    now = time.time() if now is None else now
    expired = [s for s, ctx in _oauth_states.items()
               if float(ctx.get("expires_at") or 0) <= now]
    for state in expired:
        _oauth_states.pop(state, None)


async def shutdown() -> None:
    tasks = list(_run_tasks.values())
    for task in tasks:
        task.cancel()
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)
    _run_tasks.clear()
    _oauth_states.clear()


def oauth_start(provider_id: str, owner: str, spec: dict[str, Any]) -> str:
    from .settings import oauth_client_id as _oauth_client_id

    client_id = _oauth_client_id(provider_id)
    authorize_url = (spec.get("oauth_authorize_url") or "").strip()
    if not client_id or not authorize_url:
        raise ValueError("OAuth is not configured for this provider; connect with an API key")
    verifier = base64.urlsafe_b64encode(secrets.token_bytes(32)).decode().rstrip("=")
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
    state = secrets.token_urlsafe(32)
    _sweep_oauth_states()
    _oauth_states[state] = {
        "provider_id": provider_id, "owner": owner, "verifier": verifier,
        "expires_at": time.time() + OAUTH_STATE_TTL_SECONDS,
    }
    redirect_uri = _oauth_redirect_uri()
    params = {"client_id": client_id, "redirect_uri": redirect_uri, "response_type": "code", "state": state, "code_challenge": challenge, "code_challenge_method": "S256", "scope": spec.get("oauth_scope", "")}
    return f"{authorize_url}?{urlencode({k: v for k, v in params.items() if v})}"


async def oauth_callback(state: str, code: str) -> dict[str, Any]:
    context = _oauth_states.pop(state, None)
    if not context:
        _sweep_oauth_states()
        raise ValueError("invalid or expired OAuth state")
    if float(context.get("expires_at") or 0) <= time.time():
        _sweep_oauth_states()
        raise ValueError("invalid or expired OAuth state")
    provider_id = context["provider_id"]
    from .ai_support.providers import get_provider
    from .settings import oauth_client_id as _oauth_client_id
    from .settings import oauth_client_secret as _oauth_client_secret

    spec = get_provider(provider_id) or {}
    client_id = _oauth_client_id(provider_id)
    client_secret = _oauth_client_secret(provider_id)
    token_url = (spec.get("oauth_token_url") or "").strip()
    if not client_id or not token_url:
        raise ValueError("OAuth is not configured for this provider")
    import httpx
    redirect_uri = _oauth_redirect_uri()
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
    started_at REAL, finished_at REAL,
    lease_owner TEXT, lease_generation INTEGER NOT NULL DEFAULT 0, lease_expires_at REAL,
    FOREIGN KEY(workflow_id) REFERENCES workflows(id)
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
CREATE INDEX IF NOT EXISTS idx_run_artifacts_run ON run_artifacts(run_id);
CREATE TABLE IF NOT EXISTS approval_grants (
    id TEXT PRIMARY KEY, run_id TEXT NOT NULL, owner TEXT NOT NULL,
    step_id TEXT NOT NULL, agent TEXT NOT NULL DEFAULT '',
    scope TEXT NOT NULL DEFAULT 'run', canonical_args TEXT NOT NULL,
    binding_hash TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'pending',
    created_at REAL NOT NULL, expires_at REAL, resolved_at REAL,
    resolved_by TEXT, consumed_at REAL
);
CREATE INDEX IF NOT EXISTS idx_approval_grants_run_step
    ON approval_grants(run_id, step_id, created_at);
"""


async def init_db() -> None:
    async with _open_db() as conn:
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
        # Additive lease columns. Existing rows get generation 0 and no owner,
        # so on upgrade an in-flight run simply becomes reclaimable by recovery
        # (its old in-memory executor is gone with the old process).
        if "lease_owner" not in run_columns:
            await conn.execute("ALTER TABLE runs ADD COLUMN lease_owner TEXT")
        if "lease_generation" not in run_columns:
            await conn.execute("ALTER TABLE runs ADD COLUMN lease_generation INTEGER NOT NULL DEFAULT 0")
        if "lease_expires_at" not in run_columns:
            await conn.execute("ALTER TABLE runs ADD COLUMN lease_expires_at REAL")
        await conn.commit()


def _row(row: aiosqlite.Row | None) -> dict[str, Any] | None:
    return dict(row) if row else None


def _decode(value: str | None, fallback: Any) -> Any:
    try:
        return json.loads(value or "")
    except (TypeError, json.JSONDecodeError):
        return fallback


async def list_workflows(owner: str, limit: int = 200) -> list[dict[str, Any]]:
    async with _open_db() as conn:
        conn.row_factory = aiosqlite.Row
        cur = await conn.execute(
            "SELECT * FROM workflows WHERE owner = ? AND archived_at IS NULL ORDER BY updated_at DESC LIMIT ?",
            (owner, min(max(limit, 1), 500)),
        )
        rows = [dict(r) for r in await cur.fetchall()]
    for row in rows:
        row["graph"] = _decode(row.get("graph"), {})
    return rows


async def create_workflow(owner: str, name: str, description: str, graph: dict[str, Any]) -> dict[str, Any]:
    now = time.time()
    workflow_id = f"wf_{uuid.uuid4().hex[:12]}"
    async with _open_db() as conn:
        await conn.execute(
            "INSERT INTO workflows(id, owner, name, description, graph, created_at, updated_at) VALUES(?,?,?,?,?,?,?)",
            (workflow_id, owner, name, description, json.dumps(graph), now, now),
        )
        await conn.commit()
    return {"id": workflow_id, "owner": owner, "name": name, "description": description, "graph": graph, "created_at": now, "updated_at": now}


async def get_workflow(workflow_id: str, owner: str) -> dict[str, Any] | None:
    async with _open_db() as conn:
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
    async with _open_db() as conn:
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
    async with _open_db() as conn:
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
    async with _open_db() as conn:
        conn.row_factory = aiosqlite.Row
        cur = await conn.execute("SELECT * FROM runs WHERE owner=? ORDER BY created_at DESC LIMIT ?", (owner, min(max(limit, 1), 100)))
        rows = [dict(r) for r in await cur.fetchall()]
    for row in rows:
        row["report"] = _decode(row.get("report"), None)
    return rows


async def recoverable_runs() -> list[dict[str, Any]]:
    """Runs a restarted process may attempt to take over.

    Approval waiters stay listed so they are not lost across a restart, but
    :func:`claim_run` fails closed on them: re-launching a parked gate cannot
    consume an approval or emit a second ``approval_requested``. Only a
    ``running`` run whose lease is gone or expired is otherwise reclaimable.
    """
    async with _open_db() as conn:
        conn.row_factory = aiosqlite.Row
        cur = await conn.execute(
            "SELECT id, owner FROM runs "
            "WHERE status IN ('queued', 'waiting_for_approval') "
            "OR (status='running' AND "
            "    (lease_expires_at IS NULL OR lease_expires_at <= ?)) "
            "ORDER BY created_at", (time.time(),))
        return [dict(row) for row in await cur.fetchall()]


async def get_run(run_id: str, owner: str) -> dict[str, Any] | None:
    async with _open_db() as conn:
        conn.row_factory = aiosqlite.Row
        cur = await conn.execute("SELECT * FROM runs WHERE id=? AND owner=?", (run_id, owner))
        row = _row(await cur.fetchone())
    if row:
        row["report"] = _decode(row.get("report"), None)
    return row


async def get_run_status_any_owner(run_id: str) -> dict[str, Any] | None:
    """Owner-blind status lookup for startup recovery only (never exposed via API)."""
    async with _open_db() as conn:
        conn.row_factory = aiosqlite.Row
        cur = await conn.execute("SELECT id, status FROM runs WHERE id=?", (run_id,))
        return _row(await cur.fetchone())


async def get_run_any_owner(run_id: str) -> dict[str, Any] | None:
    """Owner-blind full run lookup for the admin-only reconciliation route.

    This is deliberately not a general API helper: the only caller resolves
    the real owner first, then passes it to the owner-scoped reconcile logic.
    """
    async with _open_db() as conn:
        conn.row_factory = aiosqlite.Row
        cur = await conn.execute("SELECT * FROM runs WHERE id=?", (run_id,))
        row = await cur.fetchone()
    if row is None:
        return None
    out = dict(row)
    out["report"] = _decode(out.get("report"), None)
    return out


async def update_run(
    run_id: str, owner: str, status: str, *,
    report: dict[str, Any] | None = None,
    claim: RunClaim | None = None,
) -> dict[str, Any] | None:
    """Set a run's status; when ``claim`` is supplied, fence on its generation.

    A stale executor (whose lease was taken over) gets ``None`` and no write.
    Callers that are not executors — the approval endpoint, for example — pass
    no claim and keep the old behaviour.
    """
    now = time.time()
    started = now if status == "running" else None
    finished = now if status in {"completed", "failed", "cancelled"} else None
    fence = ""
    extra: list[Any] = []
    if claim is not None:
        fence = " AND lease_owner=? AND lease_generation=?"
        extra = [claim.worker_id, claim.generation]
    async with _open_db() as conn:
        if report is None:
            cur = await conn.execute(
                "UPDATE runs SET status=?, started_at=COALESCE(started_at, ?),"
                " finished_at=COALESCE(?, finished_at) WHERE id=? AND owner=?" + fence,
                (status, started, finished, run_id, owner, *extra),
            )
        else:
            cur = await conn.execute(
                "UPDATE runs SET status=?, report=?, started_at=COALESCE(started_at, ?),"
                " finished_at=COALESCE(?, finished_at) WHERE id=? AND owner=?" + fence,
                (status, json.dumps(report), started, finished, run_id, owner, *extra),
            )
        await conn.commit()
    return await get_run(run_id, owner) if cur.rowcount else None


async def _parked_step_id(run_id: str, owner: str) -> str | None:
    """Step of the grant the run is actually parked at.

    Consuming the newest grant for the whole run would let one gate's decision
    authorise a different gate if two grants ever coexist.
    """
    async with _open_db() as conn:
        cur = await conn.execute(
            "SELECT step_id FROM approval_grants WHERE run_id=? AND owner=? "
            "AND status IN ('pending','granted','consumed') "
            "ORDER BY created_at DESC LIMIT 1", (run_id, owner))
        row = await cur.fetchone()
    return str(row[0]) if row else None


async def claim_run(
    run_id: str, owner: str, *,
    worker_id: str,
    lease_seconds: float | None = None,
) -> RunClaim | None:
    """Atomically claim one run for one executor and return a fencing token.

    Eligible: unclaimed ``queued`` work, ``running`` work whose lease is absent
    or expired, and ``waiting_for_approval`` work with an exact-bound, granted
    approval. A gate is unparked only by consuming that grant in the SAME
    transaction as the lease UPDATE. An event, a path parameter or a restart
    is never a substitute for a human decision.
    """
    run = await get_run(run_id, owner)
    if not run:
        return None
    seconds = RUN_LEASE_SECONDS if lease_seconds is None else lease_seconds
    if run["status"] == "waiting_for_approval":
        step_id = await _parked_step_id(run_id, owner)
        if step_id is None:
            return None
        return await _consume_grant_for_claim(run, worker_id, seconds, step_id=step_id)
    now = time.time()
    expires = now + max(seconds, 0.01)
    async with _open_db() as conn:
        cur = await conn.execute(
            "UPDATE runs SET status='running', lease_owner=?, lease_expires_at=?,"
            " lease_generation=lease_generation+1, started_at=COALESCE(started_at, ?)"
            " WHERE id=? AND owner=? AND (status='queued'"
            "   OR (status='running' AND"
            "       (lease_expires_at IS NULL OR lease_expires_at <= ?)))",
            (worker_id, expires, now, run_id, owner, now),
        )
        if not cur.rowcount:
            return None
        cur = await conn.execute(
            "SELECT lease_generation, lease_expires_at FROM runs WHERE id=?", (run_id,))
        generation, lease_expiry = await cur.fetchone()
        await conn.commit()
    return RunClaim(run_id, owner, worker_id, int(generation), float(lease_expiry), seconds)


async def renew_run_lease(
    claim: RunClaim, *, lease_seconds: float | None = None
) -> bool:
    """Extend the lease only while this executor still owns that generation."""
    seconds = RUN_LEASE_SECONDS if lease_seconds is None else lease_seconds
    expires = time.time() + max(seconds, 0.01)
    async with _open_db() as conn:
        cur = await conn.execute(
            "UPDATE runs SET lease_expires_at=? WHERE id=? AND owner=?"
            " AND lease_owner=? AND lease_generation=? AND status='running'",
            (expires, claim.run_id, claim.owner, claim.worker_id, claim.generation),
        )
        await conn.commit()
        return cur.rowcount > 0


async def release_run_lease(claim: RunClaim) -> None:
    """Clear ownership on a clean stop. Best effort; the generation stays."""
    async with _open_db() as conn:
        await conn.execute(
            "UPDATE runs SET lease_owner=NULL, lease_expires_at=NULL"
            " WHERE id=? AND owner=? AND lease_owner=? AND lease_generation=?",
            (claim.run_id, claim.owner, claim.worker_id, claim.generation),
        )
        await conn.commit()


async def append_event(
    run_id: str, event_type: str, payload: dict[str, Any] | None = None,
    step_id: str | None = None, *,
    claim: RunClaim | None = None,
) -> dict[str, Any]:
    """Append a durable event. An executor's claim fences out stale writers.

    Non-executor callers (run creation, the approval endpoint) pass no claim
    and keep the old behaviour. A stale executor raises :class:`StaleRunClaim`
    instead of writing an event that would make its actions look durable.
    """
    async with event_lock():
        async with _open_db() as conn:
            if claim is not None:
                cur = await conn.execute(
                    "SELECT 1 FROM runs WHERE id=? AND owner=? AND lease_owner=?"
                    " AND lease_generation=?",
                    (claim.run_id, claim.owner, claim.worker_id, claim.generation),
                )
                if await cur.fetchone() is None:
                    raise StaleRunClaim(
                        f"run {run_id} lease changed; event {event_type} not appended")
            cur = await conn.execute("SELECT COALESCE(MAX(seq), 0) + 1 FROM run_events WHERE run_id=?", (run_id,))
            (seq,) = await cur.fetchone()
            now = time.time()
            await conn.execute("INSERT INTO run_events(run_id, seq, event_type, step_id, payload, created_at) VALUES(?,?,?,?,?,?)", (run_id, seq, event_type, step_id, json.dumps(payload or {}), now))
            await conn.commit()
    return {"run_id": run_id, "seq": seq, "event_type": event_type, "step_id": step_id, "payload": payload or {}, "created_at": now}


async def list_events(run_id: str, owner: str, after: int = 0, limit: int = 2000) -> list[dict[str, Any]]:
    # The event query's owner join is the authorization check; avoid loading
    # and decoding the run report just to establish that the owner matches.
    async with _open_db() as conn:
        conn.row_factory = aiosqlite.Row
        cur = await conn.execute("SELECT e.* FROM run_events e JOIN runs r ON r.id=e.run_id WHERE e.run_id=? AND r.owner=? AND e.seq>? ORDER BY e.seq LIMIT ?", (run_id, owner, after, min(max(limit, 1), 5000)))
        rows = [dict(r) for r in await cur.fetchall()]
    for row in rows:
        row["payload"] = _decode(row.get("payload"), {})
    return rows


# ------------------------------------------------- exact-bound approval grants
# A lease grants execution ownership, never approval authority. A grant is the
# only thing that may unpark a gate. It is bound to the exact run, gate step,
# requesting agent, scope and canonical arguments, and is consumed atomically
# with the lease claim. An event, a path parameter or a restart is not a grant.

APPROVAL_GRANT_TTL_SECONDS = 24 * 60 * 60


def _gate_binding(
    run: dict[str, Any], workflow: dict[str, Any] | None, step_id: str,
    *, nonce: str = "",
) -> tuple[dict[str, Any], str] | None:
    """Canonical descriptor for a gate, derived from the run's stored workflow.

    Recomputed at both request and consume time. An approval node is a bare
    checkpoint: it authorises resuming this workflow, NOT a specific tool or
    command. The descriptor says so explicitly so the UI cannot overstate what
    the human approved. A later workflow edit invalidates the binding and
    requires explicit re-approval — it must never silently authorise new work.
    """
    graph_raw = (workflow or {}).get("graph")
    # Callers pass both freshly created (dict) and stored (JSON string) graphs.
    graph = graph_raw if isinstance(graph_raw, dict) else (_decode(graph_raw, {}) or {})
    nodes = order_nodes(graph.get("nodes") or [], graph.get("edges") or [])
    for node in nodes:
        if str(node.get("id") or "") != step_id or node.get("type") != "approval":
            continue
        canonical = {
            "run_id": str(run["id"]),
            "step_id": step_id,
            "label": str(node.get("label") or "Approval"),
            "agent": str(node.get("agent") or ""),
            "scope": str(node.get("scope") or "run"),
            "reason": "Workflow checkpoint requires review",
            "objective": str(run.get("objective") or ""),
            "authority": (
                "Approving resumes this workflow past the gate; no specific "
                "tool or command is bound to this decision."
            ),
        }
        raw = json.dumps(canonical, sort_keys=True, separators=(",", ":"))
        # The nonce is the grant id: a re-issued gate with an identical
        # descriptor must still get a different binding hash, or an old
        # decision could silently authorise the fresh grant.
        hashed = f"{raw}|{nonce}".encode("utf-8")
        return canonical, hashlib.sha256(hashed).hexdigest()
    return None


async def _workflow_for_run_in_conn(conn: Any, run: dict[str, Any]) -> dict[str, Any] | None:
    workflow_id = run.get("workflow_id")
    if not workflow_id:
        return None
    cur = await conn.execute(
        "SELECT id, owner, name, description, graph, created_at, updated_at, archived_at "
        "FROM workflows WHERE id=? AND owner=?", (workflow_id, run["owner"]))
    row = await cur.fetchone()
    return dict(row) if row else None


async def park_at_approval(
    run_id: str, owner: str, step_id: str, *, claim: RunClaim,
    report: dict[str, Any], workflow: dict[str, Any] | None,
) -> dict[str, Any]:
    """Park a run and create its single, exact-bound approval grant.

    Status, grant and audit event are written in one transaction so a crash
    cannot leave a gate parked without a grant or a grant without a gate.
    """
    run = await get_run(run_id, owner)
    if run is None:
        raise StaleRunClaim("run disappeared while parking a gate")
    grant_id = f"agr_{uuid.uuid4().hex[:12]}"
    binding = _gate_binding(run, workflow, step_id, nonce=grant_id)
    if binding is None:
        raise ApprovalBindingError(f"no approval node for step {step_id}")
    canonical, binding_hash = binding
    now = time.time()
    async with event_lock():
        async with _open_db() as conn:
            await conn.execute("BEGIN IMMEDIATE")
            cur = await conn.execute(
                "SELECT 1 FROM runs WHERE id=? AND owner=? AND lease_owner=? AND lease_generation=?",
                (run_id, owner, claim.worker_id, claim.generation))
            if await cur.fetchone() is None:
                await conn.rollback()
                raise StaleRunClaim("lease changed while parking a gate")
            cur = await conn.execute(
                "SELECT id, status FROM approval_grants WHERE run_id=? AND owner=? AND step_id=? "
                "ORDER BY created_at DESC LIMIT 1", (run_id, owner, step_id))
            prior = await cur.fetchone()
            if prior is not None and prior[1] in {"pending", "granted"}:
                await conn.rollback()
                raise ApprovalBindingError("gate already has an active approval grant")
            await conn.execute(
                "INSERT INTO approval_grants(id, run_id, owner, step_id, agent, scope,"
                " canonical_args, binding_hash, status, created_at, expires_at)"
                " VALUES(?,?,?,?,?,?,?,?, 'pending', ?, ?)",
                (grant_id, run_id, owner, step_id, canonical["agent"], canonical["scope"],
                 json.dumps(canonical, sort_keys=True, separators=(",", ":")),
                 binding_hash, now, now + APPROVAL_GRANT_TTL_SECONDS))
            cur = await conn.execute(
                "UPDATE runs SET status='waiting_for_approval', report=?"
                " WHERE id=? AND owner=? AND lease_owner=? AND lease_generation=?"
                " AND status='running'",
                (json.dumps(report), run_id, owner, claim.worker_id, claim.generation))
            if not cur.rowcount:
                await conn.rollback()
                raise StaleRunClaim("run left running state while parking a gate")
            cur = await conn.execute("SELECT COALESCE(MAX(seq), 0) + 1 FROM run_events WHERE run_id=?", (run_id,))
            (seq,) = await cur.fetchone()
            payload = {
                "label": canonical["label"], "reason": canonical["reason"],
                "agent": canonical["agent"], "scope": canonical["scope"],
                "canonical_args": canonical, "binding_hash": binding_hash,
                "grant_id": grant_id,
            }
            await conn.execute(
                "INSERT INTO run_events(run_id, seq, event_type, step_id, payload, created_at)"
                " VALUES(?,?, 'approval_requested', ?, ?, ?)",
                (run_id, seq, step_id, json.dumps(payload), now))
            await conn.commit()
    return {"grant_id": grant_id, "binding_hash": binding_hash, "canonical_args": canonical, "seq": seq}


async def get_approval_grant(
    run_id: str, owner: str, step_id: str,
) -> dict[str, Any] | None:
    async with _open_db() as conn:
        conn.row_factory = aiosqlite.Row
        cur = await conn.execute(
            "SELECT * FROM approval_grants WHERE run_id=? AND owner=? AND step_id=? "
            "ORDER BY created_at DESC LIMIT 1", (run_id, owner, step_id))
        row = await cur.fetchone()
    if row is None:
        return None
    out = dict(row)
    out["canonical_args"] = _decode(out.get("canonical_args"), {})
    return out


async def consumed_gate_steps(run_id: str, owner: str) -> set[str]:
    """Steps whose exact-bound grant was consumed by a live claim."""
    async with _open_db() as conn:
        cur = await conn.execute(
            "SELECT DISTINCT step_id FROM approval_grants "
            "WHERE run_id=? AND owner=? AND status='consumed'", (run_id, owner))
        return {str(row[0]) for row in await cur.fetchall()}


async def resolve_approval_grant(
    run_id: str, owner: str, step_id: str, decision: str, *,
    resolved_by: str, grant_id: str | None = None,
    binding_hash: str | None = None,
) -> dict[str, Any] | None:
    """Atomically record one human decision on one exact-bound grant."""
    if decision not in {"approved", "denied"}:
        raise ValueError("decision must be approved or denied")
    now = time.time()
    async with _open_db() as conn:
        conn.row_factory = aiosqlite.Row
        await conn.execute("BEGIN IMMEDIATE")
        cur = await conn.execute(
            "SELECT * FROM approval_grants WHERE run_id=? AND owner=? AND step_id=? "
            "ORDER BY created_at DESC LIMIT 1", (run_id, owner, step_id))
        row = await cur.fetchone()
        if row is None:
            await conn.rollback()
            return None
        grant = dict(row)
        stored_hash = grant.get("binding_hash")
        if grant_id is not None and grant["id"] != grant_id:
            await conn.rollback()
            raise ApprovalBindingError("approval grant id does not match this decision")
        if binding_hash is not None and stored_hash != binding_hash:
            await conn.rollback()
            raise ApprovalBindingError("approval arguments changed since the decision was shown")
        status = grant.get("status")
        if status in {"granted", "denied", "consumed"}:
            await conn.rollback()
            return {"state": "already", "grant": grant}
        expiry = grant.get("expires_at")
        if expiry is not None and float(expiry) <= now:
            await conn.execute(
                "UPDATE approval_grants SET status='expired' WHERE id=? AND status='pending'",
                (grant["id"],))
            await conn.commit()
            grant["status"] = "expired"
            return {"state": "expired", "grant": grant}
        cur = await conn.execute(
            "UPDATE approval_grants SET status=?, resolved_by=?, resolved_at=? "
            "WHERE id=? AND status='pending'",
            ("granted" if decision == "approved" else "denied", resolved_by, now, grant["id"]))
        if not cur.rowcount:
            await conn.rollback()
            return {"state": "already", "grant": grant}
        await conn.commit()
    grant["status"] = "granted" if decision == "approved" else "denied"
    grant["resolved_by"] = resolved_by
    grant["resolved_at"] = now
    return {"state": "resolved", "grant": grant}


def _grant_view(
    grant: dict[str, Any], run_status: str, now: float | None = None,
    *, binding_current: bool | None = None,
) -> dict[str, Any]:
    """Derive an honest, UI-safe state from the durable grant and run status."""
    now = time.time() if now is None else now
    state = str(grant.get("status") or "unknown")
    expiry = grant.get("expires_at")
    expired = expiry is not None and float(expiry) <= now
    if state in {"pending", "granted"} and expired:
        state = "expired"
    # A grant whose workflow changed after parking cannot be consumed. Report
    # it as stale with a recovery action instead of leaving a dead end.
    if state in {"pending", "granted"} and not expired and binding_current is False:
        state = "stale"
    # A consumed grant on a still-parked run is the crash-ambiguity signature.
    if state == "consumed" and run_status == "waiting_for_approval":
        state = "ambiguous"
    canonical = _decode(grant.get("canonical_args"), {})
    return {
        "state": state,
        "grant_id": grant.get("id"),
        "step_id": grant.get("step_id"),
        "run_status": run_status,
        "binding_hash": grant.get("binding_hash"),
        "canonical_args": canonical,
        "expires_at": expiry,
        "resolved_by": grant.get("resolved_by"),
        "resolved_at": grant.get("resolved_at"),
        "can_reissue": state in {"expired", "stale"},
        "authority": canonical.get("authority") or (
            "Approving resumes this workflow past the gate; no specific tool "
            "or command is bound to this decision."
        ),
    }


async def approval_state(run_id: str, owner: str) -> dict[str, Any] | None:
    """Authoritative gate state; never inferred from the event stream alone."""
    run = await get_run(run_id, owner)
    if run is None:
        return None
    async with _open_db() as conn:
        conn.row_factory = aiosqlite.Row
        cur = await conn.execute(
            "SELECT * FROM approval_grants WHERE run_id=? AND owner=? "
            "ORDER BY created_at DESC LIMIT 1", (run_id, owner))
        row = await cur.fetchone()
    if row is None:
        return {
            "state": "none", "run_status": run["status"],
            "can_reissue": False,
            "authority": "No approval grant exists for this run.",
        }
    grant = dict(row)
    binding_current: bool | None = None
    if grant.get("status") in {"pending", "granted"}:
        workflow = (
            await get_workflow(run.get("workflow_id"), owner)
            if run.get("workflow_id") else None
        )
        binding = _gate_binding(run, workflow, str(grant["step_id"]), nonce=grant["id"])
        binding_current = bool(binding) and (
            grant.get("binding_hash") == binding[1]
            and grant.get("agent") == binding[0]["agent"]
            and grant.get("scope") == binding[0]["scope"]
        )
    return _grant_view(grant, run["status"], binding_current=binding_current)


async def _new_grant_in_conn(
    conn: Any, run: dict[str, Any], step_id: str, *, tag: str
) -> dict[str, Any]:
    """Insert a fresh grant and its approval_requested event in one transaction."""
    new_id = f"agr_{uuid.uuid4().hex[:12]}"
    binding = _gate_binding(run, run.get("_workflow"), step_id, nonce=new_id)
    if binding is None:
        raise ApprovalBindingError("gate no longer exists in the stored workflow")
    canonical, binding_hash = binding
    now = time.time()
    await conn.execute(
        "INSERT INTO approval_grants(id, run_id, owner, step_id, agent, scope,"
        " canonical_args, binding_hash, status, created_at, expires_at)"
        " VALUES(?,?,?,?,?,?,?,?, 'pending', ?, ?)",
        (new_id, run["id"], run["owner"], step_id, canonical["agent"], canonical["scope"],
         json.dumps(canonical, sort_keys=True, separators=(",", ":")),
         binding_hash, now, now + APPROVAL_GRANT_TTL_SECONDS))
    cur = await conn.execute(
        "SELECT COALESCE(MAX(seq), 0) + 1 FROM run_events WHERE run_id=?", (run["id"],))
    (seq,) = await cur.fetchone()
    payload = {
        "label": canonical["label"], "reason": canonical["reason"],
        "agent": canonical["agent"], "scope": canonical["scope"],
        "canonical_args": canonical, "binding_hash": binding_hash,
        "grant_id": new_id, tag: True,
    }
    await conn.execute(
        "INSERT INTO run_events(run_id, seq, event_type, step_id, payload, created_at)"
        " VALUES(?,?, 'approval_requested', ?, ?, ?)",
        (run["id"], seq, step_id, json.dumps(payload), now))
    return {"grant_id": new_id, "binding_hash": binding_hash, "seq": seq}


async def reissue_approval(run_id: str, owner: str) -> dict[str, Any] | None:
    """Re-request an expired or invalidated gate with a fresh binding.

    Never resumes the run and never reuses the old decision: a new grant with
    a new binding hash requires a new human decision. A consumed-but-parked
    grant is ambiguous and must be reconciled explicitly instead.
    """
    run = await get_run(run_id, owner)
    if run is None or run["status"] != "waiting_for_approval":
        return None
    async with event_lock():
        async with _open_db() as conn:
            conn.row_factory = aiosqlite.Row
            await conn.execute("BEGIN IMMEDIATE")
            cur = await conn.execute(
                "SELECT * FROM approval_grants WHERE run_id=? AND owner=? "
                "ORDER BY created_at DESC LIMIT 1", (run_id, owner))
            row = await cur.fetchone()
            if row is None:
                await conn.rollback()
                return None
            grant = dict(row)
            status = str(grant.get("status"))
            expiry = grant.get("expires_at")
            expired = expiry is not None and float(expiry) <= time.time()
            workflow = await _workflow_for_run_in_conn(conn, run)
            binding = _gate_binding(run, workflow, str(grant["step_id"]), nonce=grant["id"])
            current = bool(binding) and (
                grant.get("binding_hash") == binding[1]
                and grant.get("agent") == binding[0]["agent"]
                and grant.get("scope") == binding[0]["scope"]
            )
            if status == "consumed":
                await conn.rollback()
                raise ApprovalAmbiguous("consumed grant needs reconciliation, not reissue")
            if status in {"pending", "granted"} and not expired and current:
                await conn.rollback()
                return None  # nothing stale: refuse to mint needless grants
            if status not in {"pending", "granted", "expired", "superseded"}:
                await conn.rollback()
                return None
            await conn.execute(
                "UPDATE approval_grants SET status='superseded' WHERE id=?", (grant["id"],))
            run_for_binding = dict(run)
            run_for_binding["_workflow"] = workflow
            await _new_grant_in_conn(conn, run_for_binding, str(grant["step_id"]), tag="reissued")
            await conn.commit()
    return await approval_state(run_id, owner)


async def reconcile_ambiguous(
    run_id: str, owner: str, action: str, *, resolved_by: str
) -> dict[str, Any] | None:
    """Admin-driven resolution of a consumed-but-still-parked grant.

    ``cancel`` ends the run with a durable reason. ``rearm`` supersedes the old
    grant and re-requests the gate with a fresh binding; it never replays the
    old decision or any side effect it may already have triggered.
    """
    if action not in {"cancel", "rearm"}:
        raise ValueError("action must be cancel or rearm")
    run = await get_run(run_id, owner)
    if run is None:
        return None
    state = await approval_state(run_id, owner)
    if state is None or state.get("state") != "ambiguous":
        return None
    grant_id, step_id = state["grant_id"], state["step_id"]
    now = time.time()
    async with event_lock():
        async with _open_db() as conn:
            conn.row_factory = aiosqlite.Row
            await conn.execute("BEGIN IMMEDIATE")
            cur = await conn.execute(
                "SELECT 1 FROM approval_grants WHERE id=? AND run_id=? AND owner=?",
                (grant_id, run_id, owner))
            if await cur.fetchone() is None:
                await conn.rollback()
                return None
            if action == "cancel":
                cur = await conn.execute(
                    "UPDATE runs SET status='cancelled', finished_at=? "
                    "WHERE id=? AND owner=? AND status='waiting_for_approval'",
                    (now, run_id, owner))
                if not cur.rowcount:
                    await conn.rollback()
                    return None
                payload = {"action": "cancel", "grant_id": grant_id,
                           "resolved_by": resolved_by,
                           "detail": "ambiguous approval reconciled by cancelling the run"}
            else:
                await conn.execute(
                    "UPDATE approval_grants SET status='superseded' WHERE id=?", (grant_id,))
                workflow = await _workflow_for_run_in_conn(conn, run)
                run_for_binding = dict(run)
                run_for_binding["_workflow"] = workflow
                fresh = await _new_grant_in_conn(
                    conn, run_for_binding, str(step_id), tag="rearmed")
                payload = {"action": "rearm", "old_grant_id": grant_id,
                           "grant_id": fresh["grant_id"], "resolved_by": resolved_by,
                           "detail": "ambiguous approval re-requested with a fresh binding"}
            cur = await conn.execute(
                "SELECT COALESCE(MAX(seq), 0) + 1 FROM run_events WHERE run_id=?", (run_id,))
            (seq,) = await cur.fetchone()
            await conn.execute(
                "INSERT INTO run_events(run_id, seq, event_type, step_id, payload, created_at)"
                " VALUES(?,?, 'approval_reconciled', ?, ?, ?)",
                (run_id, seq, step_id, json.dumps(payload), now))
            await conn.commit()
    return await approval_state(run_id, owner)


async def _consume_grant_for_claim(
    run: dict[str, Any], worker_id: str, lease_seconds: float, *, step_id: str,
) -> RunClaim | None:
    """Consume a granted, exact-bound approval and take the lease atomically."""
    now = time.time()
    expires = now + max(lease_seconds, 0.01)
    async with _open_db() as conn:
        conn.row_factory = aiosqlite.Row
        await conn.execute("BEGIN IMMEDIATE")
        cur = await conn.execute(
            "SELECT * FROM approval_grants WHERE run_id=? AND owner=? AND step_id=? "
            "ORDER BY created_at DESC LIMIT 1", (run["id"], run["owner"], step_id))
        row = await cur.fetchone()
        if row is None:
            await conn.rollback()
            return None
        grant = dict(row)
        status = grant["status"]
        if status == "consumed":
            cur = await conn.execute(
                "SELECT status FROM runs WHERE id=? AND owner=?",
                (run["id"], run["owner"]))
            current = await cur.fetchone()
            if current is None or str(current[0]) != "waiting_for_approval":
                # The winner already unparked the run. Losing this race is not
                # ambiguity — do not emit a false reconciliation alarm.
                await conn.rollback()
                return None
            await conn.rollback()
            # Crash ambiguity: grant consumed but the run is still parked.
            # Do not replay the gate or its side effects; surface it instead.
            raise ApprovalAmbiguous(
                f"grant {grant['id']} was consumed but run {run['id']} is still parked")
        workflow = await _workflow_for_run_in_conn(conn, run)
        binding = _gate_binding(run, workflow, grant["step_id"], nonce=grant["id"])
        if binding is None:
            await conn.rollback()
            raise ApprovalBindingError("approval gate no longer exists in the stored workflow")
        canonical, binding_hash = binding
        if (grant["binding_hash"] != binding_hash
                or grant["agent"] != canonical["agent"]
                or grant["scope"] != canonical["scope"]):
            await conn.rollback()
            raise ApprovalBindingError("approval binding no longer matches the parked gate")
        if status != "granted":
            await conn.rollback()
            return None
        expiry = grant["expires_at"]
        if expiry is not None and float(expiry) <= now:
            await conn.execute(
                "UPDATE approval_grants SET status='expired' WHERE id=? AND status='granted'",
                (grant["id"],))
            await conn.commit()
            return None
        cur = await conn.execute(
            "UPDATE approval_grants SET status='consumed', consumed_at=? "
            "WHERE id=? AND status='granted'", (now, grant["id"]))
        if not cur.rowcount:
            await conn.rollback()
            return None
        cur = await conn.execute(
            "UPDATE runs SET status='running', lease_owner=?, lease_expires_at=?,"
            " lease_generation=lease_generation+1, started_at=COALESCE(started_at, ?)"
            " WHERE id=? AND owner=? AND status='waiting_for_approval'",
            (worker_id, expires, now, run["id"], run["owner"]))
        if not cur.rowcount:
            await conn.rollback()
            raise ApprovalAmbiguous("run state changed during approval consume")
        cur = await conn.execute(
            "SELECT lease_generation, lease_expires_at FROM runs WHERE id=?", (run["id"],))
        generation, lease_expiry = await cur.fetchone()
        await conn.commit()
    return RunClaim(run["id"], run["owner"], worker_id, int(generation), float(lease_expiry), lease_seconds)


async def create_artifact(
    run_id: str, owner: str, name: str, content: str,
    mime_type: str = "text/plain", step_id: str | None = None,
    *, claim: RunClaim | None = None,
) -> dict[str, Any] | None:
    """Write an artifact, fencing the durable row on the executor's lease.

    A stale executor must not write an artifact at all: the lease fence is
    checked before the file is written, and the row insert shares the same
    transaction. A DB failure after the write can still leave an orphan file,
    which is reported as ambiguous rather than silently replayed.
    """
    if not await get_run(run_id, owner):
        return None
    safe_name = Path(name).name or "artifact.txt"
    from .agent import SANDBOX_DIR
    root = Path(SANDBOX_DIR).resolve() / "runs" / run_id
    root.mkdir(parents=True, exist_ok=True)
    target = (root / safe_name).resolve()
    if root not in target.parents:
        raise ValueError("invalid artifact name")
    artifact_id = f"art_{uuid.uuid4().hex[:12]}"
    now = time.time()
    async with _open_db() as conn:
        await conn.execute("BEGIN IMMEDIATE")
        if claim is not None:
            cur = await conn.execute(
                "SELECT 1 FROM runs WHERE id=? AND owner=? AND lease_owner=? "
                "AND lease_generation=? AND status='running'",
                (run_id, owner, claim.worker_id, claim.generation))
            if await cur.fetchone() is None:
                await conn.rollback()
                return None
        target.write_text(content, encoding="utf-8")
        await conn.execute(
            "INSERT INTO run_artifacts(id, run_id, step_id, name, uri, mime_type, metadata, created_at)"
            " VALUES(?,?,?,?,?,?,?,?)",
            (artifact_id, run_id, step_id, safe_name, str(target), mime_type,
             json.dumps({"size": target.stat().st_size}), now))
        await conn.commit()
    return {"id": artifact_id, "run_id": run_id, "step_id": step_id, "name": safe_name, "uri": str(target), "mime_type": mime_type, "created_at": now}


async def list_artifacts(run_id: str, owner: str) -> list[dict[str, Any]]:
    if not await get_run(run_id, owner):
        return []
    async with _open_db() as conn:
        conn.row_factory = aiosqlite.Row
        cur = await conn.execute("SELECT id, run_id, step_id, name, uri, mime_type, metadata, created_at FROM run_artifacts WHERE run_id=? ORDER BY created_at", (run_id,))
        rows = [dict(row) for row in await cur.fetchall()]
    for row in rows:
        row["metadata"] = _decode(row.get("metadata"), {})
    return rows


async def get_artifact(artifact_id: str, owner: str) -> dict[str, Any] | None:
    async with _open_db() as conn:
        conn.row_factory = aiosqlite.Row
        cur = await conn.execute("SELECT a.* FROM run_artifacts a JOIN runs r ON r.id=a.run_id WHERE a.id=? AND r.owner=?", (artifact_id, owner))
        row = _row(await cur.fetchone())
    if row:
        row["metadata"] = _decode(row.get("metadata"), {})
    return row


async def execute_run(run_id: str, owner: str) -> None:
    """Execute a dependency-ordered workflow graph with durable events.

    Execution is owned by a renewable lease with a fencing generation. Every
    status/event write and artifact created by this executor is fenced on that
    generation, so an executor that lost a takeover cannot make its late work
    look durable. This does not promise exactly-once side effects: a crash
    between an external effect and its checkpoint stays ambiguous by design
    and is reported as a durable event rather than silently dropped.
    """
    run = await get_run(run_id, owner)
    if not run:
        return
    workflow = await get_workflow(run.get("workflow_id"), owner) if run.get("workflow_id") else None
    graph = (workflow or {}).get("graph") or {}
    nodes = order_nodes(graph.get("nodes") or [], graph.get("edges") or [])
    report: dict[str, Any] = run.get("report") or {"objective": run["objective"], "summary": "", "steps": [], "artifacts": [], "decisions": []}
    claim: RunClaim | None = None
    heartbeat: asyncio.Task | None = None
    try:
        claim = await claim_run(
            run_id, owner, worker_id=f"worker-{uuid.uuid4().hex[:12]}",
        )
        if claim is None:
            return

        async def _renew_lease() -> None:
            interval = claim.lease_seconds / LEASE_RENEW_DIVISOR
            while True:
                await asyncio.sleep(interval)
                with suppress(Exception):
                    await renew_run_lease(claim, lease_seconds=claim.lease_seconds)

        heartbeat = asyncio.create_task(_renew_lease(), name=f"v2-lease-{run_id}")

        prior_events = await list_events(run_id, owner)
        if run.get("report") is None:
            for event in prior_events:
                if event.get("event_type") != "step_completed":
                    continue
                payload = event.get("payload") or {}
                output = payload.get("output") or ""
                report["steps"].append({
                    "id": event.get("step_id"),
                    "label": payload.get("label") or event.get("step_id") or "step",
                    "output": output,
                })
                if output:
                    report["summary"] = output[:1000]
        completed_steps = {e.get("step_id") for e in prior_events if e.get("event_type") == "step_completed"}
        # Only an exact-bound grant consumed by a live claim counts as approval.
        # An approval_resolved event alone never authorises a gate.
        consumed_gates = await consumed_gate_steps(run_id, owner)
        await update_run(run_id, owner, "running", claim=claim)
        if not any(e.get("event_type") == "run_started" for e in prior_events):
            await append_event(run_id, "run_started", {"objective": run["objective"]}, claim=claim)
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
                    await append_event(run_id, "step_skipped", {"label": node.get("label") or "conditional", "condition": condition}, str(node.get("id") or "condition"), claim=claim)
                    continue
            if node.get("type") == "parallel":
                children = [child for child in (node.get("children") or []) if isinstance(child, dict)]
                completed_children = {
                    event.get("step_id"): event
                    for event in prior_events
                    if event.get("event_type") == "step_completed"
                    and event.get("step_id")
                    and (event.get("payload") or {}).get("parallel") is True
                }
                async def run_parallel_child(child: dict[str, Any]) -> dict[str, Any]:
                    child_id = str(child.get("id") or uuid.uuid4().hex[:8])
                    child_label = str(child.get("label") or child.get("agent") or "parallel step")
                    completed = completed_children.get(child_id)
                    if completed:
                        payload = completed.get("payload") or {}
                        return {
                            "id": child_id,
                            "label": payload.get("label") or child_label,
                            "output": payload.get("output") or "",
                        }
                    await append_event(run_id, "step_started", {"label": child_label, "parallel": True}, child_id, claim=claim)
                    output = await run_agent_node(child)
                    await append_event(run_id, "step_completed", {"label": child_label, "output": output[:4000], "parallel": True}, child_id, claim=claim)
                    return {"id": child_id, "label": child_label, "output": output}
                parallel_results = await asyncio.gather(*(run_parallel_child(child) for child in children))
                reported_step_ids = {step.get("id") for step in report["steps"]}
                report["steps"].extend(result for result in parallel_results if result["id"] not in reported_step_ids)
                if parallel_results:
                    report["summary"] = parallel_results[-1]["output"][:1000]
                continue
            step_id = str(node.get("id") or uuid.uuid4().hex[:8])
            label = str(node.get("label") or node.get("agent") or node.get("type") or "step")
            if step_id in completed_steps or (node.get("type") == "approval" and step_id in consumed_gates):
                continue
            await append_event(run_id, "step_started", {"label": label}, step_id, claim=claim)
            if node.get("type") == "approval":
                await park_at_approval(
                    run_id, owner, step_id,
                    claim=claim, report=report, workflow=workflow)
                return
            output = await run_agent_node(node)
            report["steps"].append({"id": step_id, "label": label, "output": output})
            if output:
                report["summary"] = output[:1000]
            await append_event(run_id, "step_completed", {"label": label, "output": output[:4000]}, step_id, claim=claim)
        report_artifact = await create_artifact(
            run_id, owner, "run-report.json", json.dumps(report, indent=2),
            "application/json", claim=claim)
        if report_artifact:
            report["artifacts"].append({"id": report_artifact["id"], "name": report_artifact["name"], "mime_type": report_artifact["mime_type"]})
        await update_run(run_id, owner, "completed", report=report, claim=claim)
        await append_event(run_id, "run_completed", {"summary": report["summary"], "step_count": len(report["steps"])}, claim=claim)
    except StaleRunClaim:
        # Another executor owns this run now. Do NOT write a status through the
        # claim: that would corrupt the winner's state. The side effects we
        # already performed are ambiguous, so leave a durable audit event.
        _logger.info("v2 run %s lost its lease; stopping without a fenced state write", run_id)
        with suppress(Exception):
            await append_event(run_id, "execution_ambiguous", {
                "detail": (
                    "executor lost its lease before its final checkpoint; "
                    "side effects after its last durable step are ambiguous "
                    "and need human reconciliation"
                ),
                "generation": claim.generation if claim else None,
            })
        return
    except ApprovalAmbiguous as exc:
        # A grant was consumed but the run state disagrees. Report it as a
        # durable audit event; never replay the gate or its side effects.
        _logger.error("v2 run %s approval ambiguity: %s", run_id, exc)
        with suppress(Exception):
            await append_event(run_id, "approval_ambiguous", {"detail": str(exc)[:500]})
        return
    except ApprovalBindingError as exc:
        # The stored gate no longer matches its grant. Fail closed and leave a
        # trace instead of inventing authority from the event history.
        _logger.error("v2 run %s approval binding error: %s", run_id, exc)
        with suppress(Exception):
            await append_event(run_id, "approval_binding_error", {"detail": str(exc)[:500]})
        return
    except asyncio.CancelledError:
        if claim is not None:
            await update_run(run_id, owner, "cancelled", claim=claim)
            with suppress(StaleRunClaim):
                await append_event(run_id, "run_cancelled", {}, claim=claim)
        raise
    except Exception as exc:  # noqa: BLE001 — degraded path is preferable to 500
        if claim is not None:
            await update_run(run_id, owner, "failed", report={**report, "error": str(exc)[:500]}, claim=claim)
            with suppress(StaleRunClaim):
                await append_event(run_id, "run_failed", {"error": str(exc)[:500]}, claim=claim)
    finally:
        if heartbeat is not None:
            heartbeat.cancel()
            with suppress(asyncio.CancelledError):
                await heartbeat
        if claim is not None:
            await release_run_lease(claim)
        _run_tasks.pop(run_id, None)


def start_run(run_id: str, owner: str) -> None:
    if run_id not in _run_tasks:
        task = asyncio.create_task(execute_run(run_id, owner), name=f"v2-run-{run_id}")

        def _done(done: asyncio.Task, run_id: str = run_id) -> None:
            _run_tasks.pop(run_id, None)
            if done.cancelled():
                return
            exc = done.exception()
            if exc is not None:
                _logger.error("v2 run %s failed: %s", run_id, exc)

        task.add_done_callback(_done)
        _run_tasks[run_id] = task


async def cancel_run(run_id: str, owner: str) -> bool:
    run = await get_run(run_id, owner)
    task = _run_tasks.get(run_id)
    if not run or not task:
        return False
    task.cancel()
    return True
