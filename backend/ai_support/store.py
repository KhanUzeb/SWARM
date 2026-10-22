"""AI provider credential storage (importable package)."""
from __future__ import annotations

import base64
import hashlib
import logging
import os
import time
from functools import lru_cache
from typing import Any

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from .. import db
from ..settings import get_settings, oauth_client_id, oauth_client_secret
from .providers import get_provider

_logger = logging.getLogger("swarm.providers")

# Credentials are sealed with AES-256-GCM, not a repeating-key XOR. The format
# is versioned (`v2:`) and every value gets a fresh 96-bit nonce, so identical
# keys do not produce identical blobs and a flipped byte fails authentication
# instead of silently decrypting to a corrupted credential. Legacy blobs (no
# prefix) are still readable and are re-sealed on first successful read.
_SEAL_PREFIX = "v2:"
_SEAL_AAD = b"swarm-provider-credential-v2"
_SEAL_SALT = b"swarm-credential-kdf-v2"


class SealKeyMissing(RuntimeError):
    """Raised when a credential would be sealed without a configured key.

    The old code silently used the public literal `swarm-local-dev-secret`,
    which is not a secret at all. Refusing the write is the only honest
    behaviour; the demo path never reaches this because it does not connect
    providers.
    """


def _secret_text() -> str:
    return (get_settings().secret or "").strip()


@lru_cache(maxsize=4)
def _key_for(secret: str) -> bytes:
    # scrypt makes a weak human-chosen SWARM_SECRET expensive to guess; the
    # salt is application-fixed because the ciphertext itself carries the
    # per-value nonce that matters for AES-GCM uniqueness. Rotating the
    # secret intentionally makes old rows unreadable (they report
    # needs_reconnect rather than silently falling back).
    return hashlib.scrypt(
        secret.encode("utf-8"), salt=_SEAL_SALT, n=2**14, r=8, p=1, dklen=32,
    )


def _seal(raw: str) -> str:
    secret = _secret_text()
    if not secret:
        raise SealKeyMissing(
            "SWARM_SECRET is not set. Set a private random value in .env "
            "before storing provider credentials; SWARM_DEMO=1 needs no key."
        )
    nonce = os.urandom(12)
    ciphertext = AESGCM(_key_for(secret)).encrypt(
        nonce, raw.encode("utf-8"), _SEAL_AAD,
    )
    return _SEAL_PREFIX + base64.urlsafe_b64encode(nonce + ciphertext).decode("ascii")


def _legacy_unseal(token: str) -> str:
    """Read the pre-v2 XOR format so existing installs can migrate."""
    secret = _secret_text()
    if not secret:
        raise SealKeyMissing("SWARM_SECRET is required to read a legacy credential")
    key = hashlib.sha256(secret.encode("utf-8")).digest()
    data = base64.urlsafe_b64decode(token.encode("ascii"))
    plain = bytes(b ^ key[i % len(key)] for i, b in enumerate(data))
    return plain.decode("utf-8")


def _unseal(token: str) -> str:
    if not token.startswith(_SEAL_PREFIX):
        return _legacy_unseal(token)
    secret = _secret_text()
    if not secret:
        raise SealKeyMissing("SWARM_SECRET is required to read a stored credential")
    raw = base64.urlsafe_b64decode(token[len(_SEAL_PREFIX):].encode("ascii"))
    if len(raw) < 13:
        raise ValueError("sealed credential is too short")
    nonce, ciphertext = raw[:12], raw[12:]
    return AESGCM(_key_for(secret)).decrypt(nonce, ciphertext, _SEAL_AAD).decode("utf-8")


def _is_legacy(token: str) -> bool:
    return bool(token) and not token.startswith(_SEAL_PREFIX)


def _hint(raw: str) -> str:
    return f"…{raw[-4:]}" if len(raw) > 4 else "****"

