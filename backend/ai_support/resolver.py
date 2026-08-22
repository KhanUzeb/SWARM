"""Resolve provider credentials and OpenAI-compatible clients (tau-style)."""
from __future__ import annotations

import os
from typing import Any

from .config import OpenAICompatibleConfig, RuntimeProviderAuth
from .providers import get_provider, providers_by_priority
from . import store


def _env_key(name: str) -> str:
    return (os.environ.get(name) or "").strip().strip('"').strip("'")


async def resolve_runtime_auth(provider_id: str) -> RuntimeProviderAuth | None:
    """DB-stored key first, then env fallback — never returns raw key to callers outside server."""
    spec = get_provider(provider_id)
    if spec is None:
        return None
    key = await store.resolve_key(provider_id, env_fallback=spec.get("env_fallback"))
    if not key:
        return None
    headers = dict(spec.get("extra_headers") or {})
    row = await store.get_connection_model(provider_id)
    return RuntimeProviderAuth(
        provider_id=provider_id,
        api_key=key,
        base_url=spec.get("base_url"),
        headers=headers or None,
        default_model=row or spec.get("default_model"),
    )


def map_model_for_provider(agent_model: str, provider_id: str, *, stored_model: str | None = None) -> str:
    spec = get_provider(provider_id)
    if spec is None:
        return agent_model
    aliases = spec.get("model_aliases") or {}
    if agent_model in aliases:
        return aliases[agent_model]
    # HF / Together expect repo-style slugs — prefer connected default when agent uses Groq ids.
    if provider_id in ("huggingface", "together") and agent_model.startswith("openai/"):
        return stored_model or spec.get("default_model") or agent_model
    if stored_model and agent_model in (spec.get("models") or []):
        return agent_model
    if stored_model:
        return stored_model
    return aliases.get(agent_model, agent_model)


def openai_compatible_config(auth: RuntimeProviderAuth) -> OpenAICompatibleConfig:
    spec = get_provider(auth.provider_id) or {}
    return OpenAICompatibleConfig(
        provider_id=auth.provider_id,
        api_key=auth.api_key,
        base_url=(auth.base_url or spec.get("base_url") or "https://api.openai.com/v1").rstrip("/"),
        headers=auth.headers,
        provider_name=str(spec.get("name") or auth.provider_id),
        model_aliases=dict(spec.get("model_aliases") or {}),
    )


def build_openai_compatible_client(config: OpenAICompatibleConfig) -> Any:
    """Groq SDK speaks OpenAI-compatible chat completions — same adapter tau uses for many backends."""
    from groq import AsyncGroq

    kwargs: dict[str, Any] = {"api_key": config.api_key}
    if config.base_url:
        kwargs["base_url"] = config.base_url
    if config.headers:
        kwargs["default_headers"] = dict(config.headers)
    return AsyncGroq(**kwargs)


async def iter_openai_compatible_attempts(agent_model: str) -> list[tuple[Any, str, str]]:
    """Ordered fallback chain: (client, resolved_model, provider_id)."""
    out: list[tuple[Any, str, str]] = []
    for spec in providers_by_priority():
        if spec.get("kind") != "openai_compatible":
            continue
        pid = spec["id"]
        auth = await resolve_runtime_auth(pid)
        if auth is None:
            continue
        config = openai_compatible_config(auth)
        client = build_openai_compatible_client(config)
        resolved = map_model_for_provider(agent_model, pid, stored_model=auth.default_model)
        out.append((client, resolved, pid))
    return out


async def any_provider_ready() -> bool:
    for spec in providers_by_priority():
        key = await store.resolve_key(spec["id"], env_fallback=spec.get("env_fallback"))
        if key:
            return True
    return False
