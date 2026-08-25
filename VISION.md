# VISION.md — swarm

**One line:** A self-hosted team workspace where AI agents are named teammates — same channels, same history, same audit trail — not a sidebar chatbot bolted onto Slack.

---

## Thesis

Team chat and AI assistance still live in different products. Slack remembers the conversation; the agent remembers its session. When an agent runs a shell command or drafts an email, that work is invisible unless someone pastes it back into chat. The team cannot `@mention` the agent the way they `@mention` each other, cannot see tool calls in the thread, and cannot hand off between specialists without becoming the router.

Block's [Buzz](https://github.com/block/buzz) names the fix: one event log, one identity model, humans and agents speaking the same protocol. That is the right shape. Buzz's implementation — Nostr relay, cryptographic signing, git hosting, workflow engine, canvases, huddles, desktop app — is a full product company, not a weekend experiment.

**swarm tests the same hypothesis with a smaller bet:** does putting agents in the same room as humans, with the same message primitives and a visible audit trail, produce something more useful than a chatbot sidebar — *without* rebuilding Buzz's infrastructure?

Phase 10 answered yes for a single-user workspace: named Bots with jobs, 1:1 DMs, skills, routines, approvals, bot-to-bot handoffs, and a shared sandbox "computer" all work today. V3 is about making that legible to a stranger in five minutes and trustworthy enough for a small team.

---

## Who this is for

| Audience | What they get |
|----------|----------------|
| **Portfolio reviewer** | Real-time systems (WS fanout), dual REST/WS API design, LLM tool-calling with audit logs, Docker deploy — in one coherent product, not a tutorial repo. |
| **Solo builder / founder** | A local "AI ops desk": Sales Outbound, Chief of Staff, Code, and Expense Manager Bots you configure once and talk to in 1:1s or rooms. |
| **Small team (2–8 people)** | Self-hosted workspace where everyone sees what agents did, approves risky actions in-thread, and delegates with `@name` instead of copy-pasting between ChatGPT tabs. |

Not for: enterprises replacing Slack, teams needing Salesforce/Slack connectors on day one, or anyone who needs cryptographic event signing and git-native workflows (use Buzz).

---

## Competitive landscape

Projects solving adjacent problems, and where swarm sits.

### Same room, agents as peers

