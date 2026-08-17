"""
swarm.agent — the AI teammates in the room.

Phase 0: one hardcoded persona, no tools.
Phase 2: tool calling (read-only shell, channel history search).
Phase 4: multiple personas, loaded from the `agents` table.
Phase 5: every call traced through Langfuse if configured, silently
         skipped if not — this module must work with zero env vars set
         beyond GROQ_API_KEY.
Phase 8: AsyncGroq; tool-call rounds non-streaming; final text streamed.
Phase 9: per-agent harness, memory notes, classified retries, OpenRouter.
"""
from __future__ import annotations

import asyncio
import json as _json
import os
import re
import subprocess
import time
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from . import db
from .models import ALLOWED_TOOLS, DEFAULT_TOOLS

HISTORY_WINDOW = 12
MAX_TOOL_CALLS = 3
SHELL_TIMEOUT_SECONDS = 10
SHELL_OUTPUT_CAP = 4000
MEMORY_INJECT_LIMIT = 12
RETRY_DELAY_SECONDS = 0.8
CUTOFF_NOTE = "\n\n[reply cut off]"

OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
_GROQ_TO_OPENROUTER = {
    "llama-3.3-70b-versatile": "meta-llama/llama-3.3-70b-instruct",
    "llama-3.1-8b-instant": "meta-llama/llama-3.1-8b-instruct",
    "llama-3.1-70b-versatile": "meta-llama/llama-3.1-70b-instruct",
}


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
# actually looks like a file/history/memory request.
_TOOL_HINT = re.compile(
    r"\b("
    r"ls|dir|cat|grep|findstr|find|files?|folder|directory|sandbox|shell|cwd|"
    r"inspect|look\s+up|search(?:\s+the)?\s+history|earlier\s+messages?|"
    r"what\s+did\s+we|last\s+time|"
    r"remember|recall|forget|notes?|memor(?:y|ies)"
    r")\b",
    re.I,
)
_TOOL_POLICY = (
    "Tools: only use a tool when the user explicitly asks to inspect files, "
    "search older channel history, or remember/recall a fact. "
    "Greetings and normal chat get a short text reply — no tools."
)

OnToolsReady = Callable[[list[dict[str, Any]]], Awaitable[None]]
OnStreamStart = Callable[[], Awaitable[None]]
OnToken = Callable[[str], Awaitable[None]]

