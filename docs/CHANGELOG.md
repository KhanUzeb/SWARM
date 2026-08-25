# Changelog

Track feature layers for easy `git revert` / bisect. Each row maps to one commit on `main`.

| Commit (search log) | Scope | Revert effect |
|---------------------|-------|---------------|
| Add central tool registry, plugins, and custom tools | `backend/tools/`, `plugins/`, tool API routes | Removes extensible tools; agents use builtins only |
| Add tau-style AI provider module | `backend/ai_support/`, `ai-support/README.md` | Removes multi-provider catalog + encrypted key store |
| Add API security guard and frontend provider UI shell | `backend/security.py`, mascot, `frontend/src/ai-support/`, cache | Removes origin guard + UI panels (backend still has providers) |
| Wire tools, providers, security, and onboarding into app | `db.py`, `agent.py`, `main.py`, `App.jsx`, tests | Breaks features if reverted alone — revert prior commits first |
| Document tools, providers, and API security in specs | `README.md`, `SPEC.md`, `PROJECT.md`, this file | Docs only |
| Add Pip mascot, custom Bot names, and group chats | mascot, `display_name`, `channel_members`, group trigger | Reverts companion UI, friendly names, and group rooms |
| Make swarm a Slack-shaped AI workspace | admin, people DMs, `@team`, export, archive, mascot removed | Reverts small-team workspace + restores Pip |
| Add computer-use, browser-use, and Composio plugin | `backend/tools/{computer,browser,composio_client}.py`, `plugins/composio/`, Computer → Browser/Apps | Removes agent computer/browser/app tools |

## Feature summary (post-merge)

- **Tools:** 21 builtins (including computer-use + browser-use) + `plugins/*/manifest.json` (python handlers supported) + DB custom tools + bundled Composio plugin
- **AI providers:** Groq, OpenRouter, OpenAI, Hugging Face, Together (tau-inspired resolver); UI in Computer → AI; onboarding step 1
- **Apps:** Composio workspace key (`COMPOSIO_API_KEY` or Computer → Apps) shared by every Bot
- **Security:** Bearer required on data reads; CORS allowlist; `X-Swarm-Client: web` for browser origins; first user is admin
- **Frontend:** Slack-style sidebar (DMs, teams, channels, search); 3-step onboarding with API key save; Browser + Apps computer tabs
- **Bots:** Custom `display_name`; group chats; `@core` team; archive instead of hard-delete
- **People:** Private 1:1s; JSON/CSV audit export per channel

Reference: [Hugging Face tau `src/tau_ai`](https://github.com/huggingface/tau/tree/main/src/tau_ai)
