"""Policy engine (plan §18): agent action -> allow / review / deny.

Replaces scattered approval checks with one evaluation point. Tool
classes drive the default decision; per-agent overrides tighten or
loosen it. Pure functions so they are trivially unit-testable.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any

from .tools.registry import (
    RISK_EXEC,
    RISK_NETWORK,
    RISK_READ,
    RISK_WRITE,
    TOOL_RISK_TIERS,
    risk_tier,
)

ALLOW = "allow"
REVIEW = "review"
DENY = "deny"

# Tool side-effect classes from plan §17.
READ = "read"
WRITE = "write"
EXECUTE = "execute"
EXTERNAL_SIDE_EFFECT = "external_side_effect"

# The registry classifies every known tool explicitly; policy maps those tiers
# onto its own approval classes. One source of truth for "what can this do",
# with the prefix heuristic below still covering custom/plugin tool names that
# the registry has never seen.
TIER_TO_CLASS: dict[str, str] = {
    RISK_READ: READ,
    RISK_WRITE: WRITE,
    RISK_EXEC: EXECUTE,
    RISK_NETWORK: EXTERNAL_SIDE_EFFECT,
}

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

# Hard-deny guardrails regardless of agent overrides. The name is the only
# input at this layer, so keep the list explicit and narrow: a policy that
# guesses from an argument can be talked around by prompt injection.
DENY_PATTERNS = ("push_to_main", "deploy_production", "delete_production", "exfiltrate")
DENY_TOOL_PREFIXES = ("delete_", "destroy_", "drop_", "wipe_", "format_", "rm_")

# Read-only network fetches return data, not effects. They are allowed even
# though the registry tier is NETWORK; the result is still wrapped as
# untrusted data by the agent loop.
ALLOW_TOOLS = frozenset({
    "request_approval",   # the approval mechanism itself
    "delegate_task",      # sub-agent tool calls are gated individually
    "write_workspace",    # sandbox file, reversible by a human
    "browser_screenshot", # writes a PNG under the sandbox screenshots dir
    "browser_navigate",   # navigation is a fetch until an input tool runs
    "fetch_url",
    "exa_search",
    "tavily_search",
    "firecrawl_scrape",
})

# Local, reversible state writes: memory and knowledge. `create_agent` is not
# here: it persists a new bot with an attacker-chosen prompt and tool list, so
# it is a privilege grant and needs a human decision.
ALLOW_WRITE_TOOLS = frozenset({
    "remember", "forget", "knowledge_save", "save_skill",
})



def tool_class(tool_name: str) -> str:
    """Side-effect class for a tool. Registry tiers win when the tool is
    known; the prefix heuristic is the fallback for anything else, so an
    unrecognised name is still classified rather than trusted."""
    name = (tool_name or "").lower()
    if name in TOOL_RISK_TIERS:
        return TIER_TO_CLASS.get(risk_tier(name), EXECUTE)
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


def canonical_args_hash(args: dict[str, Any] | None) -> str:
    """Stable hash of a tool call's arguments.

    Approval binding needs one identity for "the same call". Sorted keys and
    compact separators make ``{"a": 1, "b": 2}`` and ``{"b": 2, "a": 1}``
    hash the same; ``default=str`` keeps an exotic argument from making the
    gate unhashable (which would otherwise fail open or crash the turn).
    """
    payload = args if isinstance(args, dict) else {}
    try:
        encoded = json.dumps(
            payload, sort_keys=True, separators=(",", ":"), default=str,
        )
    except (TypeError, ValueError):
        encoded = str(sorted((str(k), str(v)) for k, v in payload.items()))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


class ToolPolicyError(PermissionError):
    """Base class for a tool call stopped by policy.

    Carries the decision and the approval row (when one was created) so the
    agent loop can audit the blocked call and stop the turn without parsing
    an error string.
    """

    def __init__(
        self,
        message: str,
        *,
        tool: str,
        decision: str,
        evaluation: dict[str, Any] | None = None,
        approval: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.tool = tool
        self.decision = decision
        self.evaluation = evaluation or {}
        self.approval = approval


class PolicyReviewRequired(ToolPolicyError):
    """The call needs a human grant before it may run."""


class PolicyDenied(ToolPolicyError):
    """The call is permanently refused for this agent."""


def evaluate_tool(
    tool: str,
    args: dict[str, Any] | None = None,
    *,
    agent: str = "",
    overrides: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Policy verdict for one concrete tool call.

    `evaluate` answers a declarative action; execution needs the same three
    outcomes with a tool-name hard deny and a short allow-list for reversible
    local work. Everything unrecognised stays REVIEW, so a new custom or
    plugin tool is never trusted just because nobody classified it.
    """
    name = (tool or "").strip()
    lowered = name.lower()
    cls = tool_class(name)
    evaluation = evaluate(
        agent=agent, tool=name, target="", action_type="",
        risk="medium", overrides=overrides,
    )
    evaluation["tool_class"] = cls
    if any(lowered.startswith(prefix) for prefix in DENY_TOOL_PREFIXES):
        evaluation["decision"] = DENY
        evaluation["risk"] = "high"
        evaluation["reasons"] = ["tool name matches a destructive prefix"]
        return evaluation
    if lowered in ALLOW_TOOLS or lowered in ALLOW_WRITE_TOOLS:
        # An explicit agent-level deny (or a guardrail deny) outranks the
        # built-in allow-list: the list names reversible defaults, not a
        # promise to ignore a stricter rule for this agent.
        if evaluation["decision"] != DENY:
            evaluation["decision"] = ALLOW
            evaluation["reasons"] = [f"{lowered} is a reversible local action"]
        return evaluation
    if lowered not in TOOL_RISK_TIERS and evaluation["decision"] != DENY:
        # A custom or plugin tool is not classified by its name: an arbitrary
        # `get_price_feed` can still perform outbound HTTP. Unknown behavior is
        # never trusted without a human decision, regardless of the prefix.
        evaluation["decision"] = REVIEW
        evaluation["reasons"] = ["unrecognised tool defaults to review"]
    return evaluation



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
