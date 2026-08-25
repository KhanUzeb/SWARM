"""Live model catalogs from provider APIs (OpenAI-compatible /v1/models)."""
from __future__ import annotations

from typing import Any

from .config import RuntimeProviderAuth
from .providers import get_provider, providers_by_priority
from .resolver import (
    build_openai_compatible_client,
    openai_compatible_config,
    resolve_runtime_auth,
)

_SKIP = (
    "whisper",
    "tts",
    "guard",
    "embed",
    "moderation",
    "canary",
    "playai",
    "distil-whisper",
)
_MAX_MODELS = 400
_FETCH_TIMEOUT = 12.0


def _catalog_models(spec: dict[str, Any]) -> list[dict[str, Any]]:
    default = spec.get("default_model")
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for mid in spec.get("models") or []:
        name = str(mid).strip()
        if not name or name in seen:
            continue
        seen.add(name)
        out.append({
            "id": name,
            "name": name,
            "owned_by": spec.get("id"),
            "default": name == default,
        })
    return out


def _is_chat_model(model_id: str) -> bool:
    lowered = model_id.lower()
    return not any(part in lowered for part in _SKIP)


def _normalize_row(raw: Any, *, provider_id: str, default_model: str | None) -> dict[str, Any] | None:
    if raw is None:
        return None
    if isinstance(raw, dict):
        mid = str(raw.get("id") or "").strip()
        name = str(raw.get("name") or mid).strip() or mid
        owned = str(raw.get("owned_by") or raw.get("ownedBy") or provider_id)
    else:
        mid = str(getattr(raw, "id", "") or "").strip()
        name = str(getattr(raw, "name", None) or mid).strip() or mid
        owned = str(getattr(raw, "owned_by", None) or provider_id)
    if not mid or not _is_chat_model(mid):
        return None
    return {
        "id": mid,
        "name": name,
        "owned_by": owned,
        "default": mid == default_model,
    }


async def _fetch_openai_compatible(auth: RuntimeProviderAuth) -> list[dict[str, Any]]:
    config = openai_compatible_config(auth)
    client = build_openai_compatible_client(config)
    response = await client.models.list(timeout=_FETCH_TIMEOUT)
    rows = getattr(response, "data", None) or []
    default_model = auth.default_model
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw in rows:
        item = _normalize_row(raw, provider_id=auth.provider_id, default_model=default_model)
        if item is None or item["id"] in seen:
            continue
        seen.add(item["id"])
        out.append(item)
        if len(out) >= _MAX_MODELS:
            break
    return out


async def _fetch_anthropic(auth: RuntimeProviderAuth) -> list[dict[str, Any]]:
    import httpx

    spec = get_provider(auth.provider_id) or {}
    base = str(spec.get("base_url") or "https://api.anthropic.com/v1").rstrip("/")
    headers = {
        "x-api-key": auth.api_key,
        "anthropic-version": "2023-06-01",
        **dict(auth.headers or {}),
    }
    async with httpx.AsyncClient(timeout=_FETCH_TIMEOUT) as client:
        response = await client.get(f"{base}/models", headers=headers)
        response.raise_for_status()
        payload = response.json()
    rows = payload.get("data") if isinstance(payload, dict) else payload
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw in rows or []:
        item = _normalize_row(raw, provider_id=auth.provider_id, default_model=auth.default_model)
        if item is None or item["id"] in seen:
            continue
        seen.add(item["id"])
        out.append(item)
        if len(out) >= _MAX_MODELS:
            break
    return out


def _auth_from_key(provider_id: str, api_key: str) -> RuntimeProviderAuth | None:
    spec = get_provider(provider_id)
    if spec is None:
        return None
    key = (api_key or "").strip()
    if len(key) < 8:
        return None
    return RuntimeProviderAuth(
        provider_id=provider_id,
        api_key=key,
        base_url=spec.get("base_url"),
        headers=dict(spec.get("extra_headers") or {}) or None,
        default_model=spec.get("default_model"),
    )


async def list_provider_models(
    provider_id: str,
    *,
    api_key: str | None = None,
) -> dict[str, Any]:
    spec = get_provider(provider_id)
    if spec is None:
        raise KeyError(provider_id)
    catalog = _catalog_models(spec)
    auth = None
    if api_key and len(api_key.strip()) >= 8:
        auth = _auth_from_key(provider_id, api_key)
    else:
        auth = await resolve_runtime_auth(provider_id)
    body: dict[str, Any] = {
        "id": provider_id,
        "provider_id": provider_id,
        "name": spec.get("name") or provider_id,
        "live": False,
        "models": catalog,
        "default_model": (auth.default_model if auth else None) or spec.get("default_model"),
    }
    if auth is None:
        body["note"] = "Paste an API key to load this provider's live model list."
        return body
    try:
        kind = spec.get("kind") or "openai_compatible"
        if kind == "anthropic":
            live = await _fetch_anthropic(auth)
        else:
            live = await _fetch_openai_compatible(auth)
        if live:
            body["live"] = True
            body["models"] = live
            body.pop("note", None)
            return body
        body["note"] = "Provider returned no chat models — showing catalog defaults."
    except Exception as exc:  # noqa: BLE001
        body["error"] = str(exc)[:240]
        body["note"] = "Couldn't reach the provider — showing catalog defaults."
    return body


async def list_all_models() -> dict[str, Any]:
    groups: list[dict[str, Any]] = []
    for spec in providers_by_priority():
        row = await list_provider_models(spec["id"])
        auth = await resolve_runtime_auth(spec["id"])
        row["connected"] = auth is not None
        groups.append(row)
    models: list[dict[str, Any]] = []
    seen: set[str] = set()
    for group in groups:
        if not group.get("connected"):
            continue
        for item in group.get("models") or []:
            mid = item.get("id")
            if not mid or mid in seen:
                continue
            seen.add(mid)
            models.append({
                **item,
                "provider_id": group["provider_id"],
                "provider_name": group["name"],
            })
    if not models:
        for group in groups:
            for item in group.get("models") or []:
                mid = item.get("id")
                if not mid or mid in seen:
                    continue
                seen.add(mid)
                models.append({
                    **item,
                    "provider_id": group["provider_id"],
                    "provider_name": group["name"],
                })
    return {"providers": groups, "models": models}
