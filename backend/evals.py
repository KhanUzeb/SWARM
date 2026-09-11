"""Evaluation harness foundation (plan §22-23).

Small, dependency-free benchmark runner: task registry with category
tags, metric computation over run event logs, and architecture
comparison (A/B/C/D/E from the plan). Persisted runs are optional;
the pure scoring helpers are what tests pin.
"""
from __future__ import annotations

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
    if category not in CATEGORIES:
        raise ValueError(f"unknown eval category: {category}")
    task = {"id": task_id, "category": category, "prompt": prompt, "checks": checks or []}
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
    tool_calls = sum(1 for t in types if t in ("tool_started", "tool_finished"))
    retries = sum(1 for t in types if "retr" in t)
    approvals = sum(1 for t in types if t == "approval_requested")
    verifications = [e for e in events if str(e.get("event_type") or e.get("type") or "").startswith("verification")]
    terminal = next((t for t in reversed(types) if t in (
        "run_completed", "work_completed", "run_failed", "work_failed",
        "run_cancelled", "work_cancelled", "run_denied")), "")
    success = terminal in ("run_completed", "work_completed")
    verified = success and any(
        str(e.get("event_type") or "") == "verification_passed" for e in events)
    duration = 0.0
    stamps = [float(e.get("created_at") or 0) for e in events if e.get("created_at")]
    if len(stamps) >= 2:
        duration = max(0.0, max(stamps) - min(stamps))
    effort = max(0, min(4, int(human_effort)))
    return {
        "task_success": success,
        "verified_success": verified,
        "first_attempt_success": success and retries == 0,
        "retries": retries,
        "human_interventions": approvals,
        "human_effort": effort,
        "human_effort_label": HUMAN_EFFORT_SCALE[effort],
        "time_seconds": duration,
        "tool_calls": tool_calls,
        "verification_steps": len(verifications),
        "terminal": terminal,
    }


def compare_architectures(results: dict[str, list[dict[str, Any]]]) -> dict[str, Any]:
    """Aggregate per-architecture score dicts into a comparison table."""
    table = {}
    for arch, runs in results.items():
        if arch not in ARCHITECTURES:
            raise ValueError(f"unknown architecture: {arch}")
        n = max(1, len(runs))
        table[arch] = {
            "n": len(runs),
            "success_rate": sum(1 for r in runs if r.get("task_success")) / n,
            "verified_rate": sum(1 for r in runs if r.get("verified_success")) / n,
            "avg_effort": sum(float(r.get("human_effort", 0)) for r in runs) / n,
            "avg_tool_calls": sum(float(r.get("tool_calls", 0)) for r in runs) / n,
        }
    return table
