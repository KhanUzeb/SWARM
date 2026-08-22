"""Provider runtime configuration (inspired by huggingface/tau src/tau_ai/env.py)."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping

DEFAULT_OPENAI_COMPATIBLE_BASE_URL = "https://api.openai.com/v1"
DEFAULT_OPENAI_COMPATIBLE_TIMEOUT_SECONDS = 60.0
DEFAULT_OPENAI_COMPATIBLE_MAX_RETRIES = 2


@dataclass(frozen=True, slots=True)
class RuntimeProviderAuth:
    """Credentials resolved immediately before a provider call."""

    provider_id: str
    api_key: str
    base_url: str | None = None
    headers: Mapping[str, str] | None = None
    default_model: str | None = None


@dataclass(frozen=True, slots=True)
class OpenAICompatibleConfig:
    """OpenAI-compatible chat completions endpoint (Groq, OpenRouter, HF, etc.)."""

    provider_id: str
    api_key: str
    base_url: str = DEFAULT_OPENAI_COMPATIBLE_BASE_URL
    headers: Mapping[str, str] | None = None
    provider_name: str = "OpenAI-compatible"
    model_aliases: Mapping[str, str] = field(default_factory=dict)
    max_retries: int = DEFAULT_OPENAI_COMPATIBLE_MAX_RETRIES
