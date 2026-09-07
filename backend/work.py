"""Unified work sessions — the seam between chat, runs, routines and the UI.

A work session is any user-visible unit of work originating from a
chat-triggered agent reply, a /api/v2/runs workflow, a routine, or a
handoff. It exposes a normalized, replayable event interface so the
workspace rail can render "what is happening now" without knowing the
underlying orchestrator.

Additive only: existing v2 run tables and chat endpoints are untouched.
"""
from __future__ import annotations

import asyncio
import json
import time
import uuid
from contextlib import suppress
from typing import Any

import aiosqlite

from . import db

SCHEMA = """
CREATE TABLE IF NOT EXISTS work_sessions (
    id TEXT PRIMARY KEY, owner TEXT NOT NULL, source TEXT NOT NULL DEFAULT 'chat',
    status TEXT NOT NULL DEFAULT 'queued', objective TEXT NOT NULL DEFAULT '',
    channel_id TEXT, root_message_id INTEGER, run_id TEXT,
    active_step TEXT, summary TEXT NOT NULL DEFAULT '',
    requires_action INTEGER NOT NULL DEFAULT 0,
    created_at REAL NOT NULL, started_at REAL, finished_at REAL
);
CREATE INDEX IF NOT EXISTS idx_work_owner ON work_sessions(owner, created_at);
CREATE INDEX IF NOT EXISTS idx_work_status ON work_sessions(status, created_at);
CREATE TABLE IF NOT EXISTS work_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT, work_id TEXT NOT NULL,
    seq INTEGER NOT NULL, type TEXT NOT NULL, step_id TEXT,
    payload TEXT NOT NULL DEFAULT '{}', created_at REAL NOT NULL,
    UNIQUE(work_id, seq), FOREIGN KEY(work_id) REFERENCES work_sessions(id)
);
CREATE INDEX IF NOT EXISTS idx_work_events_work ON work_events(work_id, seq);
CREATE TABLE IF NOT EXISTS work_message_links (
    work_id TEXT NOT NULL, message_id INTEGER NOT NULL,
    UNIQUE(work_id, message_id), FOREIGN KEY(work_id) REFERENCES work_sessions(id)
);
"""

ALLOWED_TYPES = {
    "work_queued", "work_started", "agent_started", "tool_started",
    "tool_finished", "approval_requested", "approval_resolved",
    "message_linked", "artifact_created", "work_completed",
    "work_failed", "work_cancelled",
}

TERMINAL = {"completed", "failed", "cancelled"}

_event_lock = asyncio.Lock()


async def init_db() -> None:
    async with aiosqlite.connect(db.DB_PATH) as conn:
        await conn.executescript(SCHEMA)
        await conn.commit()

def _row(row: aiosqlite.Row | None) -> dict[str, Any] | None:
    return dict(row) if row else None


def _decode(value: str | None, fallback: Any) -> Any:
    try:
        return json.loads(value or "")
    except (TypeError, json.JSONDecodeError):
        return fallback


def _public(session: dict[str, Any]) -> dict[str, Any]:
    out = dict(session)
    out["requires_action"] = bool(out.get("requires_action"))
    return out


async def create_session(
    owner: str,
    objective: str,
    *,
    source: str = "chat",
    channel_id: str | None = None,
    root_message_id: int | None = None,
    run_id: str | None = None,
) -> dict[str, Any]:
    work_id = f"work_{uuid.uuid4().hex[:12]}"
    now = time.time()
    async with aiosqlite.connect(db.DB_PATH) as conn:
        await conn.execute(
            "INSERT INTO work_sessions(id, owner, source, status, objective, channel_id,"
            " root_message_id, run_id, created_at) VALUES(?,?,?,?,?,?,?,?,?)",
            (work_id, owner, source, "queued", objective or "", channel_id,
             root_message_id, run_id, now),
        )
        await conn.commit()
    await append_event(work_id, "work_queued", {"objective": objective or "", "source": source})
    row = await get_session(work_id, owner)
    assert row is not None
    return row


async def get_session(work_id: str, owner: str) -> dict[str, Any] | None:
    async with aiosqlite.connect(db.DB_PATH) as conn:
        conn.row_factory = aiosqlite.Row
        cur = await conn.execute(
            "SELECT * FROM work_sessions WHERE id=? AND owner=?", (work_id, owner))
        row = _row(await cur.fetchone())
    return _public(row) if row else None


