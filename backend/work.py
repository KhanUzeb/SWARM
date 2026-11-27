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
    # Advisories: a warning or an unfinished step must never masquerade as a
    # successful tool result, but it is also not the run's outcome. A failed
    # step, a lost lease, or a gate whose grant no longer matches needs human
    # reconciliation while the run's own terminal event still decides whether
    # the work completed, failed or was cancelled. Mapping them to
    # ``work_failed`` settled the session early and terminal dedupe then
    # suppressed the later, truthful ``run_completed`` — a completed run shown
    # as failed forever. They join the audit order as ``work_advisory``.
    "step_failed": "work_advisory", "execution_ambiguous": "work_advisory",
    "approval_ambiguous": "work_advisory", "approval_binding_error": "work_advisory",
    "approval_reconciled": "approval_requested",
}

ALLOWED_TYPES = {
    "work_queued", "work_started", "agent_started", "tool_started",
    "tool_finished", "approval_requested", "approval_resolved",
    "message_linked", "artifact_created", "work_advisory",
    "work_completed", "work_failed", "work_cancelled",
}

TERMINAL = {"completed", "failed", "cancelled"}
TERMINAL_WORK_EVENTS = {"work_completed", "work_failed", "work_cancelled"}


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
    # A terminal session is final. A delayed or replayed run event may still
    # join the immutable event order, but it must never move the session back
    # to running/waiting, and a second terminal event must not overwrite the
    # first one's outcome. Every transition below is therefore conditional on
    # the session's current state.
    active = "status IN ('queued','running','waiting_for_approval')"
    open_terminal = "status NOT IN ('completed','failed','cancelled')"
    if event_type == "work_started":
        await conn.execute(
            "UPDATE work_sessions SET status='running',"
            f" started_at=COALESCE(started_at, ?) WHERE id=? AND {active}", (now, work_id))
    elif event_type == "approval_requested":
        # A replayed older gate event joins the audit order but must not move
        # the human's attention back to a gate that was already resolved.
        # Only a session that is not already parked adopts the event's step;
        # a parked session is repaired from the newest materialized gate by
        # _repair_active_gate below.
        await conn.execute(
            "UPDATE work_sessions SET status='waiting_for_approval', requires_action=1,"
            " active_step=CASE WHEN status='waiting_for_approval' OR active_step IS NOT NULL"
            " THEN active_step ELSE ? END"
            f" WHERE id=? AND {active}", (step_id, work_id))
    elif event_type == "approval_resolved":
        await conn.execute(
            f"UPDATE work_sessions SET status='running', requires_action=0"
            f" WHERE id=? AND {active}",
            (work_id,),
        )
    elif event_type == "work_advisory":
        # An advisory asks for a human's eyes, it does not own the outcome.
        # The session stays in whatever live state the run is in (running,
        # queued or parked at a gate) so the run's truthful terminal event can
        # still settle it. requires_action surfaces it without claiming the
        # work failed; a resolved gate or a terminal event clears the flag.
        await conn.execute(
            f"UPDATE work_sessions SET requires_action=1 WHERE id=? AND {open_terminal}",
            (work_id,),
        )
    elif event_type == "work_completed":
        summary = str(safe.get("summary") or "")[:1000]
        await conn.execute(
            "UPDATE work_sessions SET status='completed', summary=?,"
            f" requires_action=0, finished_at=? WHERE id=? AND {open_terminal}",
            (summary, now, work_id))
    elif event_type == "work_failed":
        summary = str(safe.get("error") or "")[:1000]
        await conn.execute(
            "UPDATE work_sessions SET status='failed', summary=?,"
            f" requires_action=1, finished_at=? WHERE id=? AND {open_terminal}",
            (summary, now, work_id))
    elif event_type == "work_cancelled":
        await conn.execute(
            "UPDATE work_sessions SET status='cancelled', requires_action=0,"
            f" finished_at=? WHERE id=? AND {open_terminal}", (now, work_id))
    if step_id and event_type not in {
            "work_queued", "work_advisory", "work_completed",
            "work_failed", "work_cancelled"}:
        await conn.execute(
            f"UPDATE work_sessions SET active_step=? WHERE id=? AND active_step IS NULL AND {active}",
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
            if event_type in TERMINAL_WORK_EVENTS:
                # The public helper is also used by the approval endpoint. A
                # terminal session is final for every caller, not just cancel,
                # so a second native settlement adopts the recorded event.
                cur = await conn.execute(
                    "SELECT seq, type, step_id, payload, created_at FROM work_events"
                    " WHERE work_id=? AND type IN"
                    " ('work_completed','work_failed','work_cancelled') LIMIT 1",
                    (work_id,))
                row = await cur.fetchone()
                if row is not None:
                    return {"work_id": work_id, "seq": int(row[0]), "type": row[1],
                            "step_id": row[2], "payload": _decode(row[3], {}),
                            "created_at": row[4]}
            event = await _append_event_in_conn(
                conn, work_id, event_type, payload, step_id)
            await conn.commit()
    return event


async def append_terminal_once(
    work_id: str, event_type: str, payload: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Append a native terminal event only when none has been recorded yet.

    Cancel, recovery and the run mirror can all try to settle the same
    session. The check and the append share the event lock and one transaction,
    so two concurrent cancels cannot each write a work_cancelled event, and a
    native settlement cannot duplicate a terminal event the mirror adopted.
    """
    if event_type not in TERMINAL_WORK_EVENTS:
        raise ValueError(f"not a terminal work event: {event_type}")
    async with _event_lock():
        async with db.open_db() as conn:
            cur = await conn.execute(
                "SELECT 1 FROM work_events WHERE work_id=?"
                " AND type IN ('work_completed','work_failed','work_cancelled') LIMIT 1",
                (work_id,))
            if await cur.fetchone() is not None:
                return None
            event = await _append_event_in_conn(conn, work_id, event_type, payload, None)
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


async def _repair_active_gate(work_id: str) -> None:
    """Point a parked session at the newest materialized approval gate.

    main._sync_work_with_run only re-marks a waiting session when its status
    changes, so a stale active_step would otherwise survive every poll. The
    newest approval_requested event in the immutable work order is the gate
    the human is actually looking at.
    """
    async with db.open_db() as conn:
        cur = await conn.execute(
            "SELECT s.status, s.active_step,"
            " (SELECT step_id FROM work_events WHERE work_id=s.id"
            "  AND type='approval_requested' AND step_id IS NOT NULL"
            "  ORDER BY seq DESC LIMIT 1) AS latest"
            " FROM work_sessions s WHERE s.id=?", (work_id,))
        row = await cur.fetchone()
        if not row or row[0] != "waiting_for_approval" or not row[2] or row[1] == row[2]:
            return
        await conn.execute(
            "UPDATE work_sessions SET active_step=? WHERE id=?"
            " AND status='waiting_for_approval'", (row[2], work_id))
        await conn.commit()


async def settle_from_recorded_terminal_event(work_id: str) -> bool:
    """Re-apply a recorded terminal event to a session that is still active.

    A terminal event without its state transition (old data, a crash between
    writes, or an adopted mirror reservation) used to make cancel_session
    return a running session even though the audit already said terminal.
    """
    async with db.open_db() as conn:
        conn.row_factory = aiosqlite.Row
        cur = await conn.execute(
            "SELECT type, payload, created_at FROM work_events WHERE work_id=?"
            " AND type IN ('work_completed','work_failed','work_cancelled')"
            " ORDER BY seq LIMIT 1", (work_id,))
        event = await cur.fetchone()
        if event is None:
            return False
        payload = _decode(event["payload"], {}) or {}
        now = float(event["created_at"] or time.time())
        if event["type"] == "work_completed":
            await conn.execute(
                "UPDATE work_sessions SET status='completed', summary=?,"
                " requires_action=0, active_step=NULL,"
                " finished_at=COALESCE(finished_at, ?)"
                " WHERE id=? AND status NOT IN ('completed','failed','cancelled')",
                (str(payload.get("summary") or "")[:1000], now, work_id))
        elif event["type"] == "work_failed":
            await conn.execute(
                "UPDATE work_sessions SET status='failed', summary=?,"
                " requires_action=1, active_step=NULL,"
                " finished_at=COALESCE(finished_at, ?)"
                " WHERE id=? AND status NOT IN ('completed','failed','cancelled')",
                (str(payload.get("error") or "")[:1000], now, work_id))
        else:
            await conn.execute(
                "UPDATE work_sessions SET status='cancelled', requires_action=0,"
                " active_step=NULL, finished_at=COALESCE(finished_at, ?)"
                " WHERE id=? AND status NOT IN ('completed','failed','cancelled')",
                (now, work_id))
        await conn.commit()
        return True


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
                    rtype = str(run_event.get("event_type") or "")
                    wtype = _RUN_EVENT_TYPES.get(rtype, "tool_finished")
                    if wtype == "work_advisory":
                        # Keep the run's own vocabulary in the payload so the
                        # rail can name the warning (failed step, lost lease,
                        # ambiguous gate) while the event stays non-terminal.
                        payload["kind"] = rtype
                        if not payload.get("detail"):
                            payload["detail"] = str(
                                payload.get("error") or payload.get("message") or "")[:500]
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
                    elif wtype == "approval_requested":
                        # An older gate replayed after a newer gate was already
                        # materialized must not add a second gate line or steal
                        # active_step. Adopt the newer gate's work event so the
                        # reservation advances without another audit event.
                        cur = await conn.execute(
                            "SELECT m.work_seq FROM work_run_event_mirror m"
                            " JOIN run_events re ON re.run_id=m.via_run AND re.seq=m.run_seq"
                            " WHERE m.work_id=? AND re.event_type='approval_requested'"
                            " AND re.seq>? AND m.work_seq>0"
                            " ORDER BY re.seq DESC LIMIT 1", (work_id, run_seq))
                        newer_gate = await cur.fetchone()
                        if newer_gate is not None:
                            work_seq = int(newer_gate[0])
                        else:
                            mirrored = await _append_event_in_conn(
                                conn, work_id, wtype,
                                {**payload, "_via_run": run_id, "_via_run_seq": run_seq},
                                run_event.get("step_id"))
                            work_seq = int(mirrored["seq"])
                    elif wtype in TERMINAL_WORK_EVENTS:
                        # A run can emit more than one terminal-shaped event
                        # (step_failed then run_failed, or a late failure after
                        # an owner cancel). The first terminal event owns the
                        # outcome; adopting it keeps one audit record and one
                        # immutable session status.
                        cur = await conn.execute(
                            "SELECT seq FROM work_events WHERE work_id=?"
                            " AND type IN ('work_completed','work_failed','work_cancelled')"
                            " LIMIT 1", (work_id,))
                        settled = await cur.fetchone()
                        if settled is not None:
                            work_seq = int(settled[0])
                        else:
                            mirrored = await _append_event_in_conn(
                                conn, work_id, wtype,
                                {**payload, "_via_run": run_id, "_via_run_seq": run_seq},
                                run_event.get("step_id"))
                            work_seq = int(mirrored["seq"])
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
    # Repair state that lagged behind the immutable event order. Both helpers
    # are guarded, so a settled or correctly parked session is untouched.
    await settle_from_recorded_terminal_event(work_id)
    await _repair_active_gate(work_id)


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
    """Cancel the underlying run, then settle the work view with the real outcome.

    Marking the session first used to leave a parked approval grant live. A
    late decision then resumed the run and overwrote the "cancelled" session
    with fresh events — work the owner believed they had stopped. A failed or
    raced run cancellation now fails closed: the session stays active. When
    the run had already reached a terminal state, the mirror carries *that*
    outcome (including its summary); a native work_cancelled would fabricate
    a cancellation over an honest completed/failed result.
    """
    session = await get_session(work_id, owner)
    if not session:
        return None
    if session.get("status") in TERMINAL:
        return session
    run_id = session.get("run_id")
    if not run_id:
        # Chat/routine work has no run to cancel, so a native settlement is
        # the only honest record. append_terminal_once makes it idempotent.
        await append_terminal_once(work_id, "work_cancelled", {})
        await settle_from_recorded_terminal_event(work_id)
        return await get_session(work_id, owner)

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

    # Mirror first: the run's own terminal event is the audit record, and its
    # payload carries the truthful summary. A late approval event in the same
    # replay cannot regress the session afterwards (see _append_event_in_conn).
    with suppress(Exception):
        await _mirror_run_events(work_id, owner, run_id)
    settled = await get_session(work_id, owner)
    if settled is not None and settled.get("status") in TERMINAL:
        return settled
    # An already-recorded terminal event can outlive its state transition.
    # Re-apply it instead of falling through with a still-running session.
    await settle_from_recorded_terminal_event(work_id)
    settled = await get_session(work_id, owner)
    if settled is not None and settled.get("status") in TERMINAL:
        return settled

    # Fallback for a mirror that could not complete. Settle from the run's
    # real terminal status, tagged with its event so a later mirror adopts
    # this event instead of appending a duplicate.
    current = await v2_mod.get_run(run_id, owner) or {}
    status = str(current.get("status") or "")
    report = current.get("report") or {}
    if status == "completed":
        event_type = "work_completed"
        payload: dict[str, Any] = {"summary": str(report.get("summary") or "")[:1000]}
    elif status == "failed":
        event_type = "work_failed"
        payload = {"error": str(report.get("error") or "run failed")[:500]}
    else:
        # A run that genuinely cancelled (or a missing run) is the only case
        # where work_cancelled is truthful here.
        event_type = "work_cancelled"
        payload = {"reason": "run cancelled" if status == "cancelled" else "run not found"}
        if cancelled or status == "cancelled":
            for event in reversed(await v2_mod.list_events(run_id, owner)):
                if event.get("event_type") == "run_cancelled":
                    payload.update({"_via_run": run_id,
                                    "_via_run_seq": event.get("seq")})
                    break
    await append_terminal_once(work_id, event_type, payload)
    await settle_from_recorded_terminal_event(work_id)
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
            if await append_terminal_once(session["id"], event_type, payload) is not None:
                settled += 1
    return settled