TOOL_SCHEMAS: dict[str, dict[str, Any]] = {
    "read_only_shell": {
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
    "search_channel_history": {
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
    "remember": {
        "type": "function",
        "function": {
            "name": "remember",
            "description": (
                "Persist a short fact this agent should keep. "
                "scope=channel (default) is for this room; scope=global "
                "follows the agent everywhere."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "body": {"type": "string", "description": "the fact to remember"},
                    "scope": {
                        "type": "string",
                        "enum": ["channel", "global"],
                        "description": "channel (default) or global",
                    },
                },
                "required": ["body"],
            },
        },
    },
    "recall": {
        "type": "function",
        "function": {
            "name": "recall",
            "description": (
                "Keyword-search this agent's saved notes. Use when the user "
                "asks what you remember beyond the notes already in context."
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
}

# Back-compat alias for tests that imported TOOLS.
TOOLS = [TOOL_SCHEMAS[name] for name in DEFAULT_TOOLS if name in TOOL_SCHEMAS]


def agent_tools(agent_row: dict[str, Any]) -> list[str]:
    names = db.parse_tools(agent_row.get("tools"))
    return [n for n in names if n in ALLOWED_TOOLS]


def tools_schema_for(names: list[str]) -> list[dict[str, Any]]:
    return [TOOL_SCHEMAS[n] for n in names if n in TOOL_SCHEMAS]


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


def _env_key(name: str) -> str:
    return (os.environ.get(name) or "").strip().strip('"').strip("'")


def _groq_client():
    from groq import AsyncGroq

    api_key = _env_key("GROQ_API_KEY")
    if not api_key:
        raise RuntimeError("missing_key")
    return AsyncGroq(api_key=api_key)


def _openrouter_client():
    from groq import AsyncGroq

    api_key = _env_key("OPENROUTER_API_KEY")
    if not api_key:
        return None
    return AsyncGroq(
        api_key=api_key,
        base_url=OPENROUTER_BASE_URL,
        default_headers={
            "HTTP-Referer": "http://localhost:8000",
            "X-Title": "swarm",
        },
    )


def openrouter_model(groq_model: str) -> str:
    override = _env_key("OPENROUTER_MODEL")
    if override:
        return override
    return _GROQ_TO_OPENROUTER.get(groq_model, groq_model)


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


async def _run_remember_tool(
    agent_name: str, channel_id: str, body: str, scope: str
) -> str:
    text = (body or "").strip()
    if not text:
        return "(nothing to remember)"
    scoped = None if scope == "global" else channel_id
    row = await db.add_memory(agent_name, text[:2000], channel_id=scoped, kind="note")
    where = "globally" if scoped is None else f"in #{channel_id}"
    return f"remembered {where} (id {row['id']})"


async def _run_recall_tool(agent_name: str, channel_id: str, query: str) -> str:
    rows = await db.search_memory(agent_name, query, channel_id=channel_id, limit=10)
    if not rows:
        return "(no matching notes)"
    lines = []
    for r in rows:
        scope = "global" if r["channel_id"] is None else f"#{r['channel_id']}"
        lines.append(f"[{scope}] {r['body'][:200]}")
    return "\n".join(lines)


def history_window_of(agent_row: dict[str, Any]) -> int:
    try:
        window = int(agent_row.get("history_window") or HISTORY_WINDOW)
    except (TypeError, ValueError):
        window = HISTORY_WINDOW
    return max(1, min(window, 50))


def max_tool_calls_of(agent_row: dict[str, Any]) -> int:
    try:
        cap = int(agent_row.get("max_tool_calls") or MAX_TOOL_CALLS)
    except (TypeError, ValueError):
        cap = MAX_TOOL_CALLS
    return max(1, min(cap, 8))


def _build_messages(
    system_prompt: str,
    history: list[dict[str, Any]],
    *,
    window: int = HISTORY_WINDOW,
    allowed_tools: list[str] | None = None,
    notes: list[dict[str, Any]] | None = None,
    summary: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    tools = allowed_tools if allowed_tools is not None else list(DEFAULT_TOOLS)
    policy = _TOOL_POLICY
    if tools:
        policy += " Allowed tools: " + ", ".join(tools) + "."
    else:
        policy += " You have no tools for this turn."
    blocks = [system_prompt, policy]
    if notes:
        lines = []
        for n in notes:
            scope = "global" if n.get("channel_id") is None else f"#{n.get('channel_id')}"
            lines.append(f"- ({scope}) {n.get('body', '')}")
        blocks.append("Known notes:\n" + "\n".join(lines))
    if summary and summary.get("body"):
        blocks.append("Channel summary:\n" + summary["body"])
    messages = [{"role": "system", "content": "\n\n".join(blocks)}]
    for m in history[-window:]:
        if m["author_kind"] == "system":
            continue  # tool-call audit messages aren't part of the model's own context
        role = "assistant" if m["author_kind"] == "agent" else "user"
        prefix = "" if role == "assistant" else f"{m['author']}: "
        messages.append({"role": role, "content": f"{prefix}{m['body']}"})
    return messages


def compact_summary(history: list[dict[str, Any]], window: int) -> str | None:
    """Local extract of messages that fall outside the live window."""
    non_system = [m for m in history if m.get("author_kind") != "system"]
    if len(non_system) < window:
        return None
    older = non_system[:-window]
    if not older:
        return None
    lines = []
    for m in older[-12:]:
        body = (m.get("body") or "").replace("\n", " ").strip()[:160]
        lines.append(f"{m.get('author')}: {body}")
    return "Earlier in this channel:\n" + "\n".join(lines)


def classify_error(exc: BaseException) -> str:
    """Short in-channel line. Never a raw traceback."""
    status = getattr(exc, "status_code", None)
    raw = str(exc) or exc.__class__.__name__
    lowered = raw.lower()
    name = exc.__class__.__name__.lower()
    if raw == "missing_key" or "api_key" in lowered or "missing_key" in lowered:
        return "[agent error: no API key — set GROQ_API_KEY in .env]"
    if status == 429 or "ratelimit" in name or "rate limit" in lowered:
        return "[agent error: rate limited — try again in a moment]"
    if status in (408, 504) or "timeout" in name or "timeout" in lowered:
        return "[agent error: the model timed out]"
    if status == 404 or "model" in lowered and ("not found" in lowered or "does not exist" in lowered):
        return "[agent error: that model isn't available]"
    if status is not None and status >= 500:
        return "[agent error: the model provider is down]"
    if "connection" in name or "connect" in lowered or "network" in lowered:
        return "[agent error: couldn't reach the model]"
    return "[agent error: couldn't reach the model]"


def is_retryable(exc: BaseException) -> bool:
    status = getattr(exc, "status_code", None)
    name = exc.__class__.__name__.lower()
    lowered = str(exc).lower()
    if status in (429, 408, 500, 502, 503, 504):
        return True
    if "timeout" in name or "timeout" in lowered:
        return True
    if "ratelimit" in name or "rate limit" in lowered:
        return True
    if "connection" in name or "connect" in lowered:
        return True
    return False


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
    tool_schemas: list[dict[str, Any]] | None = None,
) -> tuple[str, list[dict[str, Any]], Any]:
    """Stream one completion. If the model emits tool calls, tokens are not
    forwarded and assembled tool_calls are returned. Otherwise tokens are
    forwarded live and content is returned. A mid-stream failure with
    partial content returns that content plus a cutoff note instead of
    raising."""
    kwargs: dict[str, Any] = dict(
        model=model, messages=messages, temperature=0.4, max_tokens=600, stream=True,
    )
    if tool_schemas:
        kwargs["tools"] = tool_schemas
        kwargs["tool_choice"] = "auto"
    stream = await client.chat.completions.create(**kwargs)
    content = ""
    tool_acc: dict[int, dict[str, str]] = {}
    saw_tools = False
    started = False
    usage = None

    try:
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
    except Exception:
        if content and not saw_tools:
            note = CUTOFF_NOTE
            if on_token is not None:
                await on_token(note)
            return content + note, [], usage
        raise

    tool_calls = [
        {"id": slot["id"], "name": slot["name"], "arguments": slot["arguments"]}
        for _, slot in sorted(tool_acc.items())
        if slot["name"]
    ]
    return content, tool_calls, usage


async def _execute_tool(
    name: str,
    args: dict[str, Any],
    *,
    agent_name: str,
    channel_id: str,
    allowed: list[str],
) -> str:
    if name not in allowed:
        return f"(tool {name} is disabled for this agent)"
    if name == "read_only_shell":
        return _run_shell_tool(args.get("command", ""))
    if name == "search_channel_history":
        return await _run_search_tool(args.get("query", ""), channel_id)
    if name == "remember":
        return await _run_remember_tool(
            agent_name, channel_id, args.get("body", ""), args.get("scope") or "channel",
        )
    if name == "recall":
        return await _run_recall_tool(agent_name, channel_id, args.get("query", ""))
    return f"(unknown tool {name})"


async def _run_with_client(
    client: Any,
    model: str,
    agent_row: dict[str, Any],
    channel_id: str,
    messages: list[dict[str, Any]],
    *,
    use_tools: bool,
    allowed: list[str],
    on_tools_ready: OnToolsReady | None,
    on_stream_start: OnStreamStart | None,
    on_token: OnToken | None,
    trace: Any,
) -> dict[str, Any]:
    cap = max_tool_calls_of(agent_row)
    schemas = tools_schema_for(allowed) if use_tools and allowed else []
    tool_events: list[dict[str, Any]] = []
    usage_total = {"prompt_tokens": 0, "completion_tokens": 0}
    tools_announced = False

    async def announce_tools() -> None:
        nonlocal tools_announced
        if tools_announced or on_tools_ready is None:
            return
        tools_announced = True
        await on_tools_ready(tool_events)

    async def stream_start_after_tools() -> None:
        await announce_tools()
        if on_stream_start is not None:
            await on_stream_start()

    for _ in range(cap + 1):
        start = time.time()
        content, tool_calls, stream_usage = await _complete_stream(
            client, model, messages, stream_start_after_tools, on_token,
            tool_schemas=schemas or None,
        )
        latency = time.time() - start
        _usage_from(stream_usage, usage_total)

        if trace is not None:
            try:
                trace.generation(
                    name="completion", model=model, input=messages,
                    output=content or tool_calls, usage=usage_total,
                    metadata={"latency_s": latency},
                )
            except Exception:  # noqa: BLE001
                pass

        if not tool_calls:
            await announce_tools()
            reply = (content or "").strip() or "(empty reply)"
            return {"reply": reply, "tool_events": tool_events, "usage": usage_total}

        if len(tool_events) >= cap:
            await announce_tools()
            return {
                "reply": f"hit the {cap}-tool-call cap for this reply, stopping here.",
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
            result = await _execute_tool(
                tc["name"], args,
                agent_name=agent_row["name"],
                channel_id=channel_id,
                allowed=allowed,
            )
            tool_events.append({"tool": tc["name"], "args": args, "result": result})
            messages.append({
                "role": "tool", "tool_call_id": tc["id"], "content": result,
            })

    await announce_tools()
    return {
        "reply": "(gave up after too many tool-call rounds)",
        "tool_events": tool_events, "usage": usage_total,
    }


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

    Each provider round is streamed. Tool-call rounds accumulate tool_calls
    and do not forward tokens. The first content round forwards tokens
    live; on_tools_ready is invoked before the first streamed token so
    audit messages land first."""
    name = agent_row["name"]
    model = os.environ.get("SWARM_AGENT_MODEL") or agent_row["model"]
    window = history_window_of(agent_row)
    allowed = agent_tools(agent_row)
    notes, summary = await db.get_context_memories(name, channel_id, MEMORY_INJECT_LIMIT)
    messages = _build_messages(
        agent_row["system_prompt"], history,
        window=window, allowed_tools=allowed, notes=notes, summary=summary,
    )
    use_tools = should_offer_tools(history) and bool(allowed)

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

    async def attempt(client: Any, used_model: str) -> dict[str, Any]:
        # copy messages so a failed attempt doesn't leave half-appended tool turns
        snapshot = [dict(m) for m in messages]
        return await _run_with_client(
            client, used_model, agent_row, channel_id, snapshot,
            use_tools=use_tools, allowed=allowed,
            on_tools_ready=on_tools_ready,
            on_stream_start=on_stream_start,
            on_token=on_token,
            trace=trace,
        )

    last_exc: BaseException | None = None
    groq_client = None
    try:
        groq_client = _groq_client()
    except Exception as exc:  # noqa: BLE001
        last_exc = exc

    if groq_client is not None:
        try:
            result = await attempt(groq_client, model)
            await _maybe_write_summary(name, channel_id, history, window)
            return result
        except Exception as exc:  # noqa: BLE001
            last_exc = exc
            if is_retryable(exc):
                await asyncio.sleep(RETRY_DELAY_SECONDS)
                try:
                    result = await attempt(groq_client, model)
                    await _maybe_write_summary(name, channel_id, history, window)
                    return result
                except Exception as retry_exc:  # noqa: BLE001
                    last_exc = retry_exc

    or_client = _openrouter_client()
    if or_client is not None:
        try:
            result = await attempt(or_client, openrouter_model(model))
            await _maybe_write_summary(name, channel_id, history, window)
            return result
        except Exception as exc:  # noqa: BLE001
            last_exc = exc

    return {
        "reply": classify_error(last_exc or RuntimeError("couldn't reach the model")),
        "tool_events": [],
        "usage": {"prompt_tokens": 0, "completion_tokens": 0},
    }


async def _maybe_write_summary(
    agent_name: str, channel_id: str, history: list[dict[str, Any]], window: int
) -> None:
    text = compact_summary(history, window)
    if not text:
        return
    try:
        await db.replace_summary(agent_name, channel_id, text)
    except Exception:  # noqa: BLE001 — summary is best-effort
        pass
