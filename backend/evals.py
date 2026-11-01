"""Evaluation harness foundation (plan §22-23).

Small, dependency-free benchmark runner: task registry with category
tags, metric computation over run event logs, and architecture
comparison (A/B/C/D/E from the plan). Persisted runs are optional;
the pure scoring helpers are what tests pin.
"""
from __future__ import annotations

import re
from typing import Any

CATEGORIES = (
    "coding", "research", "browser", "file_manipulation", "planning",
    "multi_agent_delegation", "knowledge_retrieval", "routine_execution",
    "approval_workflow",
)

ARCHITECTURES = (
    "single_chat", "chat_plus_tools", "multi_agent",
    "multi_agent_memory", "multi_agent_memory_verification",
)

HUMAN_EFFORT_SCALE = {
    0: "fully autonomous",
    1: "one-click approval",
    2: "clarification required",
    3: "manual correction",
    4: "human completes substantial work",
}

_TASKS: list[dict[str, Any]] = []


def register_task(task_id: str, category: str, prompt: str, checks: list[str] | None = None) -> dict[str, Any]:
    task_id = task_id.strip()
    prompt = prompt.strip()
    checks = checks or []
    if not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_.-]{0,79}", task_id):
        raise ValueError("task id must be 1-80 letters, numbers, dots, underscores or hyphens")
    if category not in CATEGORIES:
        raise ValueError(f"unknown eval category: {category}")
    if not prompt or len(prompt) > 4000:
        raise ValueError("task prompt must be between 1 and 4000 characters")
    if len(checks) > 20 or any(not isinstance(check, str) or not check.strip() for check in checks):
        raise ValueError("checks must contain at most 20 non-empty strings")
    task = {"id": task_id, "category": category, "prompt": prompt, "checks": [check.strip() for check in checks]}
    _TASKS[:] = [t for t in _TASKS if t["id"] != task_id]
    _TASKS.append(task)
    return task


def list_tasks(category: str | None = None) -> list[dict[str, Any]]:
    if category and category not in CATEGORIES:
        raise ValueError(f"unknown eval category: {category}")
    return [t for t in _TASKS if not category or t["category"] == category]


def score_run(events: list[dict[str, Any]], human_effort: int = 0) -> dict[str, Any]:
    """Compute plan §22 metrics from an event log."""
    types = [str(e.get("event_type") or e.get("type") or "") for e in events]
    # Work sessions record both sides of each tool call. Count the start when
    # lifecycle events are present, and use finishes only for older/sparse
    # traces that did not record starts.
    tool_calls = sum(1 for t in types if t == "tool_started")
    if tool_calls == 0:
        tool_calls = sum(1 for t in types if t == "tool_finished")
    retries = sum(1 for t in types if "retr" in t)
    approvals = sum(1 for t in types if t == "approval_requested")
    verifications = [
        t for t in types if t.startswith("verification")
    ]
    terminal = next((t for t in reversed(types) if t in (
        "run_completed", "work_completed", "run_failed", "work_failed",
        "run_cancelled", "work_cancelled", "run_denied")), "")
    success = terminal in ("run_completed", "work_completed")
    # The latest verification decision is authoritative: a later failed review
    # must not be hidden by an earlier pass in the same event log.
    verification_result = next(
        (t for t in reversed(verifications)
         if t in ("verification_passed", "verification_failed")),
        "",
    )
    verified = success and verification_result == "verification_passed"
    duration = 0.0
    stamps = [float(e.get("created_at") or 0) for e in events if e.get("created_at")]
    if len(stamps) >= 2:
        duration = max(0.0, max(stamps) - min(stamps))
    effort = max(0, min(4, int(human_effort)))
    return {
        "task_success": success,
        "outcome_known": bool(terminal),
        "verified_success": verified,
        "first_attempt_success": success and retries == 0,
        "retries": retries,
        "human_interventions": approvals,
        "human_effort": effort,
        "human_effort_label": HUMAN_EFFORT_SCALE[effort],
        "time_seconds": duration,
        "tool_calls": tool_calls,
        "verification_steps": len(verifications),
        "verification_result": verification_result or None,
        "terminal": terminal,
    }


def compare_architectures(results: dict[str, list[dict[str, Any]]]) -> dict[str, Any]:
    """Aggregate per-architecture score dicts into a comparison table."""
    table = {}
    for arch, runs in results.items():
        if arch not in ARCHITECTURES:
            raise ValueError(f"unknown architecture: {arch}")
        observed = [r for r in runs if r.get("outcome_known", True)]
        denominator = max(1, len(observed))
        table[arch] = {
            "n": len(runs),
            "observed": len(observed),
            "success_rate": sum(1 for r in observed if r.get("task_success")) / denominator,
            "verified_rate": sum(1 for r in observed if r.get("verified_success")) / denominator,
            "avg_effort": sum(float(r.get("human_effort", 0)) for r in observed) / denominator,
            "avg_tool_calls": sum(float(r.get("tool_calls", 0)) for r in observed) / denominator,
        }
    return table
