"""
swarm.agent contains the AI teammates that work in the room.

Phase 0: one hardcoded persona, no tools.
Phase 2: tool calling (read-only shell, channel history search).
Phase 4: multiple personas, loaded from the `agents` table.
Phase 5: every call traced through Langfuse when configured. Tracing is
         skipped otherwise, and the module works with zero env vars set
         beyond GROQ_API_KEY.
Phase 8: OpenAI SDK streaming; tool-call rounds non-streaming; final text streamed.
Phase 9: per-agent harness, memory notes, classified retries, OpenRouter.
"""
from __future__ import annotations

import asyncio
import json as _json
import logging
import os
import re
import subprocess
import time
import uuid
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from . import db
from .models import DEFAULT_TOOLS, resolve_groq_model
from .tools.registry import BUILTIN_SCHEMAS, get_registry, reload_registry

HISTORY_WINDOW = 12
MAX_TOOL_CALLS = 6
TOOL_CALL_HARD_CAP = 12
SHELL_TIMEOUT_SECONDS = 10
SHELL_OUTPUT_CAP = 4000
MEMORY_INJECT_LIMIT = 12
RETRY_DELAY_SECONDS = 0.8
CUTOFF_NOTE = "\n\n[reply cut off]"
CUTOFF_MARKER = "[reply cut off — stopped]"

_logger = logging.getLogger("swarm.agent")

DEMO_STREAM_DELAY = 0.012


def _default_sandbox_dir() -> str:
    override = os.environ.get("SWARM_SANDBOX_DIR")
    if override:
        return override
    if os.name == "nt":
        base = os.environ.get("TEMP") or os.environ.get("LOCALAPPDATA") or r"C:\Temp"
        return str(Path(base) / "swarm-sandbox")
    return "/tmp/swarm-sandbox"


SANDBOX_DIR = _default_sandbox_dir()

# Fast Groq models (e.g. openai/gpt-oss-20b) call tools on "hi" unless
# we withhold the tool schema. Only offer tools when the last human message
# actually looks like a file/history/memory request.
_TOOL_HINT = re.compile(
    r"\b("
    r"ls|dir|cat|grep|findstr|find|files?|folder|directory|sandbox|shell|cwd|"
    r"inspect|look\s+up|search(?:\s+the)?\s+history|earlier\s+messages?|"
    r"what\s+did\s+we|last\s+time|"
    r"remember|recall|forget|notes?|memor(?:y|ies)|"
    r"knowledge|kb\b|docs?|facts?|learn(?:ed|ing)?|"
    r"workspace|write|read|fetch|url|digest|save|skill|routine|schedule|approv|"
    r"computer|screenshot|browser|navigate|click|type|press|"
    r"composio|gmail|github|slack|notion|toolkit|"
    r"exa|tavily|firecrawl|crawl|scrape|research|"
    r"host|system|machine|repo|pytest|git|"
    r"handoff|draft|"
    r"bot|teammate|spin\s+up|create\s+an?\s+\w+\s+(bot|agent|assistant|helper|tracker|coach)"
    r")\b",
    re.I,
)
_SLASH_SKILL = re.compile(r"/([a-zA-Z0-9_\-]+)")
_TOOL_POLICY = (
    "You are a persistent named teammate. Finish the job and only stop when "
    "the deliverable is ready or something needs approval. "
    "Tools: use them when the work needs files, this machine, the shared sandbox, "
    "a browser, web search (Exa/Tavily), a Firecrawl scrape, Browser Use CLI, "
    "CUA desktop, a connected app (Composio), history, memory, a saved skill, "
    "or an approval gate. Greetings in a shared room get a short text reply. "
    "system_run / system_ls / system_read / system_write work on this host "
    "(bound to the system root, usually the repo). computer_run is the isolated "
    "sandbox. browser_* is Playwright; browser_use is the Browser Use CLI; "
    "cua_desktop is the CUA host driver; plugin:composio:* lists, connects, "
    "and executes Gmail/Slack/GitHub/Notion/etc. "
    "For sending, publishing, deleting, purchasing, or production changes, call "
    "request_approval and wait. Put durable sandbox files in the shared workspace; "
    "put repo/code work on the system root. "
    "Check knowledge_search before answering from memory; save durable facts with "
    "knowledge_save; drop stale notes with forget. "
    "When the user describes a recurring need (tracking, reminders, notes, "
    "research, coaching), offer to provision a dedicated bot with create_agent "
    "— a short slug name, a job title, and a focused system_prompt — and "
    "point them at its new 1:1 DM channel. "
    "You may @mention another bot to hand off work. Follow your profile.md."
)

