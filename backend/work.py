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
from .work_state import event_lock as _loop_event_lock

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
-- Run-derived events are materialized into work_events exactly once. Their
-- seq is then part of the same immutable global order as native work events,
-- so appending new work events cannot renumber or replay them.
CREATE TABLE IF NOT EXISTS work_run_event_mirror (
    work_id TEXT NOT NULL, run_seq INTEGER NOT NULL, work_seq INTEGER NOT NULL,
    via_run TEXT NOT NULL, PRIMARY KEY(work_id, run_seq),
    FOREIGN KEY(work_id) REFERENCES work_sessions(id)
);
"""

# Run event types mapped onto the normalized work vocabulary. Applied once,
# when an event is mirrored, instead of on every cursor poll.
_RUN_EVENT_TYPES = {
    "run_queued": "work_queued", "run_started": "work_started",
    "step_started": "agent_started", "step_completed": "tool_finished",
    "step_skipped": "tool_finished", "approval_requested": "approval_requested",
    "approval_resolved": "approval_resolved", "run_completed": "work_completed",
    "run_failed": "work_failed", "run_cancelled": "work_cancelled",
    "run_denied": "work_cancelled",
}

ALLOWED_TYPES = {
    "work_queued", "work_started", "agent_started", "tool_started",
    "tool_finished", "approval_requested", "approval_resolved",
    "message_linked", "artifact_created", "work_completed",
    "work_failed", "work_cancelled",
}

TERMINAL = {"completed", "failed", "cancelled"}


class CancellationFailed(RuntimeError):
    """The underlying run could not be cancelled; work stays active.

    A work session is a view over a run, not an independent executor. If the
    run is still live, marking the session cancelled would let a parked grant
    resume work the owner believes they stopped.
    """


def _event_lock() -> asyncio.Lock:
    """Per-loop event lock (see work_state.event_lock)."""
    return _loop_event_lock()


async def init_db() -> None:
    async with db.open_db() as conn:
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
    async with db.open_db() as conn:
        await conn.execute(
            "INSERT INTO work_sessions(id, owner, source, status, objective, channel_id,"
            " root_message_id, run_id, created_at) VALUES(?,?,?,?,?,?,?,?,?)",
            (work_id, owner, source, "queued", objective or "", channel_id,
             root_message_id, run_id, now),
        )
        await conn.commit()
    await append_event(work_id, "work_queued", {"objective": objective or "", "source": source})
    return _public({
        "id": work_id, "owner": owner, "source": source, "status": "queued",
        "objective": objective or "", "channel_id": channel_id,
        "root_message_id": root_message_id, "run_id": run_id,
        "active_step": None, "summary": "", "requires_action": 0,
        "created_at": now, "started_at": None, "finished_at": None,
    })


async def get_session(work_id: str, owner: str) -> dict[str, Any] | None:
    async with db.open_db() as conn:
        conn.row_factory = aiosqlite.Row
        cur = await conn.execute(
            "SELECT * FROM work_sessions WHERE id=? AND owner=?", (work_id, owner))
        row = _row(await cur.fetchone())
    return _public(row) if row else None


async def get_session_any_owner(work_id: str) -> dict[str, Any] | None:
    async with db.open_db() as conn:
        conn.row_factory = aiosqlite.Row
        cur = await conn.execute("SELECT * FROM work_sessions WHERE id=?", (work_id,))
        row = _row(await cur.fetchone())
    return _public(row) if row else None


async def list_sessions(owner: str, limit: int = 50) -> list[dict[str, Any]]:
    async with db.open_db() as conn:
        conn.row_factory = aiosqlite.Row
        cur = await conn.execute(
            "SELECT * FROM work_sessions WHERE owner=? ORDER BY created_at DESC LIMIT ?",
            (owner, min(max(limit, 1), 100)),
        )
        rows = [_public(dict(r)) for r in await cur.fetchall()]
    return rows


async def find_by_run(run_id: str, owner: str) -> dict[str, Any] | None:
    async with db.open_db() as conn:
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


async def _append_event_in_conn(
    conn: Any, work_id: str, event_type: str,
    payload: dict[str, Any] | None, step_id: str | None,
) -> dict[str, Any]:
    """Write one event and its session state transition on an open connection.

    Callers own the transaction and the event lock. The run-event mirror needs
    to commit the event and its one-time reservation atomically; keeping the
    write here avoids calling the locking public helper while already holding
    the lock.
    """
    if event_type not in ALLOWED_TYPES:
        raise ValueError(f"unsupported work event type: {event_type}")
    safe = _strip_sensitive(payload)
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
    return {"work_id": work_id, "seq": seq, "type": event_type,
            "step_id": step_id, "payload": safe, "created_at": now}


async def append_event(
    work_id: str,
    event_type: str,
    payload: dict[str, Any] | None = None,
    step_id: str | None = None,
) -> dict[str, Any]:
    async with _event_lock():
        async with db.open_db() as conn:
            event = await _append_event_in_conn(
                conn, work_id, event_type, payload, step_id)
            await conn.commit()
    return event


MIRROR_PAGE_SIZE = 2000


async def mark_started_from_run(work_id: str, owner: str) -> bool:
    """Reflect a claimed run's running state without inventing a work event.

    The run's own `run_started` event is the audit record; the work mirror
    materialises it once. Writing a native `work_started` here would race the
    mirror and duplicate that event whenever the UI polls between claim and
    run_started.
    """
    async with db.open_db() as conn:
        cur = await conn.execute(
            "UPDATE work_sessions SET status='running', started_at=COALESCE(started_at, ?)"
            " WHERE id=? AND owner=? AND status='queued'",
            (time.time(), work_id, owner))
        await conn.commit()
        return cur.rowcount > 0


async def mark_waiting_from_run(
    work_id: str, owner: str, step_id: str | None,
) -> bool:
    """Reflect a parked run atomically without inventing an approval event.

    Concurrent work-list polls used to each append a native
    ``approval_requested`` before any of them committed the waiting state.
    The run's own approval_requested event is the audit record; the mirror
    materialises it once with the real step and canonical label. The active
    step follows the current gate, so a second gate cannot inherit the first
    gate's stale step id; when no grant exists the stale step is cleared
    instead of being presented as the current gate.
    """
    async with db.open_db() as conn:
        cur = await conn.execute(
            "UPDATE work_sessions SET status='waiting_for_approval',"
            " requires_action=1, active_step=?"
            " WHERE id=? AND owner=? AND status IN ('queued','running')",
            (step_id, work_id, owner))
        await conn.commit()
        return cur.rowcount > 0


async def mark_terminal_from_run(
    work_id: str, owner: str, status: str, *, summary: str = "",
    requires_action: bool = False,
) -> bool:
    """Settle a work session atomically from a run's terminal state.

    Concurrent work-list polls all read the same stale snapshot; tagging their
    native events still leaves one event per poll. A guarded state update lets
    only the first poll settle the session, and the run's own terminal event is
    the single audit record the mirror materialises.
    """
    async with db.open_db() as conn:
        cur = await conn.execute(
            "UPDATE work_sessions SET status=?, summary=?, requires_action=?,"
            " finished_at=COALESCE(finished_at, ?), active_step=NULL"
            " WHERE id=? AND owner=? AND status NOT IN ('completed','failed','cancelled')",
            (status, summary[:1000], int(requires_action), time.time(), work_id, owner))
        await conn.commit()
        return cur.rowcount > 0


async def _mirror_run_events(work_id: str, owner: str, run_id: str) -> None:
    """Materialize unseen run events into the immutable work-event order.

    The work event and its one-time reservation are committed together: a
    crash can no longer leave a work_seq=0 reservation that makes a run event
    invisible forever. Reservations left by older code are retried, adopting
    an already written work event instead of duplicating it. Paging starts at
    the oldest unfinished reservation (if any) and continues past the highest
    reserved run event, so a run with more than one page of events does not
    silently lose its tail.
    """
    from . import v2 as v2_mod

    async with db.open_db() as conn:
        cur = await conn.execute(
            "SELECT run_seq, work_seq FROM work_run_event_mirror WHERE work_id=?",
            (work_id,))
        rows = [(int(s), int(w or 0)) for s, w in await cur.fetchall()]
    done = {seq for seq, work_seq in rows if work_seq > 0}
    pending = [seq for seq, work_seq in rows if work_seq == 0]
    cursor = (min(pending) - 1) if pending else max((seq for seq, _ in rows), default=0)

    while True:
        batch = await v2_mod.list_events(run_id, owner, cursor, limit=MIRROR_PAGE_SIZE)
        if not batch:
            break
        before = cursor
        async with _event_lock():
            async with db.open_db() as conn:
                await conn.execute("BEGIN IMMEDIATE")
                for run_event in batch:
                    run_seq = int(run_event.get("seq") or 0)
                    if run_seq <= 0:
                        continue
                    if run_seq in done:
                        cursor = max(cursor, run_seq)
                        continue
                    cur = await conn.execute(
                        "SELECT work_seq FROM work_run_event_mirror"
                        " WHERE work_id=? AND run_seq=?", (work_id, run_seq))
                    row = await cur.fetchone()
                    if row is not None and int(row[0] or 0) > 0:
                        done.add(run_seq)
                        cursor = max(cursor, run_seq)
                        continue
                    payload = dict(run_event.get("payload") or {})
                    wtype = _RUN_EVENT_TYPES.get(run_event.get("event_type", ""), "tool_finished")
                    # Older code could commit the work event and crash before
                    # recording its work_seq. Adopt that event instead of
                    # appending a duplicate to the audit order.
                    cur = await conn.execute(
                        "SELECT seq FROM work_events WHERE work_id=?"
                        " AND json_extract(payload, '$._via_run')=?"
                        " AND json_extract(payload, '$._via_run_seq')=? LIMIT 1",
                        (work_id, run_id, run_seq))
                    existing = await cur.fetchone()
                    if existing is not None:
                        work_seq = int(existing[0])
                    else:
                        mirrored = await _append_event_in_conn(
                            conn, work_id, wtype,
                            {**payload, "_via_run": run_id, "_via_run_seq": run_seq},
                            run_event.get("step_id"))
                        work_seq = int(mirrored["seq"])
                    await conn.execute(
                        "INSERT INTO work_run_event_mirror(work_id, run_seq, work_seq, via_run)"
                        " VALUES(?,?,?,?) ON CONFLICT(work_id, run_seq)"
                        " DO UPDATE SET work_seq=excluded.work_seq",
                        (work_id, run_seq, work_seq, run_id))
                    done.add(run_seq)
                    cursor = max(cursor, run_seq)
                await conn.commit()
        if cursor <= before or len(batch) < MIRROR_PAGE_SIZE:
            break


async def list_events(work_id: str, owner: str, after: int = 0) -> list[dict[str, Any]]:
    async with db.open_db() as conn:
        cur = await conn.execute(
            "SELECT run_id FROM work_sessions WHERE id=? AND owner=?",
            (work_id, owner),
        )
        row = await cur.fetchone()
        if row is None:
            return []
        run_id = row[0]
    if run_id:
        await _mirror_run_events(work_id, owner, run_id)
    # One immutable global order for both native and run-derived events.
    events: list[dict[str, Any]] = []
    async with db.open_db() as conn:
        conn.row_factory = aiosqlite.Row
        cur = await conn.execute(
            "SELECT e.seq, e.type, e.step_id, e.payload, e.created_at "
            "FROM work_events e JOIN work_sessions s ON s.id=e.work_id"
            " WHERE e.work_id=? AND s.owner=? AND e.seq>? ORDER BY e.seq",
            (work_id, owner, after),
        )
        for r in await cur.fetchall():
            row = dict(r)
            payload = _decode(row.get("payload"), {})
            via_run = payload.pop("_via_run", None)
            payload.pop("_via_run_seq", None)
            event = {
                "work_id": work_id, "seq": row["seq"], "type": row["type"],
                "step_id": row["step_id"], "payload": payload,
                "created_at": row["created_at"],
            }
            if via_run:
                event["via_run"] = via_run
            events.append(event)
    return events


async def link_message(work_id: str, message_id: int) -> bool:
    """Idempotent message-to-work linking. Returns True if newly linked."""
    async with db.open_db() as conn:
        cur = await conn.execute(
            "INSERT OR IGNORE INTO work_message_links(work_id, message_id) VALUES(?,?)",
            (work_id, message_id),
        )
        await conn.commit()
        return cur.rowcount > 0


async def linked_messages(work_id: str, owner: str) -> list[int]:
    async with db.open_db() as conn:
        cur = await conn.execute(
            "SELECT l.message_id FROM work_message_links l "
            "JOIN work_sessions s ON s.id=l.work_id "
            "WHERE l.work_id=? AND s.owner=? ORDER BY l.message_id",
            (work_id, owner),
        )
        return [r[0] for r in await cur.fetchall()]


async def cancel_session(work_id: str, owner: str) -> dict[str, Any] | None:
    """Cancel the underlying run before reporting the work session cancelled.

    Marking the session first used to leave a parked approval grant live. A
    late decision then resumed the run and overwrote the "cancelled" session
    with fresh events — work the owner believed they had stopped. A failed or
    raced run cancellation now fails closed: the session stays active.
    """
    session = await get_session(work_id, owner)
    if not session:
        return None
    if session.get("status") in TERMINAL:
        return session
    run_id = session.get("run_id")
    settlement: dict[str, Any] = {}
    if run_id:
        from . import v2 as v2_mod
        try:
            cancelled = await v2_mod.cancel_run(run_id, owner)
        except Exception as exc:
            raise CancellationFailed(
                f"run {run_id} could not be cancelled; work was kept active") from exc
        if not cancelled:
            current = await v2_mod.get_run(run_id, owner)
            if current is not None and current.get("status") not in v2_mod.TERMINAL_RUN_STATUSES:
                raise CancellationFailed(
                    f"run {run_id} is still active; work was kept active")
        # Tag the native settlement with the run event it mirrors so the work
        # mirror adopts it instead of appending a second work_cancelled.
        for event in reversed(await v2_mod.list_events(run_id, owner)):
            if event.get("event_type") == "run_cancelled":
                settlement = {"_via_run": run_id, "_via_run_seq": event.get("seq")}
                break
    await append_event(work_id, "work_cancelled", settlement)
    return await get_session(work_id, owner)


async def recover_interrupted() -> int:
    """Settle sessions left active by a previous process.

    Run-backed sessions whose run is recoverable are left alone (v2
    restarts them and the work view re-syncs). A run that already reached a
    terminal state is settled with *that* outcome: recording "interrupted by
    restart" over a completed run fabricated a cancellation in the audit log.
    Only sessions with no live or terminal run are marked interrupted.
    Returns the settled count.
    """
    from . import v2 as v2_mod
    async with db.open_db() as conn:
        conn.row_factory = aiosqlite.Row
        cur = await conn.execute(
            "SELECT * FROM work_sessions WHERE status IN"
            " ('queued', 'running', 'waiting_for_approval')")
        stuck = [dict(r) for r in await cur.fetchall()]
    settled = 0
    for session in stuck:
        run_id = session.get("run_id")
        event_type, payload = "work_cancelled", {"reason": "interrupted by restart"}
        if run_id:
            run = await v2_mod.get_run_status_any_owner(run_id)
            if run and run.get("status") in {"queued", "running", "waiting_for_approval"}:
                continue
            if run and run.get("status") == "completed":
                report = run.get("report") or {}
                event_type, payload = "work_completed", {
                    "summary": str(report.get("summary") or "")[:1000]}
            elif run and run.get("status") == "failed":
                report = run.get("report") or {}
                event_type, payload = "work_failed", {
                    "error": str(report.get("error") or "workflow failed")[:500]}
            elif run and run.get("status") == "cancelled":
                event_type, payload = "work_cancelled", {"reason": "run cancelled"}
            # Mirror first so the run's own terminal event settles the session
            # and an old run_queued tail cannot be appended after it.
            with suppress(Exception):
                await _mirror_run_events(session["id"], str(session["owner"]), run_id)
            settled_session = await get_session(session["id"], str(session["owner"]))
            if settled_session is not None and settled_session.get("status") in TERMINAL:
                # The mirrored terminal event already told the truth; a second
                # native terminal event would duplicate the audit record.
                settled += 1
                continue
        with suppress(Exception):
            await append_event(session["id"], event_type, payload)
            settled += 1
    return settled
