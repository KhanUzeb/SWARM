"""
swarm.agent — the AI teammates in the room.

Phase 0: one hardcoded persona, no tools.
Phase 2: tool calling (read-only shell, channel history search).
Phase 4: multiple personas, loaded from the `agents` table.
Phase 5: every call traced through Langfuse if configured, silently
         skipped if not — this module must work with zero env vars set
         beyond GROQ_API_KEY.
Phase 8: AsyncGroq; tool-call rounds non-streaming; final text streamed.
"""
from __future__ import annotations

import json as _json
import os
import re
import subprocess
import time
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from . import db

HISTORY_WINDOW = 12
MAX_TOOL_CALLS = 3
SHELL_TIMEOUT_SECONDS = 10
SHELL_OUTPUT_CAP = 4000


def _default_sandbox_dir() -> str:
    override = os.environ.get("SWARM_SANDBOX_DIR")
    if override:
        return override
    if os.name == "nt":
        base = os.environ.get("TEMP") or os.environ.get("LOCALAPPDATA") or r"C:\Temp"
        return str(Path(base) / "swarm-sandbox")
    return "/tmp/swarm-sandbox"


SANDBOX_DIR = _default_sandbox_dir()

# Small Groq models (e.g. llama-3.1-8b-instant) call tools on "hi" unless
# we withhold the tool schema. Only offer tools when the last human message
# actually looks like a file/history request.
_TOOL_HINT = re.compile(
    r"\b("
    r"ls|dir|cat|grep|findstr|find|files?|folder|directory|sandbox|shell|cwd|"
    r"inspect|look\s+up|search(?:\s+the)?\s+history|earlier\s+messages?|"
    r"what\s+did\s+we|last\s+time"
    r")\b",
    re.I,
)
_TOOL_POLICY = (
    "Tools: only use read_only_shell or search_channel_history when the user "
    "explicitly asks to inspect files or search older channel history. "
    "Greetings and normal chat get a short text reply — no tools."
)

