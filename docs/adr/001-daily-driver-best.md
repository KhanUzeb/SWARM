# ADR 001 — "Best" means daily-driver ready

Date: 2026-09-06. Grilled with the user (`/grill-with-docs` round).

## Decision

The single outcome that proves Swarm is "the best" product it can be:
**the user runs their real daily work through it with agents** —
not a pitch-perfect demo, not craft for its own sake, not deeper
capability alone.

## Frontiers (all four, in this order)

1. **Talk view defects** — zero visual/UX breaks in chat. Chat is home;
   every jammed button or clipped label vetoes trust.
2. **Onboarding flow** — register → first bot → first reply in under
   5 minutes on a fresh database.
3. **Work rail depth** — the rail is the reason to stay: traces,
   artifacts, approvals, all legible without leaving the room.
4. **Agent quality** — better replies via context, knowledge, tools,
   and fewer error bubbles.

## Guardrail

Stay inside `VISION.md` scope: no Nostr/signing, no multi-tenant SaaS,
no mobile native, no cloud VM. Depth within current bounds — judge
every ticket against daily-driver value.

## Consequences

- Talk-view bugs outrank new features until the view is clean.
- Each frontier becomes its own ticket thread (`/to-tickets` next).
- Success is measured by a real day of use, not by demo smoothness.