OnToolsReady = Callable[[list[dict[str, Any]]], Awaitable[None]]
OnStreamStart = Callable[[], Awaitable[None]]
OnToken = Callable[[str], Awaitable[None]]

TOOL_SCHEMAS: dict[str, dict[str, Any]] = BUILTIN_SCHEMAS

# Back-compat alias for tests that imported TOOLS.
TOOLS = [BUILTIN_SCHEMAS[name] for name in DEFAULT_TOOLS if name in BUILTIN_SCHEMAS]


def agent_tools(agent_row: dict[str, Any]) -> list[str]:
    names = db.parse_tools(agent_row.get("tools"))
    return get_registry().filter_allowed(names)


def tools_schema_for(names: list[str]) -> list[dict[str, Any]]:
    return get_registry().schemas_for(names)


def should_offer_tools(
    history: list[dict[str, Any]], *, channel_kind: str | None = None
) -> bool:
    """True if this turn looks like work. DMs and routines always offer tools.
    Shared rooms still skip greetings so small models don't tool-call 'hi'."""
    if channel_kind in ("dm", "group"):
        return True
    for m in reversed(history):
        body = m.get("body") or ""
        if m.get("author_kind") == "system" and body.startswith("[routine:"):
            return True
        if m.get("author_kind") == "human":
            if body.lstrip().startswith("/") or _TOOL_HINT.search(body):
                return True
            return False
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


