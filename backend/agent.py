"""
swarm.agent — the AI teammates in the room.

Phase 0: one hardcoded persona, no tools.
Phase 2: tool calling (read-only shell, channel history search).
Phase 4: multiple personas, loaded from the `agents` table.
Phase 5: every call traced through Langfuse if configured, silently
         skipped if not — this module must work with zero env vars set
         beyond GROQ_API_KEY.
"""
from __future__ import annotations

import os
import re
import subprocess
import time
from typing import Any

from . import db

HISTORY_WINDOW = 12
MAX_TOOL_CALLS = 3
SHELL_TIMEOUT_SECONDS = 10
SHELL_OUTPUT_CAP = 4000
SANDBOX_DIR = os.environ.get("SWARM_SANDBOX_DIR", "/tmp/swarm-sandbox")

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "read_only_shell",
            "description": (
                "Run a read-only shell command inside a sandboxed working "
                "directory. Use for listing files, reading file contents, "
                "grepping, checking state — never for writes."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {"type": "string", "description": "the shell command to run"}
                },
                "required": ["command"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_channel_history",
            "description": "Keyword-search this channel's message history.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "keyword or phrase to search for"}
                },
                "required": ["query"],
            },
        },
    },
]


def find_mentioned_agents(body: str, agents: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Returns agents mentioned in body, ordered by position of first
    mention in the text (not by agent registration order)."""
    hits: list[tuple[int, dict[str, Any]]] = []
    lowered = body.lower()
    for a in agents:
        match = re.search(rf"@{re.escape(a['name'].lower())}\b", lowered)
        if match:
            hits.append((match.start(), a))
    hits.sort(key=lambda h: h[0])
    return [a for _, a in hits]


def _groq_client():
    from groq import Groq

    api_key = os.environ.get("GROQ_API_KEY")
    if not api_key:
        raise RuntimeError("GROQ_API_KEY is not set. Export it or put it in backend/.env")
    return Groq(api_key=api_key)


def _langfuse_client():
    if not os.environ.get("LANGFUSE_PUBLIC_KEY") or not os.environ.get("LANGFUSE_SECRET_KEY"):
        return None
    try:
        from langfuse import Langfuse

        return Langfuse()
    except Exception:  # noqa: BLE001 — tracing is best-effort, never blocks the agent
        return None


def _run_shell_tool(command: str) -> str:
    os.makedirs(SANDBOX_DIR, exist_ok=True)
    try:
        result = subprocess.run(
            command,
            shell=True,
            cwd=SANDBOX_DIR,
            capture_output=True,
            text=True,
            timeout=SHELL_TIMEOUT_SECONDS,
            env={"PATH": "/usr/bin:/bin"},  # minimal env; not a real network-isolation guarantee
        )
        output = (result.stdout or "") + (result.stderr or "")
        return output[:SHELL_OUTPUT_CAP] or "(no output)"
    except subprocess.TimeoutExpired:
        return f"(timed out after {SHELL_TIMEOUT_SECONDS}s)"
    except Exception as exc:  # noqa: BLE001
        return f"(shell error: {exc})"


async def _run_search_tool(query: str, channel_id: str) -> str:
    rows = await db.search_history(channel_id, query, limit=10)
    if not rows:
        return "(no matches)"
    lines = [f"[{r['author']}] {r['body'][:200]}" for r in rows]
    return "\n".join(lines)


def _build_messages(system_prompt: str, history: list[dict[str, Any]]) -> list[dict[str, Any]]:
    messages = [{"role": "system", "content": system_prompt}]
    for m in history[-HISTORY_WINDOW:]:
        if m["author_kind"] == "system":
            continue  # tool-call audit messages aren't part of the model's own context
        role = "assistant" if m["author_kind"] == "agent" else "user"
        prefix = "" if role == "assistant" else f"{m['author']}: "
        messages.append({"role": role, "content": f"{prefix}{m['body']}"})
    return messages


async def generate_reply(
    agent_row: dict[str, Any], channel_id: str, history: list[dict[str, Any]]
) -> dict[str, Any]:
    """Returns {"reply": str, "tool_events": [{"tool", "args", "result"}], "usage": {...}}.
    tool_events is populated in order — caller (main.py) persists each as
    a system message before posting the final reply, per FR2.2."""
    name = agent_row["name"]
    model = agent_row["model"]
    messages = _build_messages(agent_row["system_prompt"], history)

    langfuse = _langfuse_client()
    trace = None
    if langfuse is not None:
        try:
            trace = langfuse.trace(
                name=f"swarm.agent.{name}",
                metadata={"channel_id": channel_id, "agent": name},
            )
        except Exception:  # noqa: BLE001
            trace = None

    tool_events: list[dict[str, Any]] = []
    usage_total = {"prompt_tokens": 0, "completion_tokens": 0}

    try:
        client = _groq_client()

        for _ in range(MAX_TOOL_CALLS + 1):
            start = time.time()
            resp = client.chat.completions.create(
                model=model, messages=messages, tools=TOOLS, tool_choice="auto",
                temperature=0.4, max_tokens=600,
            )
            latency = time.time() - start
            choice = resp.choices[0].message
            usage = getattr(resp, "usage", None)
            if usage:
                usage_total["prompt_tokens"] += getattr(usage, "prompt_tokens", 0) or 0
                usage_total["completion_tokens"] += getattr(usage, "completion_tokens", 0) or 0

            if trace is not None:
                try:
                    trace.generation(
                        name="groq-completion", model=model, input=messages,
                        output=choice.content, usage=usage_total,
                        metadata={"latency_s": latency},
                    )
                except Exception:  # noqa: BLE001
                    pass

            if not choice.tool_calls:
                reply = (choice.content or "").strip() or "(empty reply)"
                return {"reply": reply, "tool_events": tool_events, "usage": usage_total}

            if len(tool_events) >= MAX_TOOL_CALLS:
                return {
                    "reply": f"hit the {MAX_TOOL_CALLS}-tool-call cap for this reply, stopping here.",
                    "tool_events": tool_events, "usage": usage_total,
                }

            messages.append({
                "role": "assistant", "content": choice.content or "",
                "tool_calls": [tc.model_dump() for tc in choice.tool_calls],
            })

            for tc in choice.tool_calls:
                import json as _json
                args = _json.loads(tc.function.arguments or "{}")
                if tc.function.name == "read_only_shell":
                    result = _run_shell_tool(args.get("command", ""))
                elif tc.function.name == "search_channel_history":
                    result = await _run_search_tool(args.get("query", ""), channel_id)
                else:
                    result = f"(unknown tool {tc.function.name})"

                tool_events.append({"tool": tc.function.name, "args": args, "result": result})
                messages.append({
                    "role": "tool", "tool_call_id": tc.id, "content": result,
                })

        return {
            "reply": "(gave up after too many tool-call rounds)",
            "tool_events": tool_events, "usage": usage_total,
        }

    except Exception as exc:  # noqa: BLE001 — surface it in-channel, never crash the relay
        return {"reply": f"[agent error: {exc}]", "tool_events": tool_events, "usage": usage_total}
