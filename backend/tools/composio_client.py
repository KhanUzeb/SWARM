"""Composio integration — 1000+ app toolkits behind a few meta-tools.

Uses the official `composio` SDK when installed, otherwise the v3.1 REST API.
A single workspace user_id shares Gmail/Slack/GitHub/etc. across every Bot.
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

API_ROOT = "https://backend.composio.dev/api/v3.1"
_session_cache: dict[str, Any] = {}


def user_id() -> str:
    return (os.environ.get("COMPOSIO_USER_ID") or "swarm-workspace").strip() or "swarm-workspace"


async def api_key() -> str | None:
    from ..ai_support import store
    return await store.resolve_key("composio", env_fallback="COMPOSIO_API_KEY")


async def configured() -> bool:
    return bool(await api_key())


def _headers(key: str) -> dict[str, str]:
    return {
        "x-api-key": key,
        "Content-Type": "application/json",
        "Accept": "application/json",
    }


def _rest(method: str, path: str, key: str, body: dict | None = None, query: dict | None = None) -> Any:
    url = API_ROOT + path
    if query:
        url += "?" + urllib.parse.urlencode({k: v for k, v in query.items() if v is not None})
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers=_headers(key))
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:  # noqa: S310
            raw = resp.read().decode("utf-8", errors="replace")
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:800]
        raise RuntimeError(f"composio HTTP {exc.code}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"composio network error: {exc.reason}") from exc


async def _sdk():
    key = await api_key()
    if not key:
        return None
    try:
        from composio import Composio
    except ImportError:
        return None
    return Composio(api_key=key)


def _format(result: Any) -> str:
    if result is None:
        return "(empty composio result)"
    if isinstance(result, str):
        return result[:8000]
    try:
        return json.dumps(result, default=str, ensure_ascii=False)[:8000]
    except TypeError:
        return str(result)[:8000]


async def status() -> dict[str, Any]:
    key = await api_key()
    sdk = False
    try:
        import composio  # noqa: F401
        sdk = True
    except ImportError:
        sdk = False
    return {
        "connected": bool(key),
        "user_id": user_id(),
        "sdk": sdk,
        "key_hint": f"…{key[-4:]}" if key and len(key) > 4 else ("set" if key else ""),
    }


async def list_toolkits(query: str = "") -> str:
    key = await api_key()
    if not key:
        return "(composio not connected — set COMPOSIO_API_KEY or Computer → Apps)"
    q = (query or "").strip().lower()
    sdk = await _sdk()
    try:
        if sdk is not None and hasattr(sdk, "toolkits"):
            items = sdk.toolkits.get()
            rows = items if isinstance(items, list) else getattr(items, "items", None) or []
            names = []
            for row in rows:
                if isinstance(row, dict):
                    slug = row.get("slug") or row.get("name") or ""
                    name = row.get("name") or slug
                else:
                    slug = getattr(row, "slug", "") or getattr(row, "name", "")
                    name = getattr(row, "name", slug)
                if q and q not in str(slug).lower() and q not in str(name).lower():
                    continue
                names.append(f"{slug} — {name}")
                if len(names) >= 40:
                    break
            return "\n".join(names) if names else "(no toolkits matched)"
        data = _rest("GET", "/toolkits", key, query={"limit": 50, "search": query or None})
        items = data.get("items") or data.get("toolkits") or data.get("data") or []
        names = []
        for row in items:
            slug = row.get("slug") or row.get("name") or ""
            name = row.get("name") or slug
            names.append(f"{slug} — {name}")
        return "\n".join(names[:40]) if names else "(no toolkits returned)"
    except Exception as exc:  # noqa: BLE001
        return f"(composio list error: {exc})"


async def search_tools(query: str) -> str:
    key = await api_key()
    if not key:
        return "(composio not connected — set COMPOSIO_API_KEY or Computer → Apps)"
    q = (query or "").strip()
    if len(q) < 2:
        return "(need a search query, e.g. 'gmail send' or 'github issues')"
    sdk = await _sdk()
    try:
        if sdk is not None and hasattr(sdk, "tools"):
            found = sdk.tools.get(search=q, limit=15)
            rows = found if isinstance(found, list) else getattr(found, "items", None) or []
            lines = []
            for row in rows:
                if isinstance(row, dict):
                    slug = row.get("slug") or row.get("name")
                    desc = row.get("description") or ""
                else:
                    slug = getattr(row, "slug", None) or getattr(row, "name", "")
                    desc = getattr(row, "description", "")
                lines.append(f"{slug}: {str(desc)[:160]}")
            return "\n".join(lines[:15]) if lines else "(no tools matched — try a toolkit name like gmail, github, notion)"
        data = _rest("GET", "/tools", key, query={"search": q, "limit": 15})
        items = data.get("items") or data.get("tools") or data.get("data") or []
        lines = []
        for row in items:
            slug = row.get("slug") or row.get("name")
            desc = row.get("description") or ""
            lines.append(f"{slug}: {str(desc)[:160]}")
        return "\n".join(lines) if lines else "(no tools matched)"
    except Exception as exc:  # noqa: BLE001
        return f"(composio search error: {exc})"


async def connect_toolkit(toolkit: str) -> str:
    key = await api_key()
    if not key:
        return "(composio not connected — set COMPOSIO_API_KEY or Computer → Apps)"
    slug = (toolkit or "").strip().lower()
    if not slug:
        return "(need a toolkit slug, e.g. gmail, github, slack, notion)"
    sdk = await _sdk()
    try:
        if sdk is not None and hasattr(sdk, "connected_accounts"):
            link = sdk.connected_accounts.link(user_id=user_id(), toolkit=slug)
            url = getattr(link, "redirect_url", None) or getattr(link, "url", None) or link
            if isinstance(url, dict):
                url = url.get("redirect_url") or url.get("url")
            return (
                f"Connect {slug} in the browser, then tell the Bot to continue:\n{url}"
            )
        data = _rest(
            "POST",
            "/connected_accounts",
            key,
            body={"user_id": user_id(), "toolkit": {"slug": slug}},
        )
        url = (
            data.get("redirect_url")
            or data.get("url")
            or (data.get("data") or {}).get("redirect_url")
        )
        if not url:
            return _format(data)
        return f"Connect {slug} in the browser, then tell the Bot to continue:\n{url}"
    except Exception as exc:  # noqa: BLE001
        return f"(composio connect error: {exc})"


async def execute(tool_slug: str, arguments: Any = None) -> str:
    key = await api_key()
    if not key:
        return "(composio not connected — set COMPOSIO_API_KEY or Computer → Apps)"
    slug = (tool_slug or "").strip()
    if not slug:
        return "(need a tool slug from search_tools, e.g. GMAIL_SEND_EMAIL)"
    args = arguments if isinstance(arguments, dict) else {}
    if isinstance(arguments, str) and arguments.strip():
        try:
            parsed = json.loads(arguments)
            if isinstance(parsed, dict):
                args = parsed
        except json.JSONDecodeError:
            return "(arguments must be a JSON object)"
    sdk = await _sdk()
    try:
        if sdk is not None:
            session = _session_cache.get("sdk")
            if session is None and hasattr(sdk, "create"):
                session = sdk.create(user_id=user_id())
                _session_cache["sdk"] = session
            if session is not None and hasattr(session, "execute"):
                result = session.execute(slug, arguments=args)
                return _format(result)
            if hasattr(sdk, "tools") and hasattr(sdk.tools, "execute"):
                result = sdk.tools.execute(slug, arguments=args, user_id=user_id())
                return _format(result)
        data = _rest(
            "POST",
            f"/tools/execute/{urllib.parse.quote(slug)}",
            key,
            body={"user_id": user_id(), "arguments": args},
        )
        return _format(data)
    except Exception as exc:  # noqa: BLE001
        return f"(composio execute error: {exc})"
