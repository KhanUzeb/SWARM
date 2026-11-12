# Product thesis

**One line:** a self-hosted team workspace where AI agents are named
teammates. They use the same channels and history as people, with the same
audit trail. Swarm is not a sidebar chatbot bolted onto chat.

## Thesis

Team chat and AI assistance still live in different products. Chat remembers
the conversation; the agent remembers its session. When an agent runs a shell
command or drafts a message, that work is invisible unless someone pastes it
back into chat. The team cannot `@mention` the agent the way they `@mention`
each other, cannot see tool calls in the thread, and cannot hand off between
specialists without becoming the router.

Block's [Buzz](https://github.com/block/buzz) names the fix: one event log,
one identity model, humans and agents speaking the same protocol. Swarm tests
the same hypothesis with a smaller bet: does putting agents in the same room
as humans, with the same message primitives and a visible audit trail,
produce something more useful than a chatbot sidebar — without rebuilding
Buzz's infrastructure (Nostr, git hosting, canvases, huddles)?

The bet is looking cheaper every quarter: OpenAI (Dots), SpaceXAI (Grok
Bot), Meta (Muse) and Anthropic (Claude Duet) now ship the hosted version of
it, and OpenClaw and Nous Research's Hermes Agent ship the open-source
personal-agent versions. Swarm remains the small web-workspace one.

The answer so far is yes for a small-team workspace: named bots with jobs,
1:1 DMs, skills, routines, approvals, bot-to-bot handoffs, and a shared
computer all run today.

## Who this is for

| Audience | What they get |
| -------- | ------------- |
| **Portfolio reviewer** | Realtime systems (WS fanout), dual REST/WS API design, LLM tool-calling with audit logs, Docker deploy — one coherent product |
| **Solo builder / founder** | A local "AI ops desk": bots you configure once and talk to in 1:1s or rooms |
| **Small team (2–8 people)** | Self-hosted workspace where everyone sees what agents did and approves risky actions in-thread |

Not for: enterprises replacing chat suites, teams needing day-one
CRM connectors, or anyone needing cryptographic event signing and
git-native workflows (use Buzz).

## Competition

The market moved fast in 2026. As of November 2026:

| Project | License | Shape | Swarm vs. it |
| ------- | ------- | ----- | ------------ |
| [OpenClaw](https://github.com/openclaw/openclaw) | MIT | Gateway architecture with an agent inside; 24+ messaging channels, thousands of skills; ~247k stars in June 2026 (≈393k by November); 501(c)(3) foundation; v2026.8.1 added shared cloud sessions | Breadth vs focus. Swarm doesn't ship a channel ecosystem or native apps; it ships a web workspace where rooms, approvals and the audit trail are the product |
| [Hermes Agent](https://github.com/nousresearch/hermes-agent) (Nous Research) | MIT | Single self-improving agent: learning loop, agent-written skills, cron, FTS5 recall, messaging gateway (Telegram/Discord/Slack/WhatsApp/Signal/CLI) plus Bot Mode; ~188k stars (June 2026) | Different shape. One deep personal agent vs rooms of specialists; Swarm keeps the bots, the history and the audit in one shared transcript |
| [OpenBot](https://openbot.run) (Synthetify Labs) | PolyForm Noncommercial 1.0.0 | Desktop apps (macOS/Windows/Linux) + phone clients; wraps the CLI agents people already pay for (Codex, Claude Code, Gemini, Grok, …); channels, workflows with approvals, plugins; hosted "Teams" tier | Source-available, not open source; personal-agent-shaped. Swarm is MIT, web-first and team-first |
| xAI/SpaceXAI's **Grok Bot** | Proprietary | Hosted; Linux VM per member; teammates-style named bots; bundled in SuperGrok / Cursor plans (reported ~$120–300 per seat monthly; no published standalone price); shared-VM security caveat on the record | The self-hosted, model-agnostic counterpart — same job/1:1/routine shape, no vendor VM, no plan gating |
| OpenAI's **Dots** | Proprietary | Hosted; named agents with a cloud computer; first dot included on eligible plans (launched Sep 29, 2026) | Same bet, vendor's cloud, vendor's terms; Swarm keeps the computer and data yours |
| Meta's **Muse** | Proprietary | Hosted; free tier up to a token cap, then paid (launched Sep 2026) | Same |
| Anthropic's **Claude Duet** | Proprietary | Hosted proactive agent (research preview, Oct 2026) | Same |
| [Buzz](https://github.com/block/buzz) (Block) | — | Own workspace + desktop | Same thesis; Swarm is the small slice that tests "chat-native agents" without Nostr/git/workflows |
| [SlackHive](https://github.com/pelago-labs/slackhive) | — | Slack app | Job specialists + Boss delegation; Swarm owns the workspace, no Slack dependency |
| [Operator](https://github.com/geekforbrains/operator) | — | Slack app | Routines + delegation; Swarm has routines + handoffs, no Slack dependency |
| [OpenTag](https://github.com/linxidnju/OpenTag) | — | Slack gateway | Approvals + channel-native; Swarm is runtime + UI, not a gateway |
| [Rakazo](https://github.com/elie222/rakazo) | — | Web + desktop + mobile | Persistent bots, BYO model/computer, provider-neutral interfaces — Swarm's model for `custom` endpoints and `SWARM_COMPUTER_PROVIDER` |

Orchestration runtimes (Orloj, Clawix, GAIA) are a different layer;
IDE-native tools (Cursor skills) are a different surface.

Swarm doesn't chase feature parity with any of these. OpenClaw and Hermes
Agent will always out-skill it through their ecosystems, and the hosted
vendors will always ship more compute. What Swarm does that none of them
ship as the whole product: people and named bots in the same rooms, every
tool call in-channel, risky calls paused for a human bound to the exact
agent, channel, tool and arguments — in a single workspace you self-host
and can read end to end.

## Scope

**Shipped:** admin roles, onboarding, `@team` pods, human DMs, audit
export, demo mode, fluid sandbox (Places → Sandbox, rebindable) + host-system
Places (Places → System), browser-use, Composio
apps, durable v2 workflows with approvals, 46 providers + custom
OpenAI-compatible endpoints, OAuth + API-key auth.

**Explicitly out:** Nostr signing, git hosting, multi-tenant SaaS, cloud
VM / remote desktop, container-per-agent isolation, voice huddles,
canvases, vector/semantic history (keyword search stands until it fails
in daily use — speculative work stays gated).

## Demo

The 5-minute demo script lives in [README.md](../README.md)
("Demo script"). The contract for everything the demo shows lives in
the code and [`AGENTS.md`](../AGENTS.md).

## Design principles

1. **Agents are messages, not modals.** If it happened, it's in the
   channel, including tool calls.
2. **Gate speculative work.** Don't build it until the simpler thing has
   failed in daily use.
3. **The code is the contract.** Code and `AGENTS.md` disagree is a bug.
4. **Portfolio-real.** Every change ships something runnable, not a
   design doc.
