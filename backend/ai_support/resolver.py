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
    """Resolve the stored key first, then the environment fallback."""
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
    spec = get_provider(provider_id) or {}
    aliases = spec.get("model_aliases") or {}
    chosen = (agent_model or "").strip()
    if chosen in aliases:
        return aliases[chosen]
    # HF / Together expect repo-style slugs — map Groq-style ids to the connected default.
    if provider_id in ("huggingface", "together") and chosen.startswith("openai/"):
        return stored_model or spec.get("default_model") or chosen
    if chosen:
        return chosen
    return stored_model or spec.get("default_model") or chosen


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


class _AnthropicDelta:
    def __init__(self, content: str):
        self.content = content
        self.tool_calls = None


class _AnthropicChoice:
    def __init__(self, content: str):
        self.delta = _AnthropicDelta(content)


class _AnthropicChunk:
    def __init__(self, content: str):
        self.choices = [_AnthropicChoice(content)]
        self.usage = None


class _AnthropicStream:
    def __init__(self, content: str):
        self.content = content

    def __aiter__(self):
        return self._items()

    async def _items(self):
        yield _AnthropicChunk(self.content)


class _AnthropicCompletions:
    def __init__(self, auth: RuntimeProviderAuth):
        self.auth = auth

    async def create(self, *, model: str, messages: list[dict[str, Any]], max_tokens: int = 600, **_: Any) -> _AnthropicStream:
        import httpx
        system = "\n".join(str(message.get("content") or "") for message in messages if message.get("role") == "system")
        converted = [{"role": "user" if message.get("role") != "assistant" else "assistant", "content": str(message.get("content") or "")} for message in messages if message.get("role") != "system"]
        headers = {"x-api-key": self.auth.api_key, "anthropic-version": "2023-06-01", "content-type": "application/json", **dict(self.auth.headers or {})}
        payload: dict[str, Any] = {"model": model, "messages": converted, "max_tokens": max_tokens}
        if system:
            payload["system"] = system
        base = (self.auth.base_url or "https://api.anthropic.com/v1").rstrip("/")
        async with httpx.AsyncClient(timeout=60) as client:
            response = await client.post(f"{base}/messages", headers=headers, json=payload)
            response.raise_for_status()
            body = response.json()
        content = "\n".join(str(block.get("text") or "") for block in body.get("content") or [] if isinstance(block, dict)).strip()
        return _AnthropicStream(content)


class _AnthropicClient:
    def __init__(self, auth: RuntimeProviderAuth):
        self.chat = type("_Chat", (), {"completions": _AnthropicCompletions(auth)})()


async def iter_provider_attempts(agent_model: str) -> list[tuple[Any, str, str]]:
    attempts = await iter_openai_compatible_attempts(agent_model)
    spec = get_provider("anthropic") or {}
    auth = await resolve_runtime_auth("anthropic")
    if auth is not None and spec.get("kind") == "anthropic":
        attempts.append((_AnthropicClient(auth), map_model_for_provider(agent_model, "anthropic", stored_model=auth.default_model), "anthropic"))
    return attempts


async def any_provider_ready() -> bool:
    for spec in providers_by_priority():
        key = await store.resolve_key(spec["id"], env_fallback=spec.get("env_fallback"))
        if key:
            return True
    return False
