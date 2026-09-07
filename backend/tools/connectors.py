"""Workspace connectors: Exa, Tavily, Firecrawl, Browser Use CLI, CUA drivers.

API keys are stored the same way as Composio (encrypted SQLite + env fallback).
CLI connectors (browser-use, cua-driver) need a local binary/SDK, not a key.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import urllib.error
import urllib.request
from typing import Any

CLI_TIMEOUT = 30
OUTPUT_CAP = 8000

CATALOG: list[dict[str, Any]] = [
    {
        "id": "composio",
        "name": "Composio",
        "kind": "apps",
        "env_fallback": "COMPOSIO_API_KEY",
        "key_url": "https://platform.composio.dev",
        "note": "Gmail, Slack, GitHub, Notion, and 1000+ app toolkits.",
        "tools": [
            "plugin:composio:status", "plugin:composio:list_toolkits",
            "plugin:composio:search_tools", "plugin:composio:connect",
            "plugin:composio:execute",
        ],
    },
    {
        "id": "exa",
        "name": "Exa",
        "kind": "search",
        "env_fallback": "EXA_API_KEY",
        "key_url": "https://dashboard.exa.ai/api-keys",
        "note": "Neural web search for research and citations.",
        "tools": ["exa_search"],
    },
    {
        "id": "tavily",
        "name": "Tavily",
        "kind": "search",
        "env_fallback": "TAVILY_API_KEY",
        "key_url": "https://app.tavily.com",
        "note": "LLM-oriented web search with an optional short answer.",
        "tools": ["tavily_search"],
    },
    {
        "id": "firecrawl",
        "name": "Firecrawl",
        "kind": "crawl",
        "env_fallback": "FIRECRAWL_API_KEY",
        "key_url": "https://www.firecrawl.dev/app",
        "note": "Scrape a URL to clean markdown for the Bot to read.",
        "tools": ["firecrawl_scrape"],
    },
    {
        "id": "browser_use",
        "name": "Browser Use CLI",
        "kind": "cli",
        "env_fallback": None,
        "key_url": "https://docs.browser-use.com/open-source/browser-use-cli",
        "note": "Install `browser-use` locally. Agents call allowlisted CLI actions.",
        "tools": ["browser_use"],
        "binary": "browser-use",
    },
    {
        "id": "cua",
        "name": "CUA Driver",
        "kind": "cli",
        "env_fallback": None,
        "key_url": "https://cua.ai/docs/cua/guide/get-started/using-computer-sdk",
        "note": "Host-desktop computer-use via `cua-driver` or the `cua_driver` SDK.",
        "tools": ["cua_desktop"],
        "binary": "cua-driver",
        "package": "cua-driver",
    },
]

_BY_ID = {row["id"]: row for row in CATALOG}


def get_connector(connector_id: str) -> dict[str, Any] | None:
    return _BY_ID.get((connector_id or "").strip())


async def _key(connector_id: str) -> str | None:
    spec = get_connector(connector_id)
    if spec is None:
        return None
    env_name = spec.get("env_fallback")
    from ..ai_support import store
    return await store.resolve_key(connector_id, env_fallback=env_name)


def _not_connected(name: str, env_name: str) -> str:
    return (
        f"({name} not connected — set {env_name} or Computer → Apps)"
    )


def _http_json(
    method: str,
    url: str,
    *,
    headers: dict[str, str],
    body: dict[str, Any] | None = None,
    timeout: int = 30,
) -> Any:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310
            raw = resp.read().decode("utf-8", errors="replace")
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:800]
        raise RuntimeError(f"HTTP {exc.code}: {detail}") from exc
    except urllib.error.URLError as extra:
        raise RuntimeError(f"network error: {extra.reason}") from extra


def _which(name: str) -> str | None:
    return shutil.which(name)


def _cli_available(binary: str) -> bool:
    return bool(_which(binary))


def _sdk_available(mod: str) -> bool:
    try:
        __import__(mod)
        return True
    except ImportError:
        return False


def _run_argv(argv: list[str], *, cwd: str | None = None) -> str:
    try:
        result = subprocess.run(
            argv,
            capture_output=True,
            text=True,
            timeout=CLI_TIMEOUT,
            cwd=cwd,
            shell=False,
        )
    except FileNotFoundError:
        return f"(command not found: {argv[0]})"
    except subprocess.TimeoutExpired:
        return f"(timed out after {CLI_TIMEOUT}s)"
    except Exception as exc:  # noqa: BLE001
        return f"(cli error: {exc})"
    out = (result.stdout or "") + (result.stderr or "")
    body = (out.strip() or "(no output)")[:OUTPUT_CAP]
    if result.returncode:
        return f"(exit {result.returncode})\n{body}"
    return body


async def catalog_status() -> list[dict[str, Any]]:
    from ..ai_support import store
    stored = {row["provider_id"]: row for row in await store.status()}
    out = []
    for spec in CATALOG:
        item = dict(spec)
        cid = spec["id"]
        if spec["kind"] == "cli":
            binary = spec.get("binary") or cid
            pkg = spec.get("package")
            ready = _cli_available(binary) or (bool(pkg) and _sdk_available(pkg.replace("-", "_")))
            item["connected"] = ready
            item["key_hint"] = "cli" if _cli_available(binary) else ("sdk" if ready else "")
            item["ready"] = ready
        else:
            key = await _key(cid)
            row = stored.get(cid)
            item["connected"] = bool(key)
            item["key_hint"] = (row or {}).get("key_hint") or ("env" if key else "")
            item["ready"] = bool(key)
        out.append(item)
    return out


async def connector_status(connector_id: str) -> dict[str, Any]:
    spec = get_connector(connector_id)
    if spec is None:
        raise KeyError(connector_id)
    rows = await catalog_status()
    return next(r for r in rows if r["id"] == connector_id)


# ---------------------------------------------------------------- Exa ----

async def exa_search(query: str, *, num_results: int = 5) -> str:
    key = await _key("exa")
    if not key:
        return _not_connected("Exa", "EXA_API_KEY")
    q = (query or "").strip()
    if len(q) < 2:
        return "(need a search query)"
    try:
        n = max(1, min(int(num_results or 5), 10))
    except (TypeError, ValueError):
        n = 5
    try:
        data = _http_json(
            "POST",
            "https://api.exa.ai/search",
            headers={
                "Content-Type": "application/json",
                "x-api-key": key,
                "Authorization": f"Bearer {key}",
            },
            body={
                "query": q,
                "numResults": n,
                "type": "auto",
                "contents": {"text": True, "highlights": True},
            },
        )
    except Exception as exc:  # noqa: BLE001
        return f"(exa search error: {exc})"
    rows = data.get("results") or data.get("data") or []
    lines = []
    for row in rows[:n]:
        title = row.get("title") or "(untitled)"
        url = row.get("url") or ""
        snippet = (
            row.get("text")
            or " ".join(row.get("highlights") or [])
            or row.get("summary")
            or ""
        )
        snippet = str(snippet).replace("\n", " ").strip()[:280]
        lines.append(f"- {title}\n  {url}\n  {snippet}".rstrip())
    return "\n".join(lines) if lines else "(no exa results)"


# ------------------------------------------------------------- Tavily ----

async def tavily_search(query: str, *, max_results: int = 5) -> str:
    key = await _key("tavily")
    if not key:
        return _not_connected("Tavily", "TAVILY_API_KEY")
    q = (query or "").strip()
    if len(q) < 2:
        return "(need a search query)"
    try:
        n = max(1, min(int(max_results or 5), 10))
    except (TypeError, ValueError):
        n = 5
    try:
        data = _http_json(
            "POST",
            "https://api.tavily.com/search",
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {key}",
            },
            body={"query": q, "max_results": n, "include_answer": True},
        )
    except Exception as exc:  # noqa: BLE001
        return f"(tavily search error: {exc})"
    lines = []
    answer = data.get("answer")
    if answer:
        lines.append(f"Answer: {str(answer).strip()[:800]}")
    for row in (data.get("results") or [])[:n]:
        title = row.get("title") or "(untitled)"
        url = row.get("url") or ""
        snippet = str(row.get("content") or "").replace("\n", " ").strip()[:280]
        lines.append(f"- {title}\n  {url}\n  {snippet}".rstrip())
    return "\n".join(lines) if lines else "(no tavily results)"


# ---------------------------------------------------------- Firecrawl ----

async def firecrawl_scrape(url: str) -> str:
    key = await _key("firecrawl")
    if not key:
        return _not_connected("Firecrawl", "FIRECRAWL_API_KEY")
    target = (url or "").strip()
    if not target.startswith(("http://", "https://")):
        return "(url must start with http:// or https://)"
    try:
        data = _http_json(
            "POST",
            "https://api.firecrawl.dev/v1/scrape",
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {key}",
            },
            body={"url": target, "formats": ["markdown"]},
        )
    except Exception as exc:  # noqa: BLE001
        return f"(firecrawl scrape error: {exc})"
    payload = data.get("data") if isinstance(data, dict) else None
    if isinstance(payload, dict):
        text = payload.get("markdown") or payload.get("content") or payload.get("html") or ""
    else:
        text = ""
    if not text:
        try:
            return json.dumps(data, default=str, ensure_ascii=False)[:OUTPUT_CAP]
        except TypeError:
            return str(data)[:OUTPUT_CAP]
    return str(text)[:OUTPUT_CAP]


# ------------------------------------------------------ Browser Use CLI --

_BROWSER_USE_ACTIONS = {
    "status": (),
    "open": ("url",),
    "state": (),
    "click": ("target",),
    "type": ("text",),
    "keys": ("key",),
    "screenshot": (),
    "scroll": ("target",),
    "back": (),
}


def browser_use_binary() -> str | None:
    return _which("browser-use") or _which("browser_use")


async def browser_use_cli(
    action: str = "status",
    *,
    url: str = "",
    target: str = "",
    text: str = "",
    key: str = "",
) -> str:
    binary = browser_use_binary()
    if not binary:
        return (
            "(browser-use CLI not installed — `uv tool install browser-use` "
            "or `pip install browser-use`, then retry)"
        )
    act = (action or "status").strip().lower()
    if act not in _BROWSER_USE_ACTIONS:
        return f"(unknown browser-use action {act}; use {', '.join(_BROWSER_USE_ACTIONS)})"
    if act == "status":
        return _run_argv([binary, "--help"])[:2000]
    argv = [binary, act]
    if act == "open":
        if not url.startswith(("http://", "https://")):
            return "(url must start with http:// or https://)"
        argv.append(url)
    elif act == "click":
        if not (target or "").strip():
            return "(need a click target — element index or coordinates)"
        argv.append(target.strip())
    elif act == "type":
        if not text:
            return "(need text to type)"
        argv.append(text)
    elif act == "keys":
        argv.append((key or "Enter").strip() or "Enter")
    elif act == "scroll":
        argv.append((target or "down").strip() or "down")
    elif act == "screenshot":
        from .computer import sandbox_dir
        dest = sandbox_dir() / "screenshots"
        dest.mkdir(parents=True, exist_ok=True)
        argv.append(str(dest / "browser-use.png"))
    return _run_argv(argv)


# ----------------------------------------------------------- CUA driver --

def cua_available() -> bool:
    return bool(_which("cua-driver")) or _sdk_available("cua_driver")


async def cua_desktop(
    action: str = "status",
    *,
    text: str = "",
    key: str = "",
    x: int | None = None,
    y: int | None = None,
) -> str:
    act = (action or "status").strip().lower()
    if act == "status":
        bits = []
        path = _which("cua-driver")
        bits.append(f"cli: {path or 'not on PATH'}")
        bits.append("sdk: " + ("yes" if _sdk_available("cua_driver") else "no"))
        if not cua_available():
            bits.append(
                "Install with `pip install cua-driver` or put `cua-driver` on PATH. "
                "CUA drives the host desktop (apps, signed-in sessions)."
            )
        return "\n".join(bits)
    if not cua_available():
        return (
            "(CUA driver not installed — `pip install cua-driver` "
            "or install the `cua-driver` CLI, then retry)"
        )
    if act == "screenshot":
        sdk_result = await _cua_sdk_screenshot()
        if sdk_result is not None:
            return sdk_result
        binary = _which("cua-driver")
        if binary:
            return _run_argv([binary, "--help"])[:1500] + (
                "\n(SDK screenshot unavailable; CLI is present — install matching cua-driver SDK)"
            )
        return "(cua screenshot unavailable)"
    if act in ("click", "type", "key"):
        where = f" @({x},{y})" if x is not None and y is not None else ""
        extra = text or key or ""
        return (
            f"(CUA {act}{where} {extra} needs the cua_driver SDK session on this host. "
            "Call cua_desktop with action=status, then screenshot. "
            "For sends or destructive OS actions, use request_approval first.)"
        )
    return f"(unknown cua action {act}; use status, screenshot, click, type, key)"


async def _cua_sdk_screenshot() -> str | None:
    try:
        from cua_driver import CuaDriver, EndSessionInput, GetDesktopStateInput, StartSessionInput
    except ImportError:
        return None
    from .computer import sandbox_dir
    driver = None
    try:
        driver = CuaDriver.create()
        await driver.start_session(
            StartSessionInput(session="swarm", capture_scope=None, cursor_theme=None)
        )
        folder = sandbox_dir() / "screenshots"
        folder.mkdir(parents=True, exist_ok=True)
        dest = folder / "cua-desktop.png"
        desktop = await driver.get_desktop_state(
            GetDesktopStateInput(session="swarm", screenshot_out_file=str(dest))
        )
        mime = ""
        if getattr(desktop, "images", None):
            mime = getattr(desktop.images[0], "mime_type", "") or ""
        size = dest.stat().st_size if dest.is_file() else 0
        return f"saved screenshots/cua-desktop.png ({size} bytes) {mime}".strip()
    except Exception as exc:  # noqa: BLE001
        return f"(cua sdk error: {exc})"
    finally:
        if driver is not None:
            try:
                await driver.end_session(EndSessionInput(session="swarm"))
            except Exception:  # noqa: BLE001
                pass
            try:
                await driver.shutdown()
            except Exception:  # noqa: BLE001
                pass
