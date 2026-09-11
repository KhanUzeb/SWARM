"""Task-aware model routing (plan §14).

Classifies the task (coding / research / chat), then picks the first
connected provider that satisfies the task's needs. Heuristic and
stateless by design: telemetry-weighted routing can replace
``classify``/``score`` without changing the endpoint contract.
"""
from __future__ import annotations

from typing import Any

CODING = "coding"
RESEARCH = "research"
CHAT = "chat"

TASK_TYPES = (CODING, RESEARCH, CHAT)

_CODING_HINTS = (
    "code", "bug", "fix", "refactor", "test", "debug", "implement",
    "deploy", "benchmark", "latency", "stack trace", "error", "api",
    "repo", "commit", "pr ", "pull request", "function", "class",
)

_RESEARCH_HINTS = (
    "research", "report", "analyze", "analyse", "compare", "survey",
    "sources", "evidence", "investigate", "landscape", "competitor",
    "market", "brief", "summary of", "what happened",
)


def classify(objective: str) -> dict[str, Any]:
    """Classify an objective. Returns {task_type, confidence, reasons}."""
    text = (objective or "").lower()
    coding_hits = [h for h in _CODING_HINTS if h in text]
    research_hits = [h for h in _RESEARCH_HINTS if h in text]
    if coding_hits and len(coding_hits) >= len(research_hits):
        return {"task_type": CODING, "confidence": 0.7,
                "reasons": [f"matched code signals: {', '.join(coding_hits[:3])}"]}
    if research_hits:
        return {"task_type": RESEARCH, "confidence": 0.7,
                "reasons": [f"matched research signals: {', '.join(research_hits[:3])}"]}
    if coding_hits:
        return {"task_type": CODING, "confidence": 0.55,
                "reasons": [f"matched code signals: {', '.join(coding_hits[:3])}"]}
    return {"task_type": CHAT, "confidence": 0.5, "reasons": ["no strong signals; default chat"]}


def needs_tools(task_type: str) -> bool:
    return task_type in (CODING, RESEARCH)


async def route(objective: str) -> dict[str, Any]:
    """Pick a connected provider/model for the objective.

    Prefers tool-capable providers for coding/research; any connected
    provider for chat. Never raises for 'no provider' — callers get a
    structured fallback instead.
    """
    from .ai_support.providers import get_provider, providers_by_priority
    from .ai_support.resolver import resolve_runtime_auth

    classification = classify(objective)
    task_type = classification["task_type"]
    for spec in providers_by_priority():
        auth = await resolve_runtime_auth(spec["id"])
        if auth is None:
            continue
        tool_ok = (spec.get("kind") == "openai_compatible") or spec["id"] == "anthropic"
        if needs_tools(task_type) and not tool_ok:
            continue
        model = auth.default_model or spec.get("default_model")
        matched = dict(spec)
        matched["runtime_model"] = model
        return {
            "task_type": task_type,
            "confidence": classification["confidence"],
            "reasons": classification["reasons"]
            + [f"selected connected provider {spec['id']} (tools: {tool_ok})"],
            "provider_id": spec["id"],
            "provider_name": spec.get("name") or spec["id"],
            "model": model,
            "requires_tools": needs_tools(task_type),
        }
    provider = get_provider("groq") or {}
    return {
        "task_type": task_type,
        "confidence": classification["confidence"],
        "reasons": classification["reasons"] + ["no connected provider; configure one first"],
        "provider_id": None,
        "provider_name": None,
        "model": provider.get("default_model"),
        "requires_tools": needs_tools(task_type),
    }
