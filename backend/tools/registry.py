"""Central tool registry: builtins, DB custom tools, and plugins/."""
from __future__ import annotations

import importlib.util
import inspect
import json
import re
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Awaitable, Callable

ToolHandler = Callable[..., Awaitable[str] | str]

BUILTIN_SCHEMAS: dict[str, dict[str, Any]] = {
    "read_only_shell": {
        "type": "function",
        "function": {
            "name": "read_only_shell",
            "description": "Run a read-only shell command in the sandbox working directory.",
            "parameters": {
                "type": "object",
                "properties": {"command": {"type": "string"}},
                "required": ["command"],
            },
        },
    },
    "search_channel_history": {
        "type": "function",
        "function": {
            "name": "search_channel_history",
            "description": "Keyword-search this channel's message history.",
            "parameters": {
                "type": "object",
                "properties": {"query": {"type": "string"}},
                "required": ["query"],
            },
        },
    },
    "remember": {
        "type": "function",
        "function": {
            "name": "remember",
            "description": "Persist a short fact for this agent (scope channel or global).",
            "parameters": {
                "type": "object",
                "properties": {
                    "body": {"type": "string"},
                    "scope": {"type": "string", "enum": ["channel", "global"]},
                },
                "required": ["body"],
            },
        },
    },
    "recall": {
        "type": "function",
        "function": {
            "name": "recall",
            "description": "Keyword-search this agent's saved notes.",
            "parameters": {
                "type": "object",
                "properties": {"query": {"type": "string"}},
                "required": ["query"],
            },
        },
    },
    "list_workspace": {
        "type": "function",
        "function": {
            "name": "list_workspace",
            "description": "List files in the shared workspace.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    "read_workspace": {
        "type": "function",
        "function": {
            "name": "read_workspace",
            "description": "Read a text file from the shared workspace (cap 32k chars).",
            "parameters": {
                "type": "object",
                "properties": {"path": {"type": "string"}},
                "required": ["path"],
            },
        },
    },
    "write_workspace": {
        "type": "function",
        "function": {
            "name": "write_workspace",
            "description": "Write a text file into the shared workspace.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "content": {"type": "string"},
                },
                "required": ["path", "content"],
            },
        },
    },
    "fetch_url": {
        "type": "function",
        "function": {
            "name": "fetch_url",
            "description": "HTTP GET a public URL and return text (cap 8k, 10s timeout). No auth headers.",
            "parameters": {
                "type": "object",
                "properties": {"url": {"type": "string"}},
                "required": ["url"],
            },
        },
    },
    "channel_digest": {
        "type": "function",
        "function": {
            "name": "channel_digest",
            "description": "Return a local digest of the last N non-system messages in this channel.",
            "parameters": {
                "type": "object",
                "properties": {"limit": {"type": "integer"}},
            },
        },
    },
    "save_skill": {
        "type": "function",
        "function": {
            "name": "save_skill",
            "description": "Save a reusable account-wide skill invoked as /name.",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "body": {"type": "string"},
                },
                "required": ["name", "body"],
            },
        },
    },
    "request_approval": {
        "type": "function",
        "function": {
            "name": "request_approval",
            "description": "Pause for human approval before consequential external actions.",
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {"type": "string"},
                    "detail": {"type": "string"},
                },
                "required": ["action"],
            },
        },
    },
    "computer_run": {
        "type": "function",
        "function": {
            "name": "computer_run",
            "description": (
                "Run a shell command in the shared sandbox (isolated cwd, 30s timeout). "
                "For this machine's repo and host files, use system_run."
            ),
            "parameters": {
                "type": "object",
                "properties": {"command": {"type": "string"}},
                "required": ["command"],
            },
        },
    },
    "computer_open": {
        "type": "function",
        "function": {
            "name": "computer_open",
            "description": "Open a workspace file or directory. For http(s) URLs, prefer browser_navigate.",
            "parameters": {
                "type": "object",
                "properties": {"target": {"type": "string"}},
                "required": ["target"],
            },
        },
    },
    "computer_screenshot": {
        "type": "function",
        "function": {
            "name": "computer_screenshot",
            "description": "Capture the live browser page if open, otherwise a text snapshot of the sandbox.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    "browser_navigate": {
        "type": "function",
        "function": {
            "name": "browser_navigate",
            "description": "Open a URL in the shared headless Chromium session (Playwright).",
            "parameters": {
                "type": "object",
                "properties": {"url": {"type": "string"}},
                "required": ["url"],
            },
        },
    },
    "browser_snapshot": {
        "type": "function",
        "function": {
            "name": "browser_snapshot",
            "description": "Read the current page title, visible text, and links.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    "browser_click": {
        "type": "function",
        "function": {
            "name": "browser_click",
            "description": "Click a CSS selector on the current page.",
            "parameters": {
                "type": "object",
                "properties": {"selector": {"type": "string"}},
                "required": ["selector"],
            },
        },
    },
    "browser_type": {
        "type": "function",
        "function": {
            "name": "browser_type",
            "description": "Fill a CSS selector with text on the current page.",
            "parameters": {
                "type": "object",
                "properties": {
                    "selector": {"type": "string"},
                    "text": {"type": "string"},
                },
                "required": ["selector"],
            },
        },
    },
    "browser_press": {
        "type": "function",
        "function": {
            "name": "browser_press",
            "description": "Press a keyboard key on the current page (e.g. Enter, Tab, Escape).",
            "parameters": {
                "type": "object",
                "properties": {"key": {"type": "string"}},
            },
        },
    },
    "browser_wait": {
        "type": "function",
        "function": {
            "name": "browser_wait",
            "description": "Wait up to 10 seconds for a page to settle.",
            "parameters": {
                "type": "object",
                "properties": {"ms": {"type": "integer"}},
            },
        },
    },
    "browser_screenshot": {
        "type": "function",
        "function": {
            "name": "browser_screenshot",
            "description": "Save a PNG of the current page under screenshots/ in the sandbox.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    "exa_search": {
        "type": "function",
        "function": {
            "name": "exa_search",
            "description": "Neural web search via Exa. Use for research and citations. Requires EXA_API_KEY.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "num_results": {"type": "integer"},
                },
                "required": ["query"],
            },
        },
    },
    "tavily_search": {
        "type": "function",
        "function": {
            "name": "tavily_search",
            "description": "LLM-oriented web search via Tavily. Requires TAVILY_API_KEY.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "max_results": {"type": "integer"},
                },
                "required": ["query"],
            },
        },
    },
    "firecrawl_scrape": {
        "type": "function",
        "function": {
            "name": "firecrawl_scrape",
            "description": "Scrape a URL to clean markdown via Firecrawl. Requires FIRECRAWL_API_KEY.",
            "parameters": {
                "type": "object",
                "properties": {"url": {"type": "string"}},
                "required": ["url"],
            },
        },
    },
    "browser_use": {
        "type": "function",
        "function": {
            "name": "browser_use",
            "description": (
                "Drive the Browser Use CLI (browser-use). Actions: status, open, state, "
                "click, type, keys, screenshot, scroll, back. Install the CLI separately."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {"type": "string"},
                    "url": {"type": "string"},
                    "target": {"type": "string"},
                    "text": {"type": "string"},
                    "key": {"type": "string"},
                },
            },
        },
    },
    "cua_desktop": {
        "type": "function",
        "function": {
            "name": "cua_desktop",
            "description": (
                "CUA driver for the host desktop. Actions: status, screenshot, click, type, key. "
                "Install cua-driver. Request approval before destructive OS actions."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {"type": "string"},
                    "text": {"type": "string"},
                    "key": {"type": "string"},
                    "x": {"type": "integer"},
                    "y": {"type": "integer"},
                },
            },
        },
    },
    "system_run": {
        "type": "function",
        "function": {
            "name": "system_run",
            "description": (
                "Run a shell command on this machine (full PATH, cwd = the current "
                "System folder from Computer → System, which can be the project, Home, "
                "Desktop, or any other directory). 60s timeout. Destructive commands "
                "are blocked. computer_run is the isolated sandbox."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {"type": "string"},
                    "cwd": {"type": "string"},
                },
                "required": ["command"],
            },
        },
    },
    "system_ls": {
        "type": "function",
        "function": {
            "name": "system_ls",
            "description": "List a directory on this machine under the current System folder (Computer → System). Use an empty path for the folder root.",
            "parameters": {
                "type": "object",
                "properties": {"path": {"type": "string"}},
            },
        },
    },
    "system_read": {
        "type": "function",
        "function": {
            "name": "system_read",
            "description": "Read a text file on this machine under the current System folder (cap 200k chars).",
            "parameters": {
                "type": "object",
                "properties": {"path": {"type": "string"}},
                "required": ["path"],
            },
        },
    },
    "system_write": {
        "type": "function",
        "function": {
            "name": "system_write",
            "description": (
                "Write a text file on this machine under the current System folder. "
                ".env and swarm.db are protected. Request approval before production changes."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "content": {"type": "string"},
                },
                "required": ["path", "content"],
            },
        },
    },
}

