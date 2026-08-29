"""AI provider credential storage (importable package)."""
from __future__ import annotations

import base64
import hashlib
import os
import time
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


async def connect_oauth(provider_id: str, access_token: str, refresh_token: str | None, expires_in: int | float | None, *, model: str | None = None) -> dict[str, Any]:
    access = _seal(access_token.strip())
    refresh = _seal(refresh_token.strip()) if refresh_token else None
    expires_at = time.time() + float(expires_in) if expires_in else None
    return await db.update_ai_provider_oauth(provider_id, access, refresh, expires_at, model=model)


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
        if row.get("auth_method") == "oauth" and row.get("expires_at") and float(row["expires_at"]) <= time.time() + 60 and row.get("refresh_secret"):
            refreshed = await _refresh_oauth(provider_id, row)
            if refreshed:
                return refreshed
        try:
            return _unseal(row["secret"])
        except Exception:  # noqa: BLE001
            pass
    if env_fallback:
        val = (os.environ.get(env_fallback) or "").strip().strip('"').strip("'")
        return val or None
    return None


async def _refresh_oauth(provider_id: str, row: dict[str, Any]) -> str | None:
    from .providers import get_provider
    spec = get_provider(provider_id) or {}
    token_url = (spec.get("oauth_token_url") or "").strip()
    client_id = (os.environ.get(f"SWARM_{provider_id.upper()}_OAUTH_CLIENT_ID") or "").strip()
    client_secret = (os.environ.get(f"SWARM_{provider_id.upper()}_OAUTH_CLIENT_SECRET") or "").strip()
    if not token_url or not client_id or not row.get("refresh_secret"):
        return None
    try:
        refresh = _unseal(row["refresh_secret"])
        import httpx
        async with httpx.AsyncClient(timeout=20) as client:
            response = await client.post(token_url, data={"grant_type": "refresh_token", "refresh_token": refresh, "client_id": client_id, "client_secret": client_secret})
            response.raise_for_status()
            body = response.json()
        access = str(body.get("access_token") or "").strip()
        if len(access) < 8:
            return None
        await connect_oauth(provider_id, access, body.get("refresh_token") or refresh, body.get("expires_in"), model=row.get("model"))
        return access
    except Exception:  # noqa: BLE001
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