async def get_session_any_owner(work_id: str) -> dict[str, Any] | None:
    async with aiosqlite.connect(db.DB_PATH) as conn:
        conn.row_factory = aiosqlite.Row
        cur = await conn.execute("SELECT * FROM work_sessions WHERE id=?", (work_id,))
        row = _row(await cur.fetchone())
    return _public(row) if row else None


async def list_sessions(owner: str, limit: int = 50) -> list[dict[str, Any]]:
    async with aiosqlite.connect(db.DB_PATH) as conn:
        conn.row_factory = aiosqlite.Row
        cur = await conn.execute(
            "SELECT * FROM work_sessions WHERE owner=? ORDER BY created_at DESC LIMIT ?",
            (owner, min(max(limit, 1), 100)),
        )
        rows = [_public(dict(r)) for r in await cur.fetchall()]
    return rows


async def find_by_run(run_id: str, owner: str) -> dict[str, Any] | None:
    async with aiosqlite.connect(db.DB_PATH) as conn:
        conn.row_factory = aiosqlite.Row
        cur = await conn.execute(
            "SELECT * FROM work_sessions WHERE run_id=? AND owner=? ORDER BY created_at DESC LIMIT 1",
            (run_id, owner),
        )
        row = _row(await cur.fetchone())
    return _public(row) if row else None


# Payload keys that must never leak to the UI event stream.
_SENSITIVE_KEYS = frozenset({
    "secret", "api_key", "api_secret", "access_token", "refresh_token",
    "hidden_prompt", "password", "token", "authorization", "client_secret",
    "set_cookie",
})


def _strip_sensitive(payload: dict[str, Any] | None) -> dict[str, Any]:
    safe = dict(payload or {})
    for key in _SENSITIVE_KEYS:
        safe.pop(key, None)
    return safe


async def append_event(
    work_id: str,
    event_type: str,
    payload: dict[str, Any] | None = None,
    step_id: str | None = None,
) -> dict[str, Any]:
    if event_type not in ALLOWED_TYPES:
        raise ValueError(f"unsupported work event type: {event_type}")
    # Strip anything that must never leak to the UI.
    safe = _strip_sensitive(payload)
    async with _event_lock:
        async with aiosqlite.connect(db.DB_PATH) as conn:
            cur = await conn.execute(
                "SELECT COALESCE(MAX(seq), 0) + 1 FROM work_events WHERE work_id=?", (work_id,))
            (seq,) = await cur.fetchone()
            now = time.time()
            await conn.execute(
                "INSERT INTO work_events(work_id, seq, type, step_id, payload, created_at)"
                " VALUES(?,?,?,?,?,?)",
                (work_id, seq, event_type, step_id, json.dumps(safe), now),
            )
            if event_type == "work_started":
                await conn.execute(
                    "UPDATE work_sessions SET status='running',"
                    " started_at=COALESCE(started_at, ?) WHERE id=?", (now, work_id))
            elif event_type == "approval_requested":
                await conn.execute(
                    "UPDATE work_sessions SET status='waiting_for_approval',"
                    " requires_action=1, active_step=? WHERE id=?", (step_id, work_id))
            elif event_type == "approval_resolved":
                await conn.execute(
                    "UPDATE work_sessions SET status='running', requires_action=0 WHERE id=?",
                    (work_id,),
                )
            elif event_type == "work_completed":
                summary = str(safe.get("summary") or "")[:1000]
                await conn.execute(
                    "UPDATE work_sessions SET status='completed', summary=?,"
                    " requires_action=0, finished_at=? WHERE id=?", (summary, now, work_id))
            elif event_type == "work_failed":
                summary = str(safe.get("error") or "")[:1000]
                await conn.execute(
                    "UPDATE work_sessions SET status='failed', summary=?,"
                    " requires_action=1, finished_at=? WHERE id=?", (summary, now, work_id))
            elif event_type == "work_cancelled":
                await conn.execute(
                    "UPDATE work_sessions SET status='cancelled', requires_action=0,"
                    " finished_at=? WHERE id=?", (now, work_id))
            if step_id and event_type not in {"work_queued", "work_completed", "work_failed", "work_cancelled"}:
                await conn.execute(
                    "UPDATE work_sessions SET active_step=? WHERE id=? AND active_step IS NULL",
                    (step_id, work_id),
                )
            await conn.commit()
    return {"work_id": work_id, "seq": seq, "type": event_type,
            "step_id": step_id, "payload": safe, "created_at": now}


