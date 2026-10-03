# Design principles — Swarm

The durable rules that decide what a Swarm change looks like. `AGENTS.md`
§8a is the contract for the visual system; this file is the reasoning that
connects that system to what Swarm is *for*, so that a future change can be
judged without re-litigating taste.

§8a already states the station board, the two hues, the render budget, and
the control grammar. These seven are the decisions *underneath* it — the
reasons the board has the shape it has. Every later visual decision must
trace to one of them.

---

## 1. The thread is the audit log

> Every tool call, handoff, and failure is printed in the order it happened —
> on the same surface, in the same register, as the conversation that caused
> it. Nothing an agent did happens off-screen or in a panel you must go find.

Swarm's claim is that you can see what your agents actually did. That claim
is only worth anything if the evidence is *in* the place you were already
reading, in the order it occurred. So a run's steps, a tool's arguments, an
error, a retry — all belong inline on the line, not behind a disclosure you
have to guess about or in a rail you have to remember to open.

**Why it constrains:** adding a card, a panel, or a badge to an agent action
requires taking the evidence off the roll. This principle says no.

**Traceable to:** the run inspector deliberately renders as printed lines on
the roll (`RunInspector.tsx:25-38`, `.inspector` in `styles.css:886-891`)
rather than as boxed steps — because that decision is what makes the claim
true. It also forbids the §8a "nested cards" rule in the transcript: a card
inside a card hides something.

## 2. The board reads before you read it

> A lamp behind a flap, and the flap face inverting. State is legible in
> peripheral vision, before a single word is parsed — and legible to someone
> who does not read this codebase's copy conventions.

This is §8a's "state is never a tint" with the reason attached. A tinted
background tells you *that* something is different but not *what*. A flap
reading `HOLD` tells you both, at 6px of attention, from across a room.
`Sidebar.tsx:80-99` is the reference implementation: the state gets the
machine register and the name gets ordinary sentence case, because the state
is the thing you scan for and the name is the thing you read.

**Why it constrains:** exactly two signal hues exist. Adding a third means
re-deciding the world, not picking a colour. It also means *one* number gets
*one* treatment — three different renderings of the same pending approval
count is not a lamp, it is three answers.

**Traceable to:** `.flap`, `.lamp`, and the two-hue rule in `:root`.

## 3. Two hues, and every one of them must be readable

> Green is live. Red is hold. Both must be legible as *text* on every surface
> they land on — because state in Swarm is carried by words and marks that
> are sometimes small.

A hue that is fine as a 6px lamp and unreadable as 11px type is not a working
signal. `--hold-lit` sat at 3.39–4.19:1 as text on four different surfaces
while `--go-lit` cleared 5:1 on all of them. That is not a reason for a third
hue or a softer red that reads as decorative. It is a reason to lift the
existing enamel until it does its job. Enamel is opaque; a wash is not a
colour.

**Why it constrains:** any new signal must be checked against AA as text, not
just as a mark. A 3px dot passes at 1.4:1 and still tells a low-vision user
nothing.

**Traceable to:** §8a's two-hue rule; `docs/design/AUDIT.md` finding 5.

## 4. Risky actions are slow, safe ones are fast

> Handing a room to a teammate is one keystroke and fires immediately.
> Granting an agent the ability to act is a decision you read before you
> make. The interface must never blur which kind of thing it is asking of you.

Swarm has no permission model in the UI — a bot raises an approval before any
outward-facing or destructive action, and you decide it in one inbox. That
means the interface's job is to make the *cost of being wrong* visible before
the click, not after.

So: destructive and irreversible actions get an explicit confirm
(`App.jsx:679`, `MessageList.tsx:566`); pending approvals are flap modules
with a lamp because they are genuinely stateful; decided approvals drop to
plain register with no lamp because they are no longer state, just a record
(`ApprovalsInbox.tsx:30-37`). And things you already know — "Enter sends" —
do not get permanent screen space, because attention spent on a permanent
hint is attention not spent on the thing that actually needs a decision.

**Why it constrains:** progressive disclosure beats persistent chrome (AGENTS.md
§2). If a control is safe and reversible, its instructions belong in a
tooltip. If it is risky, its context belongs on screen.

**Traceable to:** §8a's restraint on chrome; `ApprovalsInbox.tsx:30-37`.

## 5. One control grammar, or the world stops being one object

> Everything you can press is the same physical thing: 3px radius, a real
> edge, an inset top highlight, one stroke weight of lucide at one size. A
> surface that introduces its own visual language is a surface from a
> different product.

