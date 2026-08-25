# ai-support

User-facing AI provider settings live in the **Computer → AI** panel and **onboarding step 1**.

Implementation: `backend/ai_support/` — modeled after [Hugging Face tau `src/tau_ai`](https://github.com/huggingface/tau/tree/main/src/tau_ai):

| tau concept | swarm |
|-------------|-------|
| `RuntimeProviderAuth` | `config.RuntimeProviderAuth` — key + base URL + headers resolved at call time |
| `OpenAICompatibleConfig` | `config.OpenAICompatibleConfig` — Groq, OpenRouter, OpenAI, HF, Together |
| `openai_compatible_config_from_env` | `store.resolve_key` + provider `env_fallback` |
| Provider adapters | `resolver.build_openai_compatible_client` (Groq SDK, OpenAI-compatible) |
| Fallback chain | `resolver.iter_openai_compatible_attempts` sorted by `priority` |

Connect a provider from the UI to store an encrypted API key in SQLite; env vars still work as fallbacks.

**Providers:** Groq, OpenRouter, OpenAI, Hugging Face, Together AI, Anthropic (catalog; use OpenRouter for Claude chat today).

**Apps (not an LLM provider):** Composio lives in Computer → Apps (`/api/composio/*`, `COMPOSIO_API_KEY`). A Composio key must not be treated as LLM-ready.
