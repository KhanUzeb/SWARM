# PROBLEM.md — swarm

## The problem

Team chat and AI assistance live in different tools.

- **Slack** has no durable memory of what an agent did in a private session five minutes ago.
- **ChatGPT / Claude** have no memory of what the team decided in Slack.
- **Every "AI teammate" product** bolts a bot onto an existing chat surface instead of treating the agent as a peer with the same primitives as a human: channel membership, message history, identity, and a visible audit trail.

When an agent runs a shell command, searches history, or drafts an outbound email, that work is invisible unless someone copy-pastes it back into the thread. The team cannot delegate with `@name`, cannot see tool calls alongside human messages, and cannot approve risky actions in the same room where the decision was made.

## The hypothesis Buzz names — and what we're testing

Block's [Buzz](https://github.com/block/buzz) states it directly: one event log, one identity model, humans and agents both speaking it, same audit trail. Agents join channels, run workflows, review code, and leave the same record as everyone else.

That's the right *shape*. Buzz's implementation — Rust, Nostr relay, cryptographic signing, git hosting, workflow engine, canvases, huddles, desktop app — is a full product with a security model and multi-tenant hosting. Cloning it teaches infrastructure, not whether the chat-native agent model actually helps.

**swarm tests one question:**

> Does putting an agent in the same room as humans — with the same message history, the same `@mention` trigger, and tool calls posted as visible audit messages — produce something more useful than a chatbot sidebar?

Everything else (signing, multi-tenancy, git integration, canvases, Slack connectors) is infrastructure in service of that question, not the question itself.

## Comparable approaches (2025–2026)

| Approach | Examples | Tradeoff |
|----------|----------|----------|
| **Full workspace (Buzz)** | block/buzz | Complete vision; massive surface area |
| **Slack bolt-on** | SlackHive, Operator, OpenTag, Slack Agent Mesh | Uses existing chat; agents invisible outside Slack; app registration + OAuth |
| **Orchestration runtime** | Orloj, Clawix, GAIA | Production governance; no human-facing team UI |
| **IDE-native** | cursor-agents, Sisyphus | Great for coding; not team-visible async work |
| **Cloud job Bots** | xAI Grok Bot | Named roles + 1:1s + routines; vendor-hosted, cloud computer |

**swarm's slice:** self-hosted workspace, Buzz-shaped agent model, Grok Bot-style named jobs — without Nostr, without Slack dependency, spec'd and Docker-deployable today.

See `VISION.md` for the full competitive map and V3 scope.

## Who this is for

1. **Portfolio / interview** — demonstrates real-time systems (WS fanout), agent integration (mention-triggered, tool audit, streaming), and API design (dual REST/WS) as one product.
2. **Solo builder** — local AI ops desk: Sales, Chief of Staff, Code, Expense Bots with 1:1s, skills, and routines.
3. **Small team (future V3)** — self-hosted workspace where coworkers see agent work and approve actions in-thread.

## Non-goals

- Not a Slack replacement or Buzz clone.
- Not production-hardened multi-tenant SaaS (auth is portfolio-grade; admin role is gated to V3).
- Not a cloud VM or browser automation platform (shared sandbox only).
- Not Salesforce/Slack/Notion connectors (job templates describe roles; they do not call APIs).

## Definition of done

### V1 (shipped)

- [x] Channels persist (SQLite)
- [x] Live WS broadcast per channel
- [x] `@swarm` triggers context-aware LLM reply in-channel
- [x] CLI posts/reads without WebSocket
- [x] Agent failures surface in-channel; relay never 500s

### V2 + Phase 10 (shipped)

- [x] Auth, multi-agent personas, tools, threads, reactions, Langfuse, Docker
- [x] Custom agents + memory, streaming UI, reconnect
- [x] Named job Bots, 1:1 DMs, skills, routines, approvals, shared computer, bot handoffs

### V3 (partial — see `VISION.md`)

- [x] Five-minute onboarding + demo mode (V3.2, V3.7)
- [ ] Admin role for Bot creation
- [ ] Agent teams (`@team-name` bundles)
- [ ] Human DMs, semantic history, audit export — each gated on actual need

Success for the documentation: someone reads `VISION.md` + `SPEC.md` cold and knows what swarm is, what it isn't, and how it compares to Buzz and SlackHive — without asking the author.
