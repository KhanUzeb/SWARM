"""Python handlers for plugin:composio:* — thin wrappers over composio_client."""
from __future__ import annotations

from typing import Any

from backend.tools import composio_client


async def status(**_: Any) -> str:
    data = await composio_client.status()
    connected = "connected" if data.get("connected") else "not connected"
    sdk = "sdk" if data.get("sdk") else "REST"
    hint = data.get("key_hint") or "no key"
    return (
        f"Composio is {connected} ({sdk}). "
        f"workspace user_id={data.get('user_id')} key={hint}. "
        "Use list_toolkits / search_tools / connect / execute. "
        "Admins paste a key in Computer → Apps if this is not connected."
    )


async def list_toolkits(query: str = "", **_: Any) -> str:
    return await composio_client.list_toolkits(query)


async def search_tools(query: str = "", **_: Any) -> str:
    return await composio_client.search_tools(query)


async def connect(toolkit: str = "", **_: Any) -> str:
    return await composio_client.connect_toolkit(toolkit)


async def execute(tool_slug: str = "", arguments: Any = None, **_: Any) -> str:
    return await composio_client.execute(tool_slug, arguments)