async def list_events(work_id: str, owner: str, after: int = 0) -> list[dict[str, Any]]:
    session = await get_session(work_id, owner)
    if not session:
        return []
    events: list[dict[str, Any]] = []
    async with aiosqlite.connect(db.DB_PATH) as conn:
        conn.row_factory = aiosqlite.Row
        cur = await conn.execute(
            "SELECT e.* FROM work_events e JOIN work_sessions s ON s.id=e.work_id"
            " WHERE e.work_id=? AND s.owner=? AND e.seq>? ORDER BY e.seq",
            (work_id, owner, after),
        )
        for r in await cur.fetchall():
            row = dict(r)
            events.append({
                "work_id": work_id, "seq": row["seq"], "type": row["type"],
                "step_id": row["step_id"], "payload": _decode(row.get("payload"), {}),
                "created_at": row["created_at"],
            })
    # Reuse linked run events so run-backed sessions show the same trace
    # through the normalized interface without duplicating orchestration.
    run_id = session.get("run_id")
    if run_id:
        from . import v2 as v2_mod
        run = await v2_mod.get_run(run_id, owner)
        if run:
            run_events = await v2_mod.list_events(run_id, owner, 0)
            mapping = {
                "run_queued": "work_queued", "run_started": "work_started",
                "step_started": "agent_started", "step_completed": "tool_finished",
                "step_skipped": "tool_finished", "approval_requested": "approval_requested",
                "approval_resolved": "approval_resolved", "run_completed": "work_completed",
                "run_failed": "work_failed", "run_cancelled": "work_cancelled",
                "run_denied": "work_cancelled",
            }
            base = max([e["seq"] for e in events], default=0)
            for idx, re in enumerate(run_events, start=1):
                wtype = mapping.get(re.get("event_type", ""), "tool_finished")
                events.append({
                    "work_id": work_id, "seq": base + idx, "type": wtype,
                    "step_id": re.get("step_id"),
                    "payload": _strip_sensitive(re.get("payload")),
                    "created_at": re.get("created_at"), "via_run": run_id,
                })
            events = [e for e in events if e["seq"] > after]
            events.sort(key=lambda e: e["seq"])
    return events


async def link_message(work_id: str, message_id: int) -> bool:
    """Idempotent message-to-work linking. Returns True if newly linked."""
    async with aiosqlite.connect(db.DB_PATH) as conn:
        cur = await conn.execute(
            "INSERT OR IGNORE INTO work_message_links(work_id, message_id) VALUES(?,?)",
            (work_id, message_id),
        )
        await conn.commit()
        return cur.rowcount > 0


async def linked_messages(work_id: str, owner: str) -> list[int]:
    if not await get_session(work_id, owner):
        return []
    async with aiosqlite.connect(db.DB_PATH) as conn:
        cur = await conn.execute(
            "SELECT message_id FROM work_message_links WHERE work_id=? ORDER BY message_id",
            (work_id,),
        )
        return [r[0] for r in await cur.fetchall()]


async def cancel_session(work_id: str, owner: str) -> dict[str, Any] | None:
    session = await get_session(work_id, owner)
    if not session:
        return None
    if session.get("status") in TERMINAL:
        return session
    run_id = session.get("run_id")
    if run_id:
        from . import v2 as v2_mod
        with suppress(Exception):
            await v2_mod.cancel_run(run_id, owner)
    await append_event(work_id, "work_cancelled", {})
    return await get_session(work_id, owner)


async def recover_interrupted() -> int:
    """Settle sessions left active by a previous process.

    Run-backed sessions whose run is recoverable are left alone (v2
    restarts them and the work view re-syncs). Everything else stuck in
    a live status is marked cancelled so the rail never shows phantom
    "running" work after a restart. Returns the settled count.
    """
    from . import v2 as v2_mod
    async with aiosqlite.connect(db.DB_PATH) as conn:
        conn.row_factory = aiosqlite.Row
        cur = await conn.execute(
            "SELECT * FROM work_sessions WHERE status IN"
            " ('queued', 'running', 'waiting_for_approval')")
        stuck = [dict(r) for r in await cur.fetchall()]
    settled = 0
    for session in stuck:
        run_id = session.get("run_id")
        if run_id:
            run = await v2_mod.get_run_any_owner(run_id)
            if run and run.get("status") in {"queued", "running", "waiting_for_approval"}:
                continue
        with suppress(Exception):
            await append_event(
                session["id"], "work_cancelled", {"reason": "interrupted by restart"})
            settled += 1
    return settled
