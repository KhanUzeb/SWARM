"""AI provider catalog — tau-style kinds with env + UI connect support."""
from __future__ import annotations

from typing import Any, Literal

ProviderKind = Literal["openai_compatible", "anthropic", "google"]

# Priority: lower number = tried first when falling back across providers.
PROVIDERS: dict[str, dict[str, Any]] = {
    "groq": {
        "id": "groq",
        "name": "Groq",
        "kind": "openai_compatible",
        "auth": "api_key",
        "priority": 1,
        "key_url": "https://console.groq.com/keys",
        "models": ["openai/gpt-oss-120b", "openai/gpt-oss-20b"],
        "default_model": "openai/gpt-oss-120b",
        "env_fallback": "GROQ_API_KEY",
        "base_url": "https://api.groq.com/openai/v1",
        "model_aliases": {
            "llama-3.1-8b-instant": "openai/gpt-oss-20b",
            "llama-3.3-70b-versatile": "openai/gpt-oss-120b",
        },
    },
    "openrouter": {
        "id": "openrouter",
        "name": "OpenRouter",
        "kind": "openai_compatible",
        "auth": "api_key",
        "priority": 2,
        "key_url": "https://openrouter.ai/keys",
        "models": [
            "openai/gpt-oss-120b",
            "anthropic/claude-3.5-sonnet",
            "meta-llama/llama-3.3-70b-instruct",
        ],
        "default_model": "openai/gpt-oss-120b",
        "env_fallback": "OPENROUTER_API_KEY",
        "base_url": "https://openrouter.ai/api/v1",
        "extra_headers": {
            "HTTP-Referer": "http://localhost:8000",
            "X-Title": "swarm",
        },
        "model_aliases": {
            "openai/gpt-oss-120b": "openai/gpt-oss-120b",
            "openai/gpt-oss-20b": "openai/gpt-oss-20b",
            "llama-3.3-70b-versatile": "meta-llama/llama-3.3-70b-instruct",
        },
    },
    "openai": {
        "id": "openai",
        "name": "OpenAI",
        "kind": "openai_compatible",
        "auth": "api_key",
        "priority": 3,
        "oauth_label": "Connect with API key",
        "key_url": "https://platform.openai.com/api-keys",
        "models": ["gpt-4o-mini", "gpt-4o", "gpt-4.1-mini"],
        "default_model": "gpt-4o-mini",
        "env_fallback": "OPENAI_API_KEY",
        "base_url": "https://api.openai.com/v1",
        "model_aliases": {
            "openai/gpt-oss-120b": "gpt-4o",
            "openai/gpt-oss-20b": "gpt-4o-mini",
        },
    },
    "huggingface": {
        "id": "huggingface",
        "name": "Hugging Face",
        "kind": "openai_compatible",
        "auth": "api_key",
        "priority": 4,
        "key_url": "https://huggingface.co/settings/tokens",
        "models": [
            "meta-llama/Meta-Llama-3-8B-Instruct",
            "Qwen/Qwen2.5-72B-Instruct",
            "deepseek-ai/DeepSeek-R1-Distill-Llama-8B",
        ],
        "default_model": "meta-llama/Meta-Llama-3-8B-Instruct",
        "env_fallback": "HF_TOKEN",
        "base_url": "https://router.huggingface.co/v1",
        "model_aliases": {},
    },
    "together": {
        "id": "together",
        "name": "Together AI",
        "kind": "openai_compatible",
        "auth": "api_key",
        "priority": 5,
        "key_url": "https://api.together.xyz/settings/api-keys",
        "models": [
            "meta-llama/Meta-Llama-3.1-70B-Instruct-Turbo",
            "Qwen/Qwen2.5-72B-Instruct-Turbo",
        ],
        "default_model": "meta-llama/Meta-Llama-3.1-70B-Instruct-Turbo",
        "env_fallback": "TOGETHER_API_KEY",
        "base_url": "https://api.together.xyz/v1",
        "model_aliases": {},
    },
    "anthropic": {
        "id": "anthropic",
        "name": "Anthropic",
        "kind": "anthropic",
        "auth": "api_key",
        "priority": 6,
        "oauth_label": "Connect with API key",
        "key_url": "https://console.anthropic.com/settings/keys",
        "models": ["claude-3-5-sonnet-latest", "claude-3-5-haiku-latest"],
        "default_model": "claude-3-5-haiku-latest",
        "env_fallback": "ANTHROPIC_API_KEY",
        "base_url": "https://api.anthropic.com/v1",
        "model_aliases": {
            "openai/gpt-oss-120b": "claude-3-5-sonnet-latest",
            "openai/gpt-oss-20b": "claude-3-5-haiku-latest",
        },
        "note": "Native Messages API — use OpenRouter for Claude in chat-completions today.",
    },
}

_PUBLIC_KEYS = frozenset({
    "id", "name", "kind", "auth", "priority", "key_url", "models",
    "default_model", "oauth_label", "note",
})


def list_providers() -> list[dict[str, Any]]:
    ordered = sorted(PROVIDERS.values(), key=lambda p: (p.get("priority", 99), p["id"]))
    return [{k: v for k, v in p.items() if k in _PUBLIC_KEYS} for p in ordered]


def get_provider(provider_id: str) -> dict[str, Any] | None:
    return PROVIDERS.get(provider_id)


def providers_by_priority() -> list[dict[str, Any]]:
    return sorted(PROVIDERS.values(), key=lambda p: (p.get("priority", 99), p["id"]))
