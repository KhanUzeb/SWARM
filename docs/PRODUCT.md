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

| Project | Surface | Swarm difference |
| ------- | ------- | ---------------- |
| [Buzz](https://github.com/block/buzz) (Block) | Own workspace + desktop | Same thesis; Swarm is the small slice that tests "chat-native agents" without Nostr/git/workflows |
| [SlackHive](https://github.com/pelago-labs/slackhive) | Slack | Job specialists + Boss delegation; Swarm owns the workspace, no Slack app |
| [Operator](https://github.com/geekforbrains/operator) | Slack | Routines + delegation; Swarm has routines + handoffs, no Slack dependency |
| [OpenTag](https://github.com/linxidnju/OpenTag) | Slack gateway | Approvals + channel-native; Swarm is runtime + UI, not a gateway |
| [Rakazo](https://github.com/elie222/rakazo) | Web + desktop + mobile | Persistent bots, BYO model/computer, provider-neutral interfaces — Swarm's model for `custom` endpoints and `SWARM_COMPUTER_PROVIDER` |
| xAI Grok Bot | Cloud job bots | Same job/1:1/routine shape, self-hosted |

Orchestration runtimes (Orloj, Clawix, GAIA) are a different layer;
IDE-native tools (Cursor skills) are a different surface.

## Scope

**Shipped:** admin roles, onboarding, `@team` pods, human DMs, audit
export, demo mode, sandbox + host-system computer, browser-use, Composio
apps, durable v2 workflows with approvals, 15 providers + custom
OpenAI-compatible endpoints, OAuth + API-key auth.

**Explicitly out:** Nostr signing, git hosting, multi-tenant SaaS, cloud
VM / remote desktop, container-per-agent isolation, voice huddles,
canvases, vector/semantic history (keyword search stands until it fails
in daily use — speculative work stays gated).

## Demo

The 5-minute demo script lives in [README.md](../README.md)
(“Demo script”). The contract for everything the demo shows lives in
the code and [`AGENTS.md`](../AGENTS.md).

## Design principles

1. **Agents are messages, not modals.** If it happened, it's in the
   channel, including tool calls.
2. **Gate speculative work.** Don't build it until the simpler thing has
   failed in daily use.
3. **The code is the contract.** Code and `AGENTS.md` disagree is a bug.
4. **Portfolio-real.** Every change ships something runnable, not a
   design doc.
