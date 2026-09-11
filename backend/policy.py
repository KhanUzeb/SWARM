"""Policy engine (plan §18): agent action -> allow / review / deny.

Replaces scattered approval checks with one evaluation point. Tool
classes drive the default decision; per-agent overrides tighten or
loosen it. Pure functions so they are trivially unit-testable.
"""
from __future__ import annotations

from typing import Any

ALLOW = "allow"
REVIEW = "review"
DENY = "deny"

# Tool side-effect classes from plan §17.
READ = "read"
WRITE = "write"
EXECUTE = "execute"
EXTERNAL_SIDE_EFFECT = "external_side_effect"

CLASS_BY_TOOL_PREFIX: dict[str, str] = {
    "search": READ,
    "read": READ,
    "list": READ,
    "get": READ,
    "inspect": READ,
    "benchmark_read": READ,
    "edit": WRITE,
    "write": WRITE,
    "patch": WRITE,
    "delete": EXTERNAL_SIDE_EFFECT,
    "push": EXTERNAL_SIDE_EFFECT,
    "deploy": EXTERNAL_SIDE_EFFECT,
    "send": EXTERNAL_SIDE_EFFECT,
    "publish": EXTERNAL_SIDE_EFFECT,
    "run": EXECUTE,
    "exec": EXECUTE,
    "test": EXECUTE,
    "benchmark": EXECUTE,
}

DEFAULT_DECISION_BY_CLASS: dict[str, str] = {
    READ: ALLOW,
    WRITE: REVIEW,
    EXECUTE: REVIEW,
    EXTERNAL_SIDE_EFFECT: REVIEW,
}

# Hard-deny guardrails regardless of agent overrides.
DENY_PATTERNS = ("push_to_main", "deploy_production", "delete_production", "exfiltrate")


def tool_class(tool_name: str) -> str:
    name = (tool_name or "").lower()
    for prefix, cls in CLASS_BY_TOOL_PREFIX.items():
        if name.startswith(prefix):
            return cls
    return EXECUTE


def evaluate(
    *,
    agent: str = "",
    tool: str = "",
    target: str = "",
    action_type: str = "",
    risk: str = "medium",
    overrides: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Evaluate one action. Returns {decision, reasons, tool_class}."""
    reasons: list[str] = []
    cls = tool_class(tool or action_type)
    haystack = f"{tool} {target} {action_type}".lower()
    if any(pattern in haystack for pattern in DENY_PATTERNS):
        return {
            "decision": DENY,
            "tool_class": cls,
            "reasons": ["matches a hard-deny guardrail pattern"],
            "risk": "high",
        }
    decision = DEFAULT_DECISION_BY_CLASS.get(cls, REVIEW)
    reasons.append(f"tool class {cls} defaults to {decision}")
    agent_rules = (overrides or {}).get(agent, {}) if overrides else {}
    if tool and tool in agent_rules:
        decision = str(agent_rules[tool])
        reasons.append(f"agent override for {tool}: {decision}")
    if risk == "high" and decision == ALLOW:
        decision = REVIEW
        reasons.append("high risk escalates allow -> review")
    if risk == "low" and cls == READ:
        decision = ALLOW
    return {"decision": decision, "tool_class": cls, "reasons": reasons, "risk": risk}


def approval_card(action: dict[str, Any], evaluation: dict[str, Any]) -> dict[str, Any]:
    """Shape an approval request with consequence, scope, reversibility."""
    return {
        "title": str(action.get("title") or action.get("tool") or "Approval requested"),
        "impact": action.get("impact") or [],
        "verification": action.get("verification") or [],
        "risk": evaluation.get("risk", "medium"),
        "reversible": bool(action.get("reversible", False)),
        "scope": action.get("scope") or {},
        "decision": evaluation.get("decision", REVIEW),
    }