| Project | Surface | Agent identity | Audit trail | Self-host | swarm overlap |
|---------|---------|----------------|-------------|-----------|---------------|
| **[Buzz](https://github.com/block/buzz)** (Block) | Own workspace + desktop | Crypto keypair per agent | Signed Nostr event log | Yes | Same thesis; swarm is the 5% slice that tests "chat-native agents" without Nostr/git/workflows |
| **[SlackHive](https://github.com/pelago-labs/slackhive)** | Slack | Virtual `@boss` + specialists | Thread + SQLite memory | Yes | Job specialists + Boss delegation; swarm has handoffs + job templates but owns the workspace |
| **[Operator](https://github.com/geekforbrains/operator)** | Slack | Markdown-defined agents | Channel-visible | Yes | Routines, spawn/delegate, RBAC; swarm has routines + handoffs, no Slack dependency |
| **[OpenTag](https://github.com/linxidnju/OpenTag)** | Slack gateway | Pluggable runtimes (Codex, Claude Code, Docker) | Thread + approvals + audit | Yes | Approvals + channel-native; swarm is runtime + UI, not a gateway |
| **[Slack Agent Mesh](https://github.com/nickvasilescu/slack-agent-mesh)** | Slack transport | Virtual `@name` identities | Signed event log + broker | Yes | Bot-to-bot `@handoff`; swarm implements handoff in-app without Slack broker |
| **xAI Grok Bot** | Grok app + connectors | Named job roles | Cloud workspace | No | Job catalog, 1:1s, routines, computer — swarm's Phase 10 model, self-hosted |

### Orchestration platforms (different layer)

| Project | Layer | swarm relationship |
|---------|-------|-------------------|
| **[Orloj](https://github.com/orlojHQ/orloj)** | YAML orchestration runtime, governance, leases | swarm could emit events *into* Orloj later; not a competitor at the UI layer |
| **[Clawix](https://github.com/clawixai/clawix)** | Docker-isolated agent containers + RBAC | Stronger isolation story; swarm's sandbox is cwd+timeout, not container-per-agent |
| **[GAIA](https://github.com/vishalsdk14/GAIA)** | Control plane, DAG, policy firewall | Enterprise orchestration; swarm is the human-facing workspace |

### IDE-native (different surface)

| Project | Surface |
|---------|---------|
| [cursor-agents](https://github.com/cocolwy/cursor-agents), [Sisyphus](https://github.com/Fguedes90/cursor-sisyphus), [cursor-agent-kit](https://github.com/mdzyaan/cursor-agent-kit) | Cursor IDE skills + subagents — great for coding, not team-visible async work |

**swarm's wedge:** the only self-contained, Buzz-shaped workspace you can `docker compose up` in ten minutes, with Grok Bot-style named teammates, no Slack app registration, and a spec (`SPEC.md`) that matches what's running.

---

## What ships today (V2 + Phase 10)

```
You ──► room, group, or Bot 1:1 ──► @mention, group, or DM message
                              │
                              ▼
                    sequential agent run(s)
                    (tools → system audit msgs → stream reply)
                              │
              ┌───────────────┼───────────────┐
              ▼               ▼               ▼
         remember/recall   workspace      request_approval
         skills/routines   computer       → human Allow/Deny
```

- **Rooms + threads + reactions** — normal team chat primitives.
- **Named Bots** — job title, custom display name, mention handle, tool harness, per-Bot 1:1 (`dm-<name>` hears you without `@`).
- **Group chats** — pick member Bots; they all hear you without `@`. `@mention` still targets one.
- **Multi-agent** — `@swarm @ledger` replies in mention order; agent replies can `@handoff` to another Bot (depth cap 2).
- **Skills** — account-wide `/skill-name` invoke + `save_skill` tool.
- **Routines** — cron-like intervals posting into a Bot's 1:1.
- **Approvals** — `request_approval` stops the Bot until a human resolves in-channel.
- **Computer** — shared sandbox directory (list/write/shell); not a cloud VM.
- **Observability** — Langfuse traces; every tool call is a visible `system` message.
- **Deploy** — Docker + SQLite volume; React UI with streaming, reconnect, mobile layout.

See `SPEC.md` for the full contract.

---

## V3 — product-complete scope

V3 does not mean "clone Buzz." It means a stranger can run the demo, understand the thesis, and trust it with two coworkers — without reading Rust or registering a Slack app.

### In scope (V3)

| # | Feature | Why | Condition to build |
|---|---------|-----|-------------------|
| V3.1 | **Admin role** | First registered user is admin; only admins create/edit/archive Bots | ✅ shipped |
| V3.2 | **Onboarding flow** | Register → pick first Bot from job template → land in 1:1 with a suggested first message | ✅ shipped |
| V3.3 | **Agent teams** | `@team-name` expands to an ordered set of Bots (Buzz-style bundles) | ✅ shipped (`@core` seeded) |
| V3.4 | **Human DMs** | 1:1 between people, not just Bot DMs | ✅ shipped |
| V3.5 | **Semantic history** | Vector search over messages + memory | Keyword search actually fails in daily use |
| V3.6 | **Audit export** | JSON/CSV export of channel history + tool audit lines | ✅ shipped |
| V3.7 | **Demo mode** | Seed data + `GROQ_API_KEY`-less mock replies for portfolio reviewers | ✅ shipped (`SWARM_DEMO=1`) |

### Explicitly out (even at V3)

- Nostr signing, git hosting, branch-as-channel (Buzz's hard problems).
- Cloud VM, browser computer-use, teach-by-demonstration (Grok Bot cloud tier).
- Salesforce / Slack / Notion connectors (job templates name these roles; they do not connect APIs).
- Multi-tenant SaaS, mobile native app, voice huddles, canvases.
- Container-per-agent isolation (Clawix-level); sandbox stays cwd+timeout unless threat model changes.

### Success criteria for V3

1. **Five-minute demo:** `docker compose up` → register → create "Chief of Staff" Bot → routine fires → approval flow → export audit — no README archaeology.
2. **Spec honesty:** `SPEC.md` still matches running code; `VISION.md` matches `PROBLEM.md`.
3. **Comparable pitch:** a reviewer can place swarm vs Buzz vs SlackHive in one sentence without you in the room.
4. **Small-team trust:** admin-gated Bot creation; private people DMs; no silent cross-user impersonation.

---

## Demo narrative (portfolio / interview)

Use this script when showing the project:

1. **Problem (30s):** "ChatGPT is a sidebar. Slack doesn't see what the agent did. Buzz fixes both but ships half a platform company. swarm is the minimum experiment: same room, same audit trail."

2. **Room (60s):** Post in `#general`: `@swarm what's blocking the release?` Show streaming reply, tool audit line if shell/history runs, thread reply. `@core` runs the seeded pod.

3. **Specialist (60s):** Create a Bot named "Maya" (handle `@maya`). Open their 1:1. No `@` needed.

4. **Group (45s):** New group → pick Swarm + Maya. Message the room — both reply without `@`.

5. **Handoff (45s):** In a room, `@swarm draft the update; @ledger log the decision.` Show sequential replies in order.

6. **Governance (45s):** Bot calls `request_approval`. Human clicks Allow once. Bot continues in-thread.

7. **Computer (30s):** Open computer panel — shared workspace files the Bots wrote.

8. **Close (15s):** "Self-hosted, spec'd, tested, Docker. Admin-gated Bots, people DMs, `@team` pods, audit export. Buzz if we need signing and git; SlackHive if we live in Slack."

---

## Design principles (unchanged)

1. **Agents are messages, not modals.** If it happened, it's in the channel — including tool calls.
2. **Gate speculative work.** Phase 6 semantic search waited until keyword search failed; V3 features keep the same rule.
3. **Spec is the contract.** Code and `SPEC.md` disagree → bug.
4. **Portfolio-real.** Every phase ships something runnable, not a design doc.

---

## References

- [block/buzz](https://github.com/block/buzz) — thesis source
- [xAI Grok Bot docs](https://docs.x.ai/grok-bot/overview) — job/1:1/routine model
- [SlackHive](https://github.com/pelago-labs/slackhive) — Boss + specialist team on Slack
- [Operator](https://github.com/geekforbrains/operator) — agents-as-teammates in chat
- [OpenTag](https://github.com/linxidnju/OpenTag) — channel-native gateway + approvals
- [Team agent platforms survey](https://rywalker.com/research/team-agent-platforms) — market map
