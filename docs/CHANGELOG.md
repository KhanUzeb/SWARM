# Changelog

Track feature layers for easy `git revert` / bisect. Each row maps to one commit on `main`.

| Commit (search log) | Scope | Revert effect |
|---------------------|-------|---------------|
| Add central tool registry, plugins, and custom tools | `backend/tools/`, `plugins/`, tool API routes | Removes extensible tools; agents use builtins only |
| Add tau-style AI provider module | `backend/ai_support/`, `ai-support/README.md` | Removes multi-provider catalog + encrypted key store |
| Add API security guard and frontend provider UI shell | `backend/security.py`, mascot, `frontend/src/ai-support/`, cache | Removes origin guard + UI panels (backend still has providers) |
| Wire tools, providers, security, and onboarding into app | `db.py`, `agent.py`, `main.py`, `App.jsx`, tests | Breaks features if reverted alone — revert prior commits first |
| Document tools, providers, and API security in specs | `README.md`, `SPEC.md`, `PROJECT.md`, this file | Docs only |

## Feature summary (post-merge)

- **Tools:** 11 builtins + `plugins/*/manifest.json` + DB custom tools (`POST /api/tools/custom`)
- **AI providers:** Groq, OpenRouter, OpenAI, Hugging Face, Together (tau-inspired resolver); UI in Computer → AI; onboarding step 1
- **Security:** Bearer required on data reads; CORS allowlist; `X-Swarm-Client: web` for browser origins
- **Frontend:** Bee mascot, cached `apiJson()`, 3-step onboarding with API key save

Reference: [Hugging Face tau `src/tau_ai`](https://github.com/huggingface/tau/tree/main/src/tau_ai)
