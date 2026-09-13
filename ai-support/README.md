# ai-support

User-facing AI provider settings live in the **Computer → AI** panel and **onboarding step 1**.

Implementation lives in `backend/ai_support/`, modeled after [Hugging Face tau `src/tau_ai`](https://github.com/huggingface/tau/tree/main/src/tau_ai):

| tau concept | swarm |
|-------------|-------|
| `RuntimeProviderAuth` | `config.RuntimeProviderAuth` — key + base URL + headers resolved at call time |
| `OpenAICompatibleConfig` | `config.OpenAICompatibleConfig` — all OpenAI-compatible providers |
| `openai_compatible_config_from_env` | `store.resolve_key` + provider `env_fallback` |
| Provider adapters | `resolver.build_openai_compatible_client` (OpenAI SDK, OpenAI-compatible) |
| Fallback chain | `resolver.iter_openai_compatible_attempts` sorted by `priority` |

Connect a provider in the UI to store an encrypted API key in SQLite. Environment variables remain available as fallbacks.

**Providers:** Groq, OpenRouter, OpenAI, Hugging Face, Together AI, Anthropic, Google Gemini, Mistral, DeepSeek, xAI, Fireworks AI, Perplexity, NVIDIA NIM, OpenCode, Zen, plus **Custom (OpenAI-compatible)** for self-hosted endpoints (Ollama, LM Studio, vLLM) via `SWARM_OPENAI_COMPAT_BASE_URL`.

**Apps (not an LLM provider):** Composio lives in Computer → Apps (`/api/composio/*`, `COMPOSIO_API_KEY`). A Composio key does not make an LLM provider ready.
