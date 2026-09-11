"""Explicit durable work state machine (plan §11).

Legacy run statuses (queued/running/waiting_for_approval/completed/failed/
cancelled) remain the persisted values. This module adds the richer
execution vocabulary on top so the UI can render Goal → Plan →
Current step → Evidence → Decision → Result without inferring semantics
from raw events.
"""
from __future__ import annotations

import asyncio
import hashlib
import time
from typing import Any

# Canonical display states. Persisted statuses map onto these.
QUEUED = "queued"
PLANNING = "planning"
EXECUTING = "executing"
TOOL_CALL = "tool_call"
WAITING_APPROVAL = "waiting_for_approval"
WAITING_INPUT = "waiting_for_input"
HANDOFF = "handoff"
RETRYING = "retrying"
VERIFYING = "verifying"
COMPLETED = "completed"
FAILED = "failed"
CANCELLED = "cancelled"

TERMINAL = frozenset({COMPLETED, FAILED, CANCELLED})

# Allowed transitions for the display machine. Persisted layer only
# enforces terminal-immutability; this table drives UI + tests.
TRANSITIONS: dict[str, frozenset[str]] = {
    QUEUED: frozenset({PLANNING, EXECUTING, CANCELLED}),
    PLANNING: frozenset({EXECUTING, WAITING_INPUT, FAILED, CANCELLED}),
    EXECUTING: frozenset({TOOL_CALL, HANDOFF, WAITING_APPROVAL, WAITING_INPUT, VERIFYING, RETRYING, COMPLETED, FAILED, CANCELLED}),
    TOOL_CALL: frozenset({EXECUTING, RETRYING, WAITING_APPROVAL, FAILED}),
    HANDOFF: frozenset({EXECUTING, WAITING_INPUT}),
    RETRYING: frozenset({EXECUTING, TOOL_CALL, FAILED}),
    WAITING_APPROVAL: frozenset({EXECUTING, CANCELLED}),
    WAITING_INPUT: frozenset({EXECUTING, PLANNING, CANCELLED}),
    VERIFYING: frozenset({COMPLETED, EXECUTING, FAILED}),
    COMPLETED: frozenset(),
    FAILED: frozenset(),
    CANCELLED: frozenset(),
}

# Map raw run_events event_type -> display state hint.
EVENT_TO_STATE: dict[str, str] = {
    "run_queued": QUEUED,
    "work_queued": QUEUED,
    "run_started": EXECUTING,
    "work_started": EXECUTING,
    "plan_proposed": PLANNING,
    "step_started": EXECUTING,
    "tool_started": TOOL_CALL,
    "agent_started": EXECUTING,
    "tool_finished": EXECUTING,
    "step_completed": EXECUTING,
    "handoff": HANDOFF,
    "step_retried": RETRYING,
    "approval_requested": WAITING_APPROVAL,
    "approval_resolved": EXECUTING,
    "input_requested": WAITING_INPUT,
    "verification_started": VERIFYING,
    "verification_passed": VERIFYING,
    "run_completed": COMPLETED,
    "work_completed": COMPLETED,
    "run_failed": FAILED,
    "work_failed": FAILED,
    "run_cancelled": CANCELLED,
    "work_cancelled": CANCELLED,
    "run_denied": CANCELLED,
}


def can_transition(from_state: str, to_state: str) -> bool:
    return to_state in TRANSITIONS.get(from_state, frozenset())


def derive_state(events: list[dict[str, Any]], persisted_status: str = QUEUED) -> str:
    """Fold an event log into a single display state."""
    state = persisted_status if persisted_status in TRANSITIONS else QUEUED
    if persisted_status == "running":
        state = EXECUTING
    for event in events:
        hint = EVENT_TO_STATE.get(str(event.get("event_type") or event.get("type") or ""))
        if hint and hint != state and can_transition(state, hint):
            state = hint
        elif hint and hint in TERMINAL:
            state = hint
    return state


def summarize_progress(events: list[dict[str, Any]]) -> dict[str, Any]:
    """Convert raw events into Goal → Plan → Evidence → Result model."""
    plan, evidence, decisions, failures = [], [], [], []
    current_step = None
    for event in events:
        etype = str(event.get("event_type") or event.get("type") or "")
        payload = event.get("payload") or {}
        if etype in ("plan_proposed", "step_started"):
            step = str(payload.get("step") or payload.get("goal") or event.get("step_id") or "")
            if step and step not in plan:
                plan.append(step)
            if etype == "step_started":
                current_step = step or event.get("step_id")
        elif etype in ("tool_finished", "step_completed", "source_linked"):
            evidence.append({"type": etype, "step_id": event.get("step_id"), "payload": payload})
        elif etype in ("approval_requested", "approval_resolved", "handoff"):
            decisions.append({"type": etype, "payload": payload})
        elif etype in ("run_failed", "work_failed", "step_failed"):
            failures.append({"payload": payload})
    return {
        "plan": plan,
        "current_step": current_step,
        "evidence_count": len(evidence),
        "evidence": evidence[-25:],
        "decisions": decisions[-25:],
        "failures": failures[-10:],
    }


def build_result_contract(
    *,
    status: str = COMPLETED,
    summary: str = "",
    evidence: list[dict[str, Any]] | None = None,
    artifacts: list[dict[str, Any]] | None = None,
    verification: list[dict[str, Any]] | None = None,
    warnings: list[str] | None = None,
    confidence: float = 0.0,
    needs_human_review: bool = False,
) -> dict[str, Any]:
    """Standard result envelope from plan §13."""
    return {
        "status": status,
        "summary": summary,
        "evidence": evidence or [],
        "artifacts": artifacts or [],
        "verification": verification or [],
        "warnings": warnings or [],
        "confidence": max(0.0, min(1.0, float(confidence))),
        "needs_human_review": bool(needs_human_review),
    }


def idempotency_key(*parts: str) -> str:
    digest = hashlib.sha256("|".join(parts).encode()).hexdigest()
    return f"idem_{digest[:24]}"


_loop_locks: dict[int, tuple[asyncio.AbstractEventLoop, asyncio.Lock]] = {}


def event_lock() -> asyncio.Lock:
    """Return the event-append lock for the calling loop.

    A module-global ``asyncio.Lock`` binds to the first loop that contends
    it and then raises "bound to a different event loop" after a server
    reload (or across TestClient portals) — and a task cancelled while
    holding it poisons every later loop. Per-loop locks keep in-loop
    mutual exclusion for seq assignment without poisoning later loops;
    entries for closed loops are pruned on access.
    """
    loop = asyncio.get_running_loop()
    key = id(loop)
    entry = _loop_locks.get(key)
    if entry is not None and entry[0] is loop:
        return entry[1]
    for dead in [k for k, (lp, _) in _loop_locks.items() if lp.is_closed()]:
        _loop_locks.pop(dead, None)
    lock = asyncio.Lock()
    _loop_locks[key] = (loop, lock)
    return lock


def heartbeat(now: float | None = None) -> dict[str, Any]:
    return {"ts": now if now is not None else time.time()}
