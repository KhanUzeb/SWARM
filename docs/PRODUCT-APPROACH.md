# Swarm — Product Approach

Concrete statement of what Swarm is, who it serves, and how the pieces fit.
Status: implemented (see SPEC.md for the technical contract).

## 1. Problem

Small teams using chatbots lose the thread: answers arrive, but nobody can
see who is working on what, what needs approval, what the agent knew, or
where the result went. The work is illegible.

## 2. Who it serves

A self-hosting team (5–50 people) that wants AI teammates inside their own
workspace — not a Slack app, not a separate agent dashboard, not a black box.

## 3. Core loop

```
Human writes in a room → named agent replies in-thread
  → every reply is a tracked work session (queued → running → done)
  → tool use, approvals, and artifacts attach to that session
  → durable knowledge (memory + knowledge base) makes the next reply smarter
```

Three rules make the loop trustworthy:

1. **Chat is home.** Runs, routines, and handoffs all surface as work
   sessions in the same conversation — never a separate tool to check.
2. **Attention has an address.** Approvals, failures, and cancellations land
   in the Work rail with an explicit next action (approve / deny / retry).
3. **Nothing is hidden.** Tool calls persist as in-thread audit messages,
   reports download as artifacts, and event cursors replay after reconnects.

## 4. How the pieces fit

| Piece | Role | Non-goal |
|---|---|---|
| Channels / DMs / threads | Where work happens | Not a Slack clone; no huddles, no canvases |
| Agents (`@name`) | Named teammates with jobs, tools, memory | Not autonomous employees; supervised by default |
| Work sessions (`/api/work`) | One normalized view over chat replies, workflow runs, routines, handoffs | Not a new orchestrator; it observes, doesn't re-run |
| Approvals | Human gate before external sends, publishes, deletes | Not fine-grained ACLs; one clear gate |
| Memory (`remember`/`recall`/`forget`) | Per-agent notes, channel or global | Not cross-agent shared brain |
| Knowledge base | Durable docs agents actually consult at reply time | Not vector RAG (keyword FTS5 + LIKE today) |
| Context meter + compact | Visible, budgeted conversation context | Not infinite context; trimming is explicit |
| Providers (API key or OAuth) | User's choice of model backend | Not a model marketplace; bring your own key |

## 5. Experience principles

- **Five-second legibility:** who is working, on what, and what needs you —
  visible without leaving the conversation.
- **Calm surfaces, loud signals:** midnight-indigo surfaces, violet for
  identity and running work, magenta for attention, jade only for
  confirmed success. See `docs/VISUAL-IDENTITY.md`.
- **Recovery over perfection:** retries, reconnect replay, restart recovery,
  and idempotent actions are features, not edge cases.
- **Local-first honesty:** SQLite, no cloud dependency, secrets sealed;
  the honest-gaps list in README.md stays current.

## 6. Success criteria

- A new user goes from sign-in to an approved agent run in under 5 minutes.
- Every agent reply links to its work session, tools used, and context cost.
- Full backend suite green, production frontend build under ~350KB JS.
- No provider secret, hidden prompt, or internal path ever reaches the UI.

## 7. Definition of best (ADR 001)

"Best" means **daily-driver ready**: real work, real agents, every day.
Four frontiers in order — Talk view defects, onboarding flow, work rail
depth, agent quality — all inside `VISION.md` scope. See
[`docs/adr/001-daily-driver-best.md`](adr/001-daily-driver-best.md).
