# PROBLEM.md — swarm

## The problem

Team chat and AI assistance live in different tools. Slack has no memory
of what an agent did five minutes ago in a different channel. ChatGPT/
Claude sessions have no memory of what the team said in Slack. Every
"AI teammate" product bolts a bot onto an existing chat surface instead
of treating the agent as a peer with the same primitives as a human:
a channel membership, a message history, an identity.

Block's `buzz` names this directly — one event log, one identity model,
humans and agents both speaking it, same audit trail. That's the right
shape. Their implementation (Rust, Nostr relay, git hosting, workflow
engine, canvases, huddles) is a full product with a security model,
multi-tenant hosting, and a mobile client. That's not a weekend build,
and it's not what's needed to test the actual idea.

## What's actually being tested

One question: **does putting an agent in the same room as humans, with
the same message history and the same trigger surface, produce
something more useful than a chatbot sidebar?**

Everything else — signing, multi-tenancy, git integration, canvases —
is infrastructure in service of that question, not the question
itself. `swarm` answers it with the minimum viable version: a relay,
channels, and one agent that reads what everyone else reads.

## Who this is for

- Primarily: a portfolio piece. Demonstrates real-time systems (WS
  fanout), agent integration patterns (mention-triggered, context-
  windowed), and API design (dual REST/WS write paths) in one project.
- Secondarily: a base to actually extend — the phases in `PROMPTS.md`
  are real feature work, not padding.

## Non-goals

- Not a Slack replacement. No intention of handling real team traffic.
- Not a Buzz clone. No Nostr, no signing, no git hosting, no multi-
  tenancy. Those are Buzz's actual hard problems and cloning them
  teaches nothing that isn't already covered by other projects (VERIS,
  context-router already cover distributed/protocol-level work).
- Not production-hardened by default. Phase 1 shipped token auth,
  impersonation checks, and a per-handle rate limit; that is still a
  portfolio demo's auth story, not a multi-tenant security model.

## Definition of done (v1, already shipped)

- [x] Channels persist across restarts (SQLite)
- [x] Messages broadcast live to every connected client in a channel
- [x] `@swarm` in a message triggers a context-aware LLM reply, posted
      back into the channel as a normal message
- [x] A CLI posts/reads without touching the WebSocket, JSON in/out
- [x] Agent failures surface in-channel, don't kill the relay

Everything past v1 is scoped in `PROJECT.md` and contracted in
`SPEC.md`. Phase 1 auth, Phase 8 streaming/reconnect, Phase 9 custom
agents/memory, and the rest of V2 are shipped; admin role and semantic
search are still gated.
