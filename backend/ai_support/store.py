"""AI provider credential storage (importable package)."""
from __future__ import annotations

import base64
import hashlib
import os
import time
from typing import Any

from .. import db
from ..settings import get_settings, oauth_client_id, oauth_client_secret
from .providers import get_provider

# The seal key lives in the one settings object (AGENTS.md s2). It is read
# once, at import, exactly as before: the seal is the key under which every
# stored provider credential sits, so re-reading it mid-process would strand
# already-sealed rows. Changing it is a restart-time decision.
_SECRET = ((get_settings().secret or "").strip() or "swarm-local-dev-secret").encode()

# Request-window resolve cache: one agent turn (or one catalog page load)
# resolves every provider several times over — the readiness gate, the model
# choice, and the client chain each walk the catalog. Without this, one turn
# costs ~3 SQLite reads per provider for the same answer. Entries live a few
# seconds: long enough to cover one turn, short enough that a key change is
# never stale for a human. Every writer below invalidates its provider, and
# the key carries the live env value so an env change is a certain miss.
_RESOLVE_CACHE: dict[tuple[str, str, str, str], tuple[float, Any]] = {}
_RESOLVE_CACHE_TTL = 5.0


def _cache_key(provider_id: str, env_fallback: str | None) -> tuple[str, str, str, str]:
    env_val = (os.environ.get(env_fallback) or "") if env_fallback else ""
    return (str(db.DB_PATH), provider_id, env_fallback or "", env_val)


def _invalidate_provider_cache(provider_id: str) -> None:
    prefix = (str(db.DB_PATH), provider_id)
    for key in [k for k in _RESOLVE_CACHE if k[:2] == prefix]:
        del _RESOLVE_CACHE[key]


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
    row = await db.upsert_ai_provider(provider_id, sealed, model=model)
    _invalidate_provider_cache(provider_id)
    return row


async def connect_oauth(provider_id: str, access_token: str, refresh_token: str | None, expires_in: int | float | None, *, model: str | None = None) -> dict[str, Any]:
    access = _seal(access_token.strip())
    refresh = _seal(refresh_token.strip()) if refresh_token else None
    expires_at = time.time() + float(expires_in) if expires_in else None
    row = await db.update_ai_provider_oauth(provider_id, access, refresh, expires_at, model=model)
    _invalidate_provider_cache(provider_id)
    return row


async def disconnect(provider_id: str) -> bool:
    ok = await db.delete_ai_provider(provider_id)
    _invalidate_provider_cache(provider_id)
    return ok


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
    """Resolve the stored key first, then the environment fallback.

    Results are cached per (database, provider, env value) for a few seconds
    so one turn's gate + model choice + client chain cost one SQLite read per
    provider instead of three (AGENTS.md s4: never resolve the same provider
    auth twice per request)."""
    cache_key = _cache_key(provider_id, env_fallback)
    hit = _RESOLVE_CACHE.get(cache_key)
    if hit is not None and time.time() - hit[0] < _RESOLVE_CACHE_TTL:
        return hit[1]
    val = await _resolve_key_uncached(provider_id, env_fallback=env_fallback)
    _RESOLVE_CACHE[cache_key] = (time.time(), val)
    return val


async def _resolve_key_uncached(provider_id: str, *, env_fallback: str | None = None) -> str | None:
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
    # The per-provider client config is read through the one settings object,
    # which owns that naming scheme (AGENTS.md s2). Building the variable
    # name here with a raw os.environ.get made a second parse surface.
    client_id = oauth_client_id(provider_id)
    client_secret = oauth_client_secret(provider_id)
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
    cache_key = (str(db.DB_PATH), provider_id, "model", "")
    hit = _RESOLVE_CACHE.get(cache_key)
    if hit is not None and time.time() - hit[0] < _RESOLVE_CACHE_TTL:
        return hit[1]
    row = await db.get_ai_provider(provider_id)
    val = str(row["model"]) if row and row.get("model") else None
    _RESOLVE_CACHE[cache_key] = (time.time(), val)
    return val


async def set_model(provider_id: str, model: str) -> dict[str, Any] | None:
    row = await db.update_ai_provider_model(provider_id, model)
    _invalidate_provider_cache(provider_id)
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
