# Swarm visual identity

Picked from a 50-palette color-combination cheatsheet against a midnight
aurora reference (near-black canvas, violet band, magenta bloom,
blue edge, pale corner haze, fine grain).

## Picks

- **Foundation — 08. Midnight Magic.** Deep navy/indigo surfaces
  (`#07070d` canvas → `#141422` surface), cool-white text (`#f4f0fa`).
  Calm infrastructure feel; saturated color is reserved for state.
- **Accents — 18. Galaxy Dust.** Violet `#9d7bff` for identity, running
  work, and primary actions; magenta `#e5499b` for attention moments.
  Periwinkle `#7aa2ff` marks running state; jade `#5da37a` only ever
  means confirmed success.

## Why not the others

- Warm families (Terracotta Dream, Warm & Cozy, Caramel Latte, Autumn
  Leaves) fight the blue-hour backdrop and read decorative, not
  instrumental.
- Pastels and neons (Citrus Crush, Candy Shop, Tropical Vibes, Papaya
  Pop) fail contrast on dark surfaces and cheapen trust UX
  (approvals, failures, audit).
- Pure monochromes (Monochrome Chic, Scandi Minimal) leave no channel
  for agent identity vs. system state.
- Adjacent darks (Indigo Nights, Galaxy-adjacent Navy & Mustard) were
  finalists; Midnight Magic won for the teal-to-lavender ramp that
  matches the backdrop glow, Galaxy Dust for the magenta peak.

## Background treatment

`body::before` recreates the reference in layered gradients (violet
band, magenta bloom low-right, blue right edge, pale corner haze);
`body::after` adds SVG-noise grain at 7% opacity. App surfaces stay
solid — glow lives at the margins and never competes with the work.
`prefers-reduced-motion` freezes all motion.

## Token map (`frontend/src/styles.css`)

| Token | Value | Use |
|---|---|---|
| `--color-bg` / elevated / surface / hover | `#07070d` / `#0d0d17` / `#141422` / `#1c1c2e` | Canvas → interactive surfaces |
| `--color-text-primary` / secondary / muted | `#f4f0fa` / `#b9b3cc` / `#8b85a3` | Text ramp |
| `--color-brand` / hover / subtle | `#9d7bff` / `#b39dff` / `rgba(157,123,255,.14)` | Identity, primary actions |
| `--accent-magenta` | `#e5499b` | Attention, never body text |
| `--state-running` / success / warning / danger | `#7aa2ff` / `#5da37a` / `#e8a33d` / `#e5605c` | The only saturated hues in UI |

## Rules

1. One dominant surface per screen; one accent per moment.
2. Every status uses the state ramp — never invent a new hue.
3. Bots get subtle identity color; humans get the violet bubble.
   The active chat model shows as a chip in the composer (grey dot =
   Auto, glowing blue dot = pinned model, provider name included) and
   as a tag on every agent bubble; token streams render live in a
   blue-outlined bubble with a    blinking caret. Stop is a red octagon
   beside send while generation runs; stopped replies use a dashed
   bubble with a Resume action. The composer is text-only — no voice
   input.
4. Borders stay at 5–12% white; depth comes from the backdrop.
5. Contrast: primary text ≥ 12:1, secondary ≥ 7:1 on surfaces.
