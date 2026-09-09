# Changelog

This file maps the feature layers to the commits that introduced them. Use it when you need to bisect a change or revert a layer.

| Commit (search log) | Scope | Revert effect |
|---------------------|-------|---------------|
| Add central tool registry, plugins, and custom tools | `backend/tools/`, `plugins/`, tool API routes | Removes extensible tools; agents use builtins only |
| Add tau-style AI provider module | `backend/ai_support/`, `ai-support/README.md` | Removes multi-provider catalog + encrypted key store |
| Add API security guard and frontend provider UI shell | `backend/security.py`, mascot, `frontend/src/ai-support/`, cache | Removes origin guard + UI panels (backend still has providers) |
| Wire tools, providers, security, and onboarding into app | `db.py`, `agent.py`, `main.py`, `App.jsx`, tests | Breaks features if reverted alone; revert prior commits first |
| Document tools, providers, and API security in specs | `README.md`, `SPEC.md`, `PROJECT.md`, this file | Docs only |
| Add Pip mascot, custom Bot names, and group chats | mascot, `display_name`, `channel_members`, group trigger | Reverts companion UI, friendly names, and group rooms |
| Make swarm a Slack-shaped AI workspace | admin, people DMs, `@team`, export, archive, mascot removed | Reverts small-team workspace + restores Pip |
| Add chat retry and admin password docs | `POST /api/messages/{id}/retry`, PBKDF2 admin password, UI retry banner | Reverts user-facing retry + password reclaim docs (code may remain) |
| Add computer-use, browser-use, and Composio plugin | `backend/tools/{computer,browser,composio_client}.py`, `plugins/composio/`, Computer → Browser/Apps | Removes agent computer/browser/app tools |
| Add bot profiles and research/CUA connectors | `profiles/`, `backend/profiles.py`, `backend/tools/connectors.py` | Removes profile.md + Exa/Tavily/Firecrawl/browser-use/CUA |
| Seed ten /commands | `skills/*.md`, `backend/bundled_skills.py` | Removes bundled standup/digest/research/… skills |
| Add host-system tools + Computer panel UX | `backend/tools/system.py`, Computer → System, topbar | Removes `system_*` tools and Sandbox/System split |
| Fix provider URL doubling, add NVIDIA/OpenCode/Zen, minimisable providers | `resolver.py`, `agent.py`, `providers.py`, `CommandCenter.jsx` | Removes new providers; reverts to Groq SDK (may break model listing) |
| Add unified work sessions + workspace rail | `backend/work.py`, `WorkRail.jsx`, `sessionStore.js`, chat/v2 hooks | Removes `/api/work` + rail; chat/v2 keep working standalone |
| Add memory/knowledge/context + chat management | `backend/{context,knowledge}.py`, `forget`/`knowledge_*` tools, `KnowledgeView.jsx`, delete authz | Removes KB endpoints, context meter, forget; deletes revert to author-only checks removed |
| Pinterest-2026 accents + taste pass + KaTeX removal | plum/persimmon/jade tokens, aurora+grain backdrop, serif hero, `ui.jsx` | Reverts palette; re-adding katex restores math rendering |

## Feature summary (post-merge)

- **Tools:** 34 builtins (sandbox computer, host system, Playwright, Exa, Tavily, Firecrawl, Browser Use CLI, CUA, memory/knowledge: `forget`, `knowledge_search`, `knowledge_save`, sub-agent `create_agent`) + plugins + Composio
- **Profiles:** `profiles/<bot>.md` for seeded Bots; `profiles/jobs/<id>.md` for each job template
- **AI providers:** Groq, OpenRouter, OpenAI, Hugging Face, Together, Anthropic, Google Gemini, Mistral, DeepSeek, xAI, Fireworks AI, Perplexity, NVIDIA NIM, OpenCode, Zen (OpenAI SDK adapter, tau-inspired resolver); UI in Computer → AI; minimisable provider panel
- **Apps:** Composio + Exa + Tavily + Firecrawl keys in Computer → Apps; Browser Use CLI and CUA are local installs
- **Security:** Bearer required on data reads; CORS allowlist; `X-Swarm-Client: web` for browser origins; first user is admin; optional admin password stored as PBKDF2 hash (never plain text)
- **Frontend:** Slack-style sidebar (DMs, teams, channels, search); Computer panel with Sandbox vs System file browser; Retry on agent error bubbles and failed human sends
- **Bots:** Custom `display_name`; group chats; `@core` team; archive instead of hard-delete
- **People:** Private 1:1s; JSON/CSV audit export per channel
- **Slash commands:** `/standup` `/digest` `/decide` `/research` `/page` `/repro` `/draft` `/review` `/plan` `/brief`
- **Work sessions:** unified `/api/work` over chat replies, v2 runs, routines, handoffs; replayable events; startup recovery; live Work rail with approvals
- **Memory & knowledge:** `forget` tool, per-agent memory endpoints, FTS5 knowledge base + agent tools, budget-aware context builder with composer meter + compact endpoint
- **Chat management:** own-message thread/delete actions, channel delete with live removal, thread delete, author-or-admin delete rule
- **Work detail:** `/api/work/{id}/messages` full-message endpoint, slide-out detail sheet (timeline with timestamps + payload detail, linked replies, in-context approve/cancel), `Ctrl+Shift+W` rail shortcut, source filter, skeleton cards, error banner with retry
- **Knowledge that gets used:** explicit knowledge-habit + memory-hygiene prompt blocks when those tools are allowed; channel-scoped KB hits boosted in ranking and budget trimming
- **Tool budget:** default 3 → 6 calls per reply, hard cap 8 → 12 (seeds: swarm/ledger 6, coder 8); hitting the cap runs one final no-tools round so the agent summarizes instead of dead-ending. Existing stored per-agent values are untouched.
- **Robustness:** composer context meter shows an explicit retry state instead of failing silently; work-event replay also covers sessions that finished while disconnected
- **Design:** Pinterest-2026 accents (Plum Noir intelligence, Persimmon attention, Jade success) on warm charcoal; aurora+grain backdrop; serif display hero; KaTeX dropped (JS −45%)

Reference: [Hugging Face tau `src/tau_ai`](https://github.com/huggingface/tau/tree/main/src/tau_ai)