The station board only works if it is one board. `CodeBlock` rendering fenced
code in Tailwind `zinc` utilities with a soft shadow and a 12px radius, while
`styles.css:769` already defined the correct machined slot — that is two
boards stacked in one transcript, and it appeared exactly where an agent's
output is most likely to contain code.

The same rule governs dead class names. `text-mono-xs` and `text-subtle`
look like utilities, emit nothing, and render as nothing. A class that refers
to a rule nobody wrote is the same failure as two styling systems: the
markup claims a control that the stylesheet does not have.

**Why it constrains:** no new colour, no new radius, no new shadow outside
the three declared in `:root`, no hard-coded hex in JSX. Extending the system
means adding a rule to `styles.css`, not a variant to a component.

**Traceable to:** §8a's one-stylesheet rule; `docs/design/AUDIT.md` findings 3
and 8.

## 6. The app is open all day, so the budget is architecture

> No blur, no canvas, no gradient wash, no infinite animation on an idle
> surface, no `width`/`height` transition. Depth comes from three shadows
> declared once in `:root`.

This is not an aesthetic preference, it is a durability claim. Swarm is a
desk you leave open in a tab for eight hours on a laptop. `backdrop-filter`
and infinite keyframes are not free at 9am and they are not free at 6pm.
The render budget is what makes "leave it open" true.

Motion that carries meaning stays: the flap sets type when a state changes
(`Flap.tsx:32-40`), and it is bounded — a module only travels when its state
actually changes, and the first render never animates. A spinner during a
genuine wait is fine. Motion that decorates is not.

**Why it constrains:** any proposal that reaches for a blur, a gradient, or a
new keyframe must justify itself against eight hours of open tab, not against
a screenshot.

**Traceable to:** §8a's render budget; `docs/FRONTEND-AUDIT.md` §2 on the
per-token smooth scroll, which is the same rule aimed at JS-driven motion.

## 7. Reading a board should cost less than reading a document

> Dense, high-contrast, tightly set, and quiet. The transcript is the loudest
> thing on the screen and everything else stays out of its way.

Swarm shows you more state per screen than a chat app and less prose than a
document. The design job is to hold both without the screen becoming a wall:
author column held to a fixed 128px so the body starts at the same x on every
row, `--measure` capping the line so long replies stay readable, one rule
between entries, and the housing kept dark so the flap faces and the two
signal hues are the brightest things present.

Restraint here is not minimalism for its own sake. Status colour is used
*sparingly and meaningfully* — which is only possible because the neutral
range is genuinely neutral and does not compete. A screen that is mostly
signal is a screen where nothing is.

**Why it constrains:** tightening spacing and the type scale is always
available and almost always the right move; adding a surface, a badge, or a
panel is almost never it.

**Traceable to:** §8a's grammar; the measured 209px dead gutter and 3px
baseline miss in `docs/design/AUDIT.md` findings 9 and 6.

---

## What changed, and which principle it maps to

One targeted pass on the **channel/thread view only**. Findings 1–5 of
`docs/design/AUDIT.md`, in impact order. Nothing else was touched.

| # | Change | Finding | Principle |
| --- | --- | --- | --- |
| 1 | `.jump-latest` re-anchored out of the composer's space (it overlapped the context row, and the composer row under 480px) | 1 | **7** — reading a board should cost less than reading a document |
| 2 | `CodeBlock` brought onto the housing's own code treatment; the Tailwind `zinc` card deleted | 3 | **5** — one control grammar |
| 3 | `.mention` given the machine-register treatment already used for handles | 4 | **1** — the thread is the audit log; **2** — the board reads before you read it |
| 4 | `--hold-lit` lifted until it clears 4.5:1 as text on every surface it is used on; `.qs-hint` moved to `--ink-2` | 5 | **3** — every hue must be readable |
| 5 | The composer's permanent "Enter sends / Hand to" row removed; both facts moved to the place that already owns them | 2 | **4** — risky actions are slow, safe ones are fast |

**Explicitly not done,** so the boundary is on the record:

- No third signal hue. No gradient. No second stylesheet. No new shadow —
  depth still comes from the three declared in `:root`.
- No new animation. The flap cadence and the reduced-motion path are
  untouched (principle 6).
- Findings 6, 7 and 9 are **not** fixed. Finding 6's fix is one declaration
  (`.entry-head { align-content: start }`) but it changes the transcript's
  vertical rhythm and deserves its own pass with its own measurement.
  Finding 7 (three renderings of one number) is a product decision about
  where the count lives, not a styling fix, and it should not be made inside
  a visual pass.
- The dead class names in finding 8 are mostly outside the main screen and
  mostly belong to surfaces this pass deliberately did not touch. They are
  inventoried in the audit rather than silently swept.