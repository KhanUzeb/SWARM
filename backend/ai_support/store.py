"""AI provider credential storage (importable package)."""
from __future__ import annotations

import base64
import hashlib
import os
from typing import Any

from .. import db
from .providers import get_provider

_SECRET = (os.environ.get("SWARM_SECRET") or "swarm-local-dev-secret").encode()


def _seal(raw: str) -> str:
    key = hashlib.sha256(_SECRET).digest()
    data = raw.encode("utf-8")
    xored = bytes(b ^ key[i % len(key)] for i, b in enumerate(data))
    return base64.urlsafe_b64encode(xored).decode("ascii")


def _unseal(token: str) -> str:
    key = hashlib.sha256(_SECRET).digest()
    data = base64.urlsafe_b64decode(token.encode("ascii"))
    plain = bytes(b ^ key[i % len(key)] for i, b in enumerate(data))
    return plain.decode("utf-8")


async def connect(provider_id: str, api_key: str, *, model: str | None = None) -> dict[str, Any]:
    sealed = _seal(api_key.strip())
    return await db.upsert_ai_provider(provider_id, sealed, model=model)


async def disconnect(provider_id: str) -> bool:
    return await db.delete_ai_provider(provider_id)


async def status() -> list[dict[str, Any]]:
    rows = await db.list_ai_providers()
    return [
        {
            "provider_id": r["provider_id"],
            "connected": True,
            "model": r.get("model"),
            "connected_at": r.get("connected_at"),
            "key_hint": r.get("key_hint"),
        }
        for r in rows
    ]


async def resolve_key(provider_id: str, *, env_fallback: str | None = None) -> str | None:
    row = await db.get_ai_provider(provider_id)
    if row and row.get("secret"):
        try:
            return _unseal(row["secret"])
        except Exception:  # noqa: BLE001
            pass
    if env_fallback:
        val = (os.environ.get(env_fallback) or "").strip().strip('"').strip("'")
        return val or None
    return None


async def get_connection_model(provider_id: str) -> str | None:
    row = await db.get_ai_provider(provider_id)
    if row and row.get("model"):
        return str(row["model"])
    return None


async def set_model(provider_id: str, model: str) -> dict[str, Any] | None:
    row = await db.update_ai_provider_model(provider_id, model)
    if row is None:
        return None
    return {
        "provider_id": row["provider_id"],
        "connected": True,
        "model": row.get("model"),
        "connected_at": row.get("connected_at"),
        "key_hint": row.get("key_hint"),
    }


async def primary_llm_key() -> tuple[str | None, str]:
    from .resolver import resolve_runtime_auth
    from .providers import providers_by_priority

    for spec in providers_by_priority():
        auth = await resolve_runtime_auth(spec["id"])
        if auth:
            return auth.api_key, spec["id"]
    return None, "groq"