def find_mentioned_teams(body: str, teams: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Teams mentioned as @team-id, ordered by first mention position."""
    hits: list[tuple[int, dict[str, Any]]] = []
    lowered = body.lower()
    for team in teams:
        slug = (team.get("id") or "").lower()
        if not slug:
            continue
        match = re.search(rf"@{re.escape(slug)}\b", lowered)
        if match:
            hits.append((match.start(), team))
    hits.sort(key=lambda h: h[0])
    return [t for _, t in hits]


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


def list_workspace_files(limit: int = 100) -> list[dict[str, Any]]:
    os.makedirs(SANDBOX_DIR, exist_ok=True)
    base = Path(SANDBOX_DIR)
    files: list[dict[str, Any]] = []
    for path in sorted(base.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(base).as_posix()
        if rel.startswith("."):
            continue
        stat = path.stat()
        files.append({"path": rel, "size": stat.st_size, "modified": stat.st_mtime})
        if len(files) >= limit:
            break
    return files


async def _run_list_workspace() -> str:
    os.makedirs(SANDBOX_DIR, exist_ok=True)
    base = Path(SANDBOX_DIR)
    files: list[str] = []
    for path in sorted(base.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(base).as_posix()
        if rel.startswith("."):
            continue
        files.append(f"{rel} ({path.stat().st_size} bytes)")
        if len(files) >= 100:
            break
    return "\n".join(files) if files else "(workspace empty)"


def _safe_workspace_path(rel: str) -> Path | None:
    if not rel or rel.strip() != rel:
        rel = (rel or "").strip()
    if not rel or rel.startswith("/") or "\\" in rel[:1]:
        return None
    base = Path(SANDBOX_DIR).resolve()
    target = (base / rel).resolve()
    try:
        target.relative_to(base)
    except ValueError:
        return None
    return target


async def _run_write_workspace(rel: str, content: str) -> str:
    target = _safe_workspace_path(rel)
    if target is None:
        return "(invalid path — stay under the workspace root)"
    text = content if isinstance(content, str) else str(content)
    if len(text) > 32_000:
        return "(file too large — cap is 32000 characters)"
    os.makedirs(SANDBOX_DIR, exist_ok=True)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")
    rel_out = target.relative_to(Path(SANDBOX_DIR).resolve()).as_posix()
    return f"wrote {rel_out} ({len(text)} chars)"


async def _run_save_skill(name: str, body: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9_\-]", "", (name or "").strip())[:64]
    text = (body or "").strip()
    if not slug or not text:
        return "(need a slug name and a body)"
    row = await db.upsert_skill(slug, text[:8000])
    return f"saved skill /{row['name']} (id {row['id']})"


async def _run_approval_tool(
    agent_name: str, channel_id: str, action: str, detail: str
) -> str:
    label = (action or "").strip()[:200]
    if not label:
        return "(need an action to approve)"
    row = await db.create_approval(agent_name, channel_id, label, (detail or "").strip()[:2000])
    await db.set_agent_status(agent_name, "needs_approval")
    return (
        f"Approval #{row['id']} is pending for: {label}. "
        "Stop here. Tell the human what you need approved. Do not proceed."
    )


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
    return max(1, min(cap, TOOL_CALL_HARD_CAP))


def _build_messages(
    system_prompt: str,
    history: list[dict[str, Any]],
    *,
    window: int = HISTORY_WINDOW,
    allowed_tools: list[str] | None = None,
    notes: list[dict[str, Any]] | None = None,
    summary: dict[str, Any] | None = None,
    knowledge: list[dict[str, Any]] | None = None,
    job: str | None = None,
    skills: list[dict[str, Any]] | None = None,
    invoked_skills: list[dict[str, Any]] | None = None,
    group_mates: list[str] | None = None,
    display_name: str | None = None,
    profile: str | None = None,
) -> list[dict[str, Any]]:
    tools = allowed_tools if allowed_tools is not None else list(DEFAULT_TOOLS)
    policy = _TOOL_POLICY
    if display_name:
        policy = f"Your name in chat is {display_name}.\n" + policy
    if job:
        policy = f"Primary job: {job}.\n" + policy
    if tools:
        policy += " Allowed tools: " + ", ".join(tools) + "."
    else:
        policy += " You have no tools for this turn."
    blocks = [system_prompt, policy]
    if profile:
        blocks.append("Bot profile (profile.md):\n" + profile)
    if group_mates:
        others = [n for n in group_mates if n]
        if others:
            blocks.append(
                "You are in a group chat with: "
                + ", ".join(f"@{n}" for n in others)
                + ". Reply as yourself. If another member already covered it, "
                "keep your reply short or @mention them to hand off."
            )
    if skills:
        names = ", ".join(f"/{s['name']}" for s in skills)
        blocks.append(
            "Saved skills (humans type /name to invoke; you may follow them when relevant): "
            + names
        )
    if invoked_skills:
        for s in invoked_skills:
            blocks.append(f"Invoked skill /{s['name']}:\n{s['body']}")
    tool_set = set(tools or [])
    if "knowledge_search" in tool_set or "knowledge_save" in tool_set:
        blocks.append(
            "Knowledge habit: before answering a factual question, call "
            "knowledge_search with the key terms (channel-scoped docs are "
            "checked first). When you learn a durable fact, preference, or "
            "decision worth reusing, call knowledge_save with a short title "
            "and body."
        )
    if "forget" in tool_set:
        blocks.append(
            "Memory hygiene: when the user asks to remove a note, or a "
            "remembered fact is clearly stale, call forget with the note id "
            "or a keyword query."
        )
    if notes:
        lines = []
        for n in notes:
            scope = "global" if n.get("channel_id") is None else f"#{n.get('channel_id')}"
            lines.append(f"- ({scope}) {n.get('body', '')}")
        blocks.append("Known notes:\n" + "\n".join(lines))
    if summary and summary.get("body"):
        blocks.append("Channel summary:\n" + summary["body"])
    if knowledge:
        chunks = []
        for hit in knowledge[:3]:
            excerpt = (hit.get("body") or "").replace("\n", " ").strip()[:400]
            chunks.append(f"- [{hit.get('title') or 'note'}] {excerpt}")
        if chunks:
            blocks.append("Knowledge base (use when relevant):\n" + "\n".join(chunks))
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


def is_agent_error(body: str) -> bool:
    return (body or "").strip().startswith("[agent error:")


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


def is_model_not_found(exc: BaseException | None) -> bool:
    if exc is None:
        return False
    status = getattr(exc, "status_code", None)
    lowered = str(exc).lower()
    return status == 404 or ("model" in lowered and ("not found" in lowered or "does not exist" in lowered))


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
    """Stream one completion. If the model emits tool calls, live token
    forwarding stops but narration is still accumulated so the assistant
    message keeps it. Tool calls with a missing id get a synthesized one so
    the follow-up `tool` message always has a valid `tool_call_id`.
    A mid-stream failure with partial content returns that content plus a
    cutoff note instead of raising."""
    kwargs: dict[str, Any] = dict(
        model=model, messages=messages, temperature=0.4, stream=True,
    )
    if model.startswith("openai/gpt-oss-"):
        # Reasoning tokens count against max_tokens; 600 was cutting replies off.
        kwargs["max_tokens"] = 2048
        base = str(getattr(client, "base_url", "") or "")
        if "openrouter.ai" not in base:
            kwargs["reasoning_effort"] = "low"
            kwargs["include_reasoning"] = False
    else:
        kwargs["max_tokens"] = 600
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
            elif delta.content:
                content += delta.content
                if saw_tools:
                    # Narration alongside tool calls: keep it for the
                    # assistant message, but don't stream it live.
                    continue
                if not started:
                    if on_stream_start is not None:
                        await on_stream_start()
                    started = True
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
        {
            "id": slot["id"] or f"tc_{uuid.uuid4().hex[:12]}",
            "name": slot["name"],
            "arguments": slot["arguments"],
        }
        for _, slot in sorted(tool_acc.items())
        if slot["name"]
    ]
    return content, tool_calls, usage


async def _run_read_workspace(rel: str) -> str:
    target = _safe_workspace_path(rel)
    if target is None:
        return "(invalid path — stay under the workspace root)"
    if not target.is_file():
        return "(file not found)"
    try:
        text = target.read_text(encoding="utf-8")
    except OSError as exc:
        return f"(read error: {exc})"
    if len(text) > 32_000:
        return text[:32_000] + "\n…[truncated]"
    return text


async def _execute_tool(
    name: str,
    args: dict[str, Any],
    *,
    agent_name: str,
    channel_id: str,
    allowed: list[str],
) -> str:
    helpers = {
        "list": _run_list_workspace,
        "read": _run_read_workspace,
        "write": _run_write_workspace,
        "save_skill": _run_save_skill,
        "approval": _run_approval_tool,
    }
    return await get_registry().execute(
        name, args,
        agent_name=agent_name,
        channel_id=channel_id,
        allowed=allowed,
        sandbox_dir=SANDBOX_DIR,
        shell_runner=_run_shell_tool,
        workspace_helpers=helpers,
    )


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

        remaining = cap - len(tool_events)
        if remaining <= 0:
            return await _finish_at_cap(
                announce_tools, client, model, messages,
                stream_start_after_tools, on_token, cap,
                tool_events, usage_total,
            )

        selected_tool_calls = tool_calls[:remaining]
        messages.append({
            "role": "assistant",
            "content": content or "",
            "tool_calls": [
                {
                    "id": tc["id"],
                    "type": "function",
                    "function": {"name": tc["name"], "arguments": tc["arguments"]},
                }
                for tc in selected_tool_calls
            ],
        })

        for tc in selected_tool_calls:
            try:
                args = _json.loads(tc["arguments"] or "{}")
            except _json.JSONDecodeError:
                args = {}
                result = "(malformed tool arguments: not valid JSON)"
                tool_events.append({"tool": tc["name"], "args": {}, "result": result})
                messages.append({
                    "role": "tool", "tool_call_id": tc["id"], "content": result,
                })
                continue
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

        if len(selected_tool_calls) < len(tool_calls):
            return await _finish_at_cap(
                announce_tools, client, model, messages,
                stream_start_after_tools, on_token, cap,
                tool_events, usage_total,
            )

    await announce_tools()
    return {
        "reply": "(gave up after too many tool-call rounds)",
        "tool_events": tool_events, "usage": usage_total,
    }


async def _finish_at_cap(
    announce_tools: Any,
    client: Any,
    model: str,
    messages: list[dict[str, Any]],
    on_stream_start: OnStreamStart | None,
    on_token: OnToken | None,
    cap: int,
    tool_events: list[dict[str, Any]],
    usage_total: dict[str, Any],
) -> dict[str, Any]:
    """Shared cap-hit exit: announce tools, run the final no-tools round."""
    await announce_tools()
    return {
        "reply": await _closing_after_cap(
            client, model, messages, on_stream_start, on_token, cap),
        "tool_events": tool_events, "usage": usage_total,
    }


async def _closing_after_cap(
    client: Any,
    model: str,
    messages: list[dict[str, Any]],
    on_stream_start: OnStreamStart | None,
    on_token: OnToken | None,
    cap: int,
) -> str:
    """One final no-tools round so a capped reply summarizes instead of
    dead-ending on a cap notice. Falls back to the notice when empty."""
    closing, _, _ = await _complete_stream(
        client, model,
        [*messages, {"role": "user", "content": "Summarize what you did and what is still left, briefly."}],
        on_stream_start, on_token, tool_schemas=None,
    )
    reply = (closing or "").strip()
    return reply or f"hit the {cap}-tool-call cap for this reply, stopping here."


def demo_mode_enabled() -> bool:
    return (os.environ.get("SWARM_DEMO") or "").strip().lower() in ("1", "true", "yes")


def _last_human_message(history: list[dict[str, Any]]) -> dict[str, Any] | None:
    for m in reversed(history):
        if m.get("author_kind") == "human":
            return m
    return None


def compose_demo_reply(agent_row: dict[str, Any], history: list[dict[str, Any]]) -> str:
    """Deterministic mock reply for portfolio demos without API keys."""
    name = agent_row["name"]
    job = (agent_row.get("job") or "Teammate").strip()
    last = _last_human_message(history)
    body = (last.get("body") or "").strip() if last else ""
    snippet = body[:120] + ("…" if len(body) > 120 else "")
    if not body:
        return (
            f"[demo mode] I'm @{name}, your {job} teammate. "
            "Ask me anything — replies are mocked locally, no API key needed."
        )
    lower = body.lower()
    if any(w in lower for w in ("approve", "send", "publish", "delete", "purchase")):
        return (
            f'[demo mode] I\'d pause here and call `request_approval` before anything external. '
            f'For "{snippet}", my next step would be a review-ready draft in the workspace.'
        )
    if "?" in body or lower.startswith(("what", "why", "how", "who", "when", "summar")):
        return (
            f'[demo mode] On "{snippet}" — as your {job}, I\'d pull evidence from this channel '
            "and the shared workspace first, then return a short ranked answer with sources."
        )
    return (
        f'[demo mode] Got it — "{snippet}". I\'d break this into a concrete deliverable, '
        f"write notes to the workspace if it should stick, and stop for approval before anything external."
    )


async def _demo_generate_reply(
    agent_row: dict[str, Any],
    history: list[dict[str, Any]],
    on_stream_start: OnStreamStart | None = None,
    on_token: OnToken | None = None,
) -> dict[str, Any]:
    reply = compose_demo_reply(agent_row, history)
    if on_stream_start is not None:
        await on_stream_start()
    words = reply.split(" ")
    for i, word in enumerate(words):
        chunk = word if i == len(words) - 1 else word + " "
        if on_token is not None:
            await on_token(chunk)
        await asyncio.sleep(DEMO_STREAM_DELAY)
    return {
        "reply": reply,
        "tool_events": [],
        "usage": {"prompt_tokens": 0, "completion_tokens": len(words)},
    }


async def generate_reply(
    agent_row: dict[str, Any],
    channel_id: str,
    history: list[dict[str, Any]],
    on_tools_ready: OnToolsReady | None = None,
    on_stream_start: OnStreamStart | None = None,
    on_token: OnToken | None = None,
    model_override: str | None = None,
) -> dict[str, Any]:
    """Returns {"reply": str, "tool_events": [{"tool", "args", "result"}], "usage": {...}, "model": str}.
    tool_events is populated in order — caller (main.py) persists each as
    a system message before posting the final reply, per FR2.2.

    Each provider round is streamed. Tool-call rounds accumulate tool_calls
    and do not forward tokens. The first content round forwards tokens
    live; on_tools_ready is invoked before the first streamed token so
    audit messages land first."""
    if demo_mode_enabled():
        result = await _demo_generate_reply(
            agent_row, history, on_stream_start=on_stream_start, on_token=on_token,
        )
        window = history_window_of(agent_row)
        await _maybe_write_summary(agent_row["name"], channel_id, history, window)
        result["model"] = result.get("model") or "demo"
        return result

    name = agent_row["name"]
    from .ai_support.resolver import any_provider_ready, iter_provider_attempts, resolve_effective_model

    model = await resolve_effective_model(
        agent_row.get("model") or "",
        override=(model_override or "").strip() or None,
    )
    window = history_window_of(agent_row)
    allowed = agent_tools(agent_row)
    notes, summary = await db.get_context_memories(name, channel_id, MEMORY_INJECT_LIMIT)
    from . import context as context_mod
    package = await context_mod.build_context(
        name, channel_id, history, window=window, kb_limit=3)
    context_history = package["history"]
    kb_hits = package["kb_hits"]
    channel = await db.get_channel(channel_id)
    channel_kind = (channel or {}).get("kind") or "room"
    skills = await db.list_skills()
    invoked: list[dict[str, Any]] = []
    for m in reversed(history):
        if m.get("author_kind") in ("human", "system"):
            slugs = _SLASH_SKILL.findall(m.get("body") or "")
            by_name = {s["name"]: s for s in skills}
            for slug in slugs:
                if slug in by_name and by_name[slug] not in invoked:
                    invoked.append(by_name[slug])
            break
    group_mates = (channel or {}).get("members") if channel_kind == "group" else None
    from .profiles import load_agent_profile
    profile = load_agent_profile(name, agent_row.get("job"))
    messages = _build_messages(
        agent_row["system_prompt"], context_history,
        window=max(len(context_history), 1), allowed_tools=allowed, notes=notes, summary=summary,
        knowledge=kb_hits,
        job=agent_row.get("job"), skills=skills, invoked_skills=invoked,
        group_mates=group_mates, display_name=agent_row.get("display_name"),
        profile=profile,
    )
    use_tools = should_offer_tools(history, channel_kind=channel_kind) and bool(allowed)

    # A provider fallback must not announce a second visible stream if the
    # first provider failed after opening one.
    stream_announced = False

    async def safe_stream_start() -> None:
        nonlocal stream_announced
        if stream_announced:
            return
        stream_announced = True
        if on_stream_start is not None:
            await on_stream_start()

    async def safe_token(delta: str) -> None:
        if on_token is not None:
            await on_token(delta)

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
            on_stream_start=safe_stream_start,
            on_token=safe_token,
            trace=trace,
        )

    last_exc: BaseException | None = None

    if not await any_provider_ready():
        return {
            "reply": "[agent error: no API key — set GROQ_API_KEY in .env or connect a provider in Command Center → AI providers]",
            "tool_events": [],
            "usage": {"prompt_tokens": 0, "completion_tokens": 0},
            "model": model,
        }

    attempts = await iter_provider_attempts(model)
    if not attempts:
        return {
            "reply": "[agent error: no API key — set GROQ_API_KEY in .env or connect a provider in Command Center → AI providers]",
            "tool_events": [],
            "usage": {"prompt_tokens": 0, "completion_tokens": 0},
            "model": model,
        }

    for client, used_model, _provider_id in attempts:
        try:
            result = await attempt(client, used_model)
            await _maybe_write_summary(name, channel_id, history, window)
            result["context"] = package["stats"]
            result["model"] = used_model
            return result
        except Exception as exc:  # noqa: BLE001
            last_exc = exc
            if is_retryable(exc):
                await asyncio.sleep(RETRY_DELAY_SECONDS)
                try:
                    result = await attempt(client, used_model)
                    await _maybe_write_summary(name, channel_id, history, window)
                    result["context"] = package["stats"]
                    result["model"] = used_model
                    return result
                except Exception as retry_exc:  # noqa: BLE001
                    last_exc = retry_exc

    if (model_override or "").strip() and is_model_not_found(last_exc):
        # The pinned model is gone (rotated id, outage) — fall back to the
        # agent default once instead of failing the turn. Partial tokens
        # already streamed stay visible; the fallback continues the reply.
        _logger.warning("model %s unavailable, falling back to agent default", model_override)
        fallback = await generate_reply(
            agent_row, channel_id, history,
            on_tools_ready=on_tools_ready,
            on_stream_start=on_stream_start,
            on_token=on_token,
            model_override=None,
        )
        fallback["model_fallback"] = True
        return fallback

    return {
        "reply": classify_error(last_exc or RuntimeError("couldn't reach the model")),
        "tool_events": [],
        "usage": {"prompt_tokens": 0, "completion_tokens": 0},
        "model": model,
    }


async def _maybe_write_summary(
    agent_name: str, channel_id: str, history: list[dict[str, Any]], window: int
) -> None:
    text = compact_summary(history, window)
    if not text:
        return
    try:
        await db.append_summary(agent_name, channel_id, text)
    except Exception:  # noqa: BLE001 — summary is best-effort
        pass