BUILTIN_TOOL_NAMES = tuple(BUILTIN_SCHEMAS.keys())
DEFAULT_BUILTIN_TOOLS = list(BUILTIN_TOOL_NAMES)
LEDGER_BUILTIN_TOOLS = [
    "search_channel_history", "remember", "recall", "channel_digest",
]
COMPUTER_USE_TOOLS = [
    "computer_run", "computer_open", "computer_screenshot",
    "browser_navigate", "browser_snapshot", "browser_click",
    "browser_type", "browser_press", "browser_wait", "browser_screenshot",
]
SYSTEM_TOOLS = [
    "system_run", "system_ls", "system_read", "system_write",
]
COMPOSIO_PLUGIN_TOOLS = [
    "plugin:composio:status",
    "plugin:composio:list_toolkits",
    "plugin:composio:search_tools",
    "plugin:composio:connect",
    "plugin:composio:execute",
]
RESEARCH_TOOLS = [
    "exa_search", "tavily_search", "firecrawl_scrape",
    "browser_use", "cua_desktop",
]

from .. import db  # noqa: E402  — after constants so models can import them

PLUGINS_DIR = Path(__file__).resolve().parent.parent.parent / "plugins"


class ToolRegistry:
    def __init__(self) -> None:
        self._custom: dict[str, dict[str, Any]] = {}
        self._plugin_tools: dict[str, dict[str, Any]] = {}
        self._plugin_meta: list[dict[str, Any]] = []
        self._plugin_modules: dict[str, Any] = {}

    async def refresh(self) -> None:
        self._custom.clear()
        self._plugin_tools.clear()
        self._plugin_meta.clear()
        self._plugin_modules.clear()
        for row in await db.list_custom_tools(enabled_only=True):
            self._custom[row["name"]] = row
        self._load_plugins()

    def _load_plugins(self) -> None:
        if not PLUGINS_DIR.is_dir():
            return
        for manifest_path in sorted(PLUGINS_DIR.glob("*/manifest.json")):
            try:
                data = json.loads(manifest_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            slug = data.get("id") or manifest_path.parent.name
            self._plugin_meta.append({
                "id": slug,
                "name": data.get("name", slug),
                "version": data.get("version", "0.0.0"),
                "description": data.get("description", ""),
                "path": str(manifest_path.parent),
            })
            for tool in data.get("tools") or []:
                name = tool.get("name")
                if not name or not re.match(r"^[a-zA-Z0-9_\-]+$", name):
                    continue
                key = f"plugin:{slug}:{name}"
                self._plugin_tools[key] = {
                    "plugin_id": slug,
                    "tool_name": name,
                    "description": tool.get("description", ""),
                    "parameters": tool.get("parameters") or {"type": "object", "properties": {}},
                    "handler": tool.get("handler") or {},
                    "plugin_dir": str(manifest_path.parent),
                }

    def all_names(self) -> set[str]:
        names = set(BUILTIN_TOOL_NAMES)
        names.update(self._custom.keys())
        names.update(self._plugin_tools.keys())
        return names

    def list_catalog(self) -> list[dict[str, Any]]:
        out = []
        for name in BUILTIN_TOOL_NAMES:
            out.append({"name": name, "kind": "builtin", "description": BUILTIN_SCHEMAS[name]["function"]["description"]})
        for name, row in sorted(self._custom.items()):
            out.append({"name": name, "kind": "custom", "description": row.get("description", ""), "id": row["id"]})
        for name, row in sorted(self._plugin_tools.items()):
            out.append({
                "name": name,
                "kind": "plugin",
                "plugin_id": row["plugin_id"],
                "tool_name": row["tool_name"],
                "description": row.get("description", ""),
            })
        return out

    def plugins(self) -> list[dict[str, Any]]:
        return list(self._plugin_meta)

    def schemas_for(self, names: list[str]) -> list[dict[str, Any]]:
        schemas: list[dict[str, Any]] = []
        for name in names:
            if name in BUILTIN_SCHEMAS:
                schemas.append(BUILTIN_SCHEMAS[name])
            elif name in self._custom:
                row = self._custom[name]
                params = row.get("parameters")
                if isinstance(params, str):
                    try:
                        params = json.loads(params)
                    except json.JSONDecodeError:
                        params = {"type": "object", "properties": {}}
                schemas.append({
                    "type": "function",
                    "function": {
                        "name": name,
                        "description": row.get("description") or f"Custom tool {name}",
                        "parameters": params or {"type": "object", "properties": {}},
                    },
                })
            elif name in self._plugin_tools:
                row = self._plugin_tools[name]
                schemas.append({
                    "type": "function",
                    "function": {
                        "name": name,
                        "description": row.get("description") or name,
                        "parameters": row.get("parameters") or {"type": "object", "properties": {}},
                    },
                })
        return schemas

    def filter_allowed(self, names: list[str]) -> list[str]:
        valid = self.all_names()
        return [n for n in names if n in valid]

    async def execute(
        self,
        name: str,
        args: dict[str, Any],
        *,
        agent_name: str,
        channel_id: str,
        allowed: list[str],
        sandbox_dir: str,
        shell_runner: Callable[[str], str],
        workspace_helpers: dict[str, Callable[..., Any]],
    ) -> str:
        if name not in allowed:
            return f"(tool {name} is disabled for this agent)"
        if name in BUILTIN_SCHEMAS:
            return await self._exec_builtin(
                name, args, agent_name=agent_name, channel_id=channel_id,
                sandbox_dir=sandbox_dir, shell_runner=shell_runner,
                workspace_helpers=workspace_helpers,
            )
        if name in self._custom:
            return await self._exec_custom(self._custom[name], args)
        if name in self._plugin_tools:
            return await self._exec_plugin(
                self._plugin_tools[name],
                args,
                agent_name=agent_name,
                channel_id=channel_id,
            )
        return f"(unknown tool {name})"

    async def _exec_builtin(
        self,
        name: str,
        args: dict[str, Any],
        *,
        agent_name: str,
        channel_id: str,
        sandbox_dir: str,
        shell_runner: Callable[[str], str],
        workspace_helpers: dict[str, Callable[..., Any]],
    ) -> str:
        if name == "read_only_shell":
            return shell_runner(args.get("command", ""))
        if name == "search_channel_history":
            rows = await db.search_history(channel_id, args.get("query", ""), limit=10)
            if not rows:
                return "(no matches)"
            return "\n".join(f"[{r['author']}] {r['body'][:200]}" for r in rows)
        if name == "remember":
            body = (args.get("body") or "").strip()
            if not body:
                return "(nothing to remember)"
            scope = args.get("scope") or "channel"
            scoped = None if scope == "global" else channel_id
            row = await db.add_memory(agent_name, body[:2000], channel_id=scoped, kind="note")
            where = "globally" if scoped is None else f"in #{channel_id}"
            return f"remembered {where} (id {row['id']})"
        if name == "recall":
            rows = await db.search_memory(agent_name, args.get("query", ""), channel_id=channel_id, limit=10)
            if not rows:
                return "(no matching notes)"
            lines = []
            for r in rows:
                scope = "global" if r["channel_id"] is None else f"#{r['channel_id']}"
                lines.append(f"[{scope}] {r['body'][:200]}")
            return "\n".join(lines)
        if name == "list_workspace":
            return await workspace_helpers["list"]()
        if name == "read_workspace":
            return await workspace_helpers["read"](args.get("path", ""))
        if name == "write_workspace":
            return await workspace_helpers["write"](args.get("path", ""), args.get("content", ""))
        if name == "fetch_url":
            return _fetch_url_text(args.get("url", ""))
        if name == "channel_digest":
            limit = int(args.get("limit") or 12)
            limit = max(1, min(limit, 30))
            rows = await db.get_history(channel_id, limit)
            lines = []
            for r in rows:
                if r.get("author_kind") == "system":
                    continue
                lines.append(f"{r['author']}: {(r.get('body') or '')[:160]}")
            return "\n".join(lines) if lines else "(no messages)"
        if name == "save_skill":
            return await workspace_helpers["save_skill"](args.get("name", ""), args.get("body", ""))
        if name == "request_approval":
            return await workspace_helpers["approval"](
                agent_name, channel_id, args.get("action", ""), args.get("detail", ""),
            )
        if name == "computer_run":
            from . import computer
            return computer.computer_run(args.get("command", ""))
        if name == "computer_open":
            from . import computer
            return computer.computer_open(args.get("target", ""))
        if name == "computer_screenshot":
            from . import computer
            return computer.computer_screenshot()
        if name == "browser_navigate":
            from . import browser
            return await browser.navigate(args.get("url", ""))
        if name == "browser_snapshot":
            from . import browser
            return await browser.snapshot()
        if name == "browser_click":
            from . import browser
            return await browser.click(args.get("selector", ""))
        if name == "browser_type":
            from . import browser
            return await browser.type_text(args.get("selector", ""), args.get("text", ""))
        if name == "browser_press":
            from . import browser
            return await browser.press(args.get("key", ""))
        if name == "browser_wait":
            from . import browser
            return await browser.wait(args.get("ms") or 1000)
        if name == "browser_screenshot":
            from . import browser
            return await browser.screenshot()
        if name == "exa_search":
            from . import connectors
            return await connectors.exa_search(
                args.get("query", ""), num_results=args.get("num_results") or 5,
            )
        if name == "tavily_search":
            from . import connectors
            return await connectors.tavily_search(
                args.get("query", ""), max_results=args.get("max_results") or 5,
            )
        if name == "firecrawl_scrape":
            from . import connectors
            return await connectors.firecrawl_scrape(args.get("url", ""))
        if name == "browser_use":
            from . import connectors
            return await connectors.browser_use_cli(
                args.get("action") or "status",
                url=args.get("url") or "",
                target=args.get("target") or "",
                text=args.get("text") or "",
                key=args.get("key") or "",
            )
        if name == "cua_desktop":
            from . import connectors
            return await connectors.cua_desktop(
                args.get("action") or "status",
                text=args.get("text") or "",
                key=args.get("key") or "",
                x=args.get("x"),
                y=args.get("y"),
            )
        if name == "system_run":
            from . import system as system_mod
            return system_mod.system_run(args.get("command", ""), cwd=args.get("cwd") or "")
        if name == "system_ls":
            from . import system as system_mod
            return system_mod.system_ls(args.get("path") or "")
        if name == "system_read":
            from . import system as system_mod
            return system_mod.system_read(args.get("path", ""))
        if name == "system_write":
            from . import system as system_mod
            return system_mod.system_write(args.get("path", ""), args.get("content", ""))
        return f"(unimplemented builtin {name})"

    async def _exec_custom(self, row: dict[str, Any], args: dict[str, Any]) -> str:
        handler = row.get("handler_type") or "template"
        config = row.get("handler_config") or {}
        if isinstance(config, str):
            try:
                config = json.loads(config)
            except json.JSONDecodeError:
                config = {}
        if handler == "template":
            tpl = config.get("template") or row.get("description") or "ok"
            try:
                return tpl.format(**args)
            except (KeyError, ValueError) as exc:
                return f"(template error: {exc})"
        if handler == "http_get":
            url = (config.get("url") or "").format(**{k: str(v) for k, v in args.items()})
            return _fetch_url_text(url)
        if handler == "echo":
            return json.dumps(args, ensure_ascii=False)[:4000]
        return f"(unknown custom handler {handler})"

    async def _exec_plugin(
        self,
        row: dict[str, Any],
        args: dict[str, Any],
        *,
        agent_name: str,
        channel_id: str,
    ) -> str:
        handler = row.get("handler") or {}
        htype = handler.get("type") or "template"
        if htype == "template":
            tpl = handler.get("template") or "plugin ok"
            try:
                return tpl.format(**args)
            except (KeyError, ValueError) as exc:
                return f"(plugin template error: {exc})"
        if htype == "http_get":
            url = (handler.get("url") or "").format(**{k: str(v) for k, v in args.items()})
            return _fetch_url_text(url)
        if htype == "shell":
            cmd = (handler.get("command") or "").format(**{k: str(v) for k, v in args.items()})
            try:
                result = subprocess.run(
                    cmd, shell=True, capture_output=True, text=True, timeout=10,
                    cwd=handler.get("cwd") or None,
                )
                out = (result.stdout or "") + (result.stderr or "")
                return out[:4000] or "(no output)"
            except Exception as exc:  # noqa: BLE001
                return f"(plugin shell error: {exc})"
        if htype == "python":
            return await self._exec_python_plugin(
                row, args, agent_name=agent_name, channel_id=channel_id,
            )
        return "(unsupported plugin handler)"

    async def _exec_python_plugin(
        self,
        row: dict[str, Any],
        args: dict[str, Any],
        *,
        agent_name: str,
        channel_id: str,
    ) -> str:
        handler = row.get("handler") or {}
        plugin_dir = Path(row.get("plugin_dir") or "")
        module_name = handler.get("module") or "handler"
        func_name = handler.get("function") or row.get("tool_name") or "run"
        path = plugin_dir / f"{module_name}.py"
        if not path.is_file():
            return f"(plugin handler missing: {path})"
        cache_key = f"{row.get('plugin_id')}:{module_name}:{path}"
        try:
            mod = self._plugin_modules.get(cache_key)
            if mod is None:
                ident = f"swarm_plugin_{row.get('plugin_id')}_{module_name}"
                spec = importlib.util.spec_from_file_location(ident, path)
                if spec is None or spec.loader is None:
                    return f"(could not load plugin module {path})"
                mod = importlib.util.module_from_spec(spec)
                sys.modules[ident] = mod
                spec.loader.exec_module(mod)
                self._plugin_modules[cache_key] = mod
            fn = getattr(mod, func_name, None)
            if fn is None or not callable(fn):
                return f"(plugin function {func_name} not found)"
            call_kwargs = _plugin_call_kwargs(
                fn, args, agent_name=agent_name, channel_id=channel_id,
            )
            result = fn(**call_kwargs)
            if inspect.isawaitable(result):
                result = await result
        except Exception as exc:  # noqa: BLE001
            return f"(plugin python error: {exc})"
        if result is None:
            return "(empty plugin result)"
        if isinstance(result, str):
            return result[:8000]
        try:
            return json.dumps(result, default=str, ensure_ascii=False)[:8000]
        except TypeError:
            return str(result)[:8000]


def _plugin_call_kwargs(
    fn: Callable[..., Any],
    args: dict[str, Any],
    *,
    agent_name: str,
    channel_id: str,
) -> dict[str, Any]:
    params = inspect.signature(fn).parameters
    accepts_var_kw = any(p.kind == inspect.Parameter.VAR_KEYWORD for p in params.values())
    extra = {"agent_name": agent_name, "channel_id": channel_id}
    merged = {**args, **extra}
    if accepts_var_kw:
        return merged
    return {k: v for k, v in merged.items() if k in params}


def _fetch_url_text(url: str) -> str:
    text = (url or "").strip()
    if not text.startswith(("http://", "https://")):
        return "(url must start with http:// or https://)"
    try:
        req = urllib.request.Request(text, headers={"User-Agent": "swarm/1.0"})
        with urllib.request.urlopen(req, timeout=10) as resp:  # noqa: S310
            raw = resp.read(8192)
            charset = resp.headers.get_content_charset() or "utf-8"
            return raw.decode(charset, errors="replace")[:8000]
    except urllib.error.URLError as exc:
        return f"(fetch error: {exc.reason})"
    except Exception as exc:  # noqa: BLE001
        return f"(fetch error: {exc})"


_registry: ToolRegistry | None = None


def get_registry() -> ToolRegistry:
    global _registry
    if _registry is None:
        _registry = ToolRegistry()
    return _registry


async def reload_registry() -> ToolRegistry:
    reg = get_registry()
    await reg.refresh()
    return reg