OnToolsReady = Callable[[list[dict[str, Any]]], Awaitable[None]]
OnStreamStart = Callable[[], Awaitable[None]]
OnToken = Callable[[str], Awaitable[None]]

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "read_only_shell",
            "description": (
                "Run a read-only shell command inside a sandboxed working "
                "directory. Use only when the user asks to list/read/grep files. "
                "On Windows prefer dir / findstr, not ls / grep. Never for writes "
                "or greetings."
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
            "description": (
                "Keyword-search this channel's older message history. "
                "Use only when the user asks about past messages beyond "
                "what's already in context. Not for greetings."
            ),
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


def should_offer_tools(history: list[dict[str, Any]]) -> bool:
    """True only if the latest human message looks like a tool request."""
    for m in reversed(history):
        if m.get("author_kind") == "human":
            return bool(_TOOL_HINT.search(m.get("body") or ""))
    return False


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
    from groq import AsyncGroq

    api_key = (os.environ.get("GROQ_API_KEY") or "").strip().strip('"').strip("'")
    if not api_key:
        raise RuntimeError("GROQ_API_KEY is not set. Export it or put it in .env / backend/.env")
    return AsyncGroq(api_key=api_key)


def _langfuse_client():
    if not os.environ.get("LANGFUSE_PUBLIC_KEY") or not os.environ.get("LANGFUSE_SECRET_KEY"):
        return None
    try:
        from langfuse import Langfuse

        return Langfuse()
    except Exception:  # noqa: BLE001 — tracing is best-effort, never blocks the agent
        return None


def _shell_env() -> dict[str, str]:
    """Minimal PATH. Not a real isolation boundary — just keep the sandbox small."""
    if os.name == "nt":
        windir = os.environ.get("WINDIR", r"C:\Windows")
        system32 = str(Path(windir) / "System32")
        path_parts = [system32, windir, str(Path(system32) / "Wbem")]
        git_bin = Path(r"C:\Program Files\Git\usr\bin")
        if git_bin.is_dir():
            path_parts.append(str(git_bin))
        return {
            "PATH": os.pathsep.join(path_parts),
            "COMSPEC": os.environ.get("COMSPEC", str(Path(system32) / "cmd.exe")),
            "SYSTEMROOT": windir,
        }
    return {"PATH": "/usr/bin:/bin"}


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
            env=_shell_env(),
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
    messages = [{"role": "system", "content": f"{system_prompt}\n\n{_TOOL_POLICY}"}]
    for m in history[-HISTORY_WINDOW:]:
        if m["author_kind"] == "system":
            continue  # tool-call audit messages aren't part of the model's own context
        role = "assistant" if m["author_kind"] == "agent" else "user"
        prefix = "" if role == "assistant" else f"{m['author']}: "
        messages.append({"role": role, "content": f"{prefix}{m['body']}"})
    return messages


def _usage_from(usage: Any, bucket: dict[str, int]) -> None:
    if not usage:
        return
    bucket["prompt_tokens"] += getattr(usage, "prompt_tokens", 0) or 0
    bucket["completion_tokens"] += getattr(usage, "completion_tokens", 0) or 0


async def _complete_stream(
    client: Any,
    model: str,
    messages: list[dict[str, Any]],
    on_stream_start: OnStreamStart | None,
    on_token: OnToken | None,
    *,
    use_tools: bool = True,
) -> tuple[str, list[dict[str, Any]], Any]:
    """Stream one completion. If the model emits tool calls, tokens are not
    forwarded and assembled tool_calls are returned. Otherwise tokens are
    forwarded live and content is returned."""
    kwargs: dict[str, Any] = dict(
        model=model, messages=messages, temperature=0.4, max_tokens=600, stream=True,
    )
    if use_tools:
        kwargs["tools"] = TOOLS
        kwargs["tool_choice"] = "auto"
    stream = await client.chat.completions.create(**kwargs)
    content = ""
    tool_acc: dict[int, dict[str, str]] = {}
    saw_tools = False
    started = False
    usage = None

    async for chunk in stream:
        if getattr(chunk, "usage", None):
            usage = chunk.usage
        if not chunk.choices:
            continue
        delta = chunk.choices[0].delta
        if delta is None:
            continue
        if delta.tool_calls:
            saw_tools = True
            for tc in delta.tool_calls:
                idx = tc.index if tc.index is not None else 0
                slot = tool_acc.setdefault(idx, {"id": "", "name": "", "arguments": ""})
                if tc.id:
                    slot["id"] = tc.id
                fn = tc.function
                if fn is not None:
                    if fn.name:
                        slot["name"] += fn.name
                    if fn.arguments:
                        slot["arguments"] += fn.arguments
        elif delta.content and not saw_tools:
            if not started:
                if on_stream_start is not None:
                    await on_stream_start()
                started = True
            content += delta.content
            if on_token is not None:
                await on_token(delta.content)

    tool_calls = [
        {"id": slot["id"], "name": slot["name"], "arguments": slot["arguments"]}
        for _, slot in sorted(tool_acc.items())
        if slot["name"]
    ]
    return content, tool_calls, usage


async def generate_reply(
    agent_row: dict[str, Any],
    channel_id: str,
    history: list[dict[str, Any]],
    on_tools_ready: OnToolsReady | None = None,
    on_stream_start: OnStreamStart | None = None,
    on_token: OnToken | None = None,
) -> dict[str, Any]:
    """Returns {"reply": str, "tool_events": [{"tool", "args", "result"}], "usage": {...}}.
    tool_events is populated in order — caller (main.py) persists each as
    a system message before posting the final reply, per FR2.2.

    Each Groq round is streamed. Tool-call rounds accumulate tool_calls
    and do not forward tokens. The first content round forwards tokens
    live; on_tools_ready is invoked before the first streamed token so
    audit messages land first."""
    name = agent_row["name"]
    model = os.environ.get("SWARM_AGENT_MODEL") or agent_row["model"]
    messages = _build_messages(agent_row["system_prompt"], history)
    use_tools = should_offer_tools(history)

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
    tools_announced = False

    async def announce_tools() -> None:
        nonlocal tools_announced
        if tools_announced or on_tools_ready is None:
            return
        tools_announced = True
        await on_tools_ready(tool_events)

    try:
        client = _groq_client()

        async def stream_start_after_tools() -> None:
            await announce_tools()
            if on_stream_start is not None:
                await on_stream_start()

        for _ in range(MAX_TOOL_CALLS + 1):
            start = time.time()
            content, tool_calls, stream_usage = await _complete_stream(
                client, model, messages, stream_start_after_tools, on_token,
                use_tools=use_tools,
            )
            latency = time.time() - start
            _usage_from(stream_usage, usage_total)

            if trace is not None:
                try:
                    trace.generation(
                        name="groq-completion", model=model, input=messages,
                        output=content or tool_calls, usage=usage_total,
                        metadata={"latency_s": latency},
                    )
                except Exception:  # noqa: BLE001
                    pass

            if not tool_calls:
                await announce_tools()
                reply = (content or "").strip() or "(empty reply)"
                return {"reply": reply, "tool_events": tool_events, "usage": usage_total}

            if len(tool_events) >= MAX_TOOL_CALLS:
                await announce_tools()
                return {
                    "reply": f"hit the {MAX_TOOL_CALLS}-tool-call cap for this reply, stopping here.",
                    "tool_events": tool_events, "usage": usage_total,
                }

            messages.append({
                "role": "assistant",
                "content": content or "",
                "tool_calls": [
                    {
                        "id": tc["id"],
                        "type": "function",
                        "function": {"name": tc["name"], "arguments": tc["arguments"]},
                    }
                    for tc in tool_calls
                ],
            })

            for tc in tool_calls:
                try:
                    args = _json.loads(tc["arguments"] or "{}")
                except _json.JSONDecodeError:
                    args = {}
                if tc["name"] == "read_only_shell":
                    result = _run_shell_tool(args.get("command", ""))
                elif tc["name"] == "search_channel_history":
                    result = await _run_search_tool(args.get("query", ""), channel_id)
                else:
                    result = f"(unknown tool {tc['name']})"

                tool_events.append({"tool": tc["name"], "args": args, "result": result})
                messages.append({
                    "role": "tool", "tool_call_id": tc["id"], "content": result,
                })

        await announce_tools()
        return {
            "reply": "(gave up after too many tool-call rounds)",
            "tool_events": tool_events, "usage": usage_total,
        }

    except Exception as exc:  # noqa: BLE001 — surface it in-channel, never crash the relay
        return {"reply": f"[agent error: {exc}]", "tool_events": tool_events, "usage": usage_total}