# Request-window resolve cache: one agent turn (or one catalog page load)
# resolves every provider several times over — the readiness gate, the model
# choice, and the client chain each walk the catalog. Without this, one turn
# costs ~3 SQLite reads per provider for the same answer. Entries live a few
# seconds: long enough to cover one turn, short enough that a key change is
# never stale for a human. Every writer below invalidates its provider, and
# the key carries the live env value so an env change is a certain miss.
_RESOLVE_CACHE: dict[tuple[str, str, str, str, str], tuple[float, Any]] = {}
_RESOLVE_CACHE_TTL = 5.0


def _cache_key(
    provider_id: str, env_fallback: str | None
) -> tuple[str, str, str, str, str]:
    env_val = (os.environ.get(env_fallback) or "") if env_fallback else ""
    # The secret fingerprint makes a rotated SWARM_SECRET an immediate miss:
    # a cached plaintext from the old key must never be served after rotation.
    secret = _secret_text()
    fingerprint = (
        hashlib.sha256(secret.encode("utf-8")).hexdigest()[:16] if secret else ""
    )
    return (str(db.DB_PATH), provider_id, env_fallback or "", env_val, fingerprint)


def _invalidate_provider_cache(provider_id: str) -> None:
    prefix = (str(db.DB_PATH), provider_id)
    for key in [k for k in _RESOLVE_CACHE if k[:2] == prefix]:
        del _RESOLVE_CACHE[key]


async def connect(provider_id: str, api_key: str, *, model: str | None = None) -> dict[str, Any]:
    plain = api_key.strip()
    sealed = _seal(plain)
    row = await db.upsert_ai_provider(provider_id, sealed, model=model, hint=_hint(plain))
    _invalidate_provider_cache(provider_id)
    return row


async def connect_oauth(provider_id: str, access_token: str, refresh_token: str | None, expires_in: int | float | None, *, model: str | None = None) -> dict[str, Any]:
    plain = access_token.strip()
    access = _seal(plain)
    refresh = _seal(refresh_token.strip()) if refresh_token else None
    expires_at = time.time() + float(expires_in) if expires_in else None
    row = await db.update_ai_provider_oauth(
        provider_id, access, refresh, expires_at, model=model, hint=_hint(plain),
    )
    _invalidate_provider_cache(provider_id)
    return row


async def disconnect(provider_id: str) -> bool:
    ok = await db.delete_ai_provider(provider_id)
    _invalidate_provider_cache(provider_id)
    return ok


async def status() -> list[dict[str, Any]]:
    rows = await db.list_ai_providers()
    out: list[dict[str, Any]] = []
    for r in rows:
        readable = True
        try:
            _unseal(r.get("secret") or "")
        except Exception:  # noqa: BLE001 - wrong/absent key means "reconnect"
            readable = False
        out.append({
            "provider_id": r["provider_id"],
            "connected": readable,
            "needs_reconnect": not readable,
            "model": r.get("model"),
            "connected_at": r.get("connected_at"),
            "key_hint": r.get("key_hint"),
        })
    return out


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
        sealed = str(row["secret"])
        legacy = _is_legacy(sealed)
        try:
            plain = _unseal(sealed)
        except SealKeyMissing:
            _logger.warning(
                "provider %s has a stored credential but SWARM_SECRET is unset; "
                "marking it for reconnect", provider_id,
            )
        except (InvalidTag, ValueError, UnicodeDecodeError):
            _logger.warning(
                "provider %s has a stored credential that cannot be decrypted "
                "with the current SWARM_SECRET; reconnect it in Command Center",
                provider_id,
            )
        else:
            if legacy:
                # Successful legacy decrypt is the only safe moment to
                # re-encrypt: never replace a row we could not read.
                try:
                    await db.reseal_ai_provider(
                        provider_id, _seal(plain), _hint(plain),
                    )
                except Exception:  # noqa: BLE001 - keep the working plaintext
                    _logger.warning("could not migrate legacy credential for %s", provider_id)
            return plain
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
    cache_key = (str(db.DB_PATH), provider_id, "model", "", "")
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
