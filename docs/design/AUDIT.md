# Visual audit — Swarm frontend

Scope: `frontend/src`. Judged against `AGENTS.md` §8a, which is the binding
contract. This document records what the interface is *now*, before the
targeted pass in `docs/design/PRINCIPLES.md` §"What changed".

## How this was measured

Everything numeric below was read off the running app, not estimated.

- Backend: `SWARM_DEMO=1`, uvicorn on `:8124`, throwaway SQLite.
- Frontend: the **built** `frontend/dist` served by that backend, so what was
  measured is what ships.
- DOM and computed-style figures: `getComputedStyle` + `getBoundingClientRect`
  + `Range.getClientRects` in Chromium over a seeded `#general` thread of
  **13 messages**, 5 agents, viewport 1262×568, then re-measured at
  720×700, 480×600 and 390×700 via CDP device-metrics overrides.
- Contrast: WCAG 2.x relative-luminance ratios computed from the actual
  `--*` token values in `styles.css`, including alpha compositing for the
  `*-wash` tokens. No axe score is quoted — axe was not run for this audit.

## Screens that are genuinely fine

Stated once, without manufacturing findings:

- **Roster.** One control grammar throughout: every row is a
  `.channel-link` (28px, 3px radius, 1px inset green spine when active).
  The fold (`Sidebar.tsx:144`, `styles.css:638`) is a genuinely good idea —
  folded, the roster becomes a rail of lamps and the words go to the tooltip,
  and it never folds on mobile where it would be unreadable. Held to 900px+
  by the drawer rule. Not touched.
- **Focus.** `:focus-visible` at `styles.css:180` is a 2px `--go-lit` ring at
  2px offset, and `styles.css:186` correctly flips it to `--ink` on a lit
  flap. Globally correct, no per-component drift.
- **Composer send affordances.** `Composer.tsx:534-543` — one primary key for
  send, `btn-danger` + stop icon when a run is live. Right shape, right place.
- **Approvals a11y.** `role="alert"` on send failure, `aria-live="polite"` on
  toasts, escalating `aria-live` in the work rail. Already handled; not
  re-litigated here.
- **Empty room.** `.talk-empty` (`styles.css:1083`) sits on the same left edge
  as the content it replaces, so nothing jumps when the first line lands.
  That comment at `styles.css:1074` is right and the code honours it.
- **No message bubbles.** Verified: zero. Every entry is a rule-separated
  printed line. This is the thing most systems get wrong and Swarm does not.

---

## Ranked findings

Ranked by impact on a first-time visitor in their first two minutes.

### 1 — The jump-to-newest key lands on top of the composer

**Severity: high. It is a collision, not a taste issue.**

`styles.css:1115` pins the control to the viewport:

```css
.jump-latest { position: fixed; bottom: 96px; right: var(--s5); z-index: 10; }
```

96px is a guess at how tall the composer is. It is 127px. Measured at
1262×568 with the room scrolled up:

| element | y range |
| --- | --- |
| `.jump-latest` | 448–472 |
| `.composer-hints` (context row) | **448–472** |
| `.composer-row` | 478–526 |
| `.composer-hints` (hand-off row) | 532–552 |

Exact overlap with the context readout row, zero clearance. At 390×700 and
480×600 — where `.btn` grows to 32px (`styles.css:2215`) — it also overlaps
`.composer-row` itself. It is a `z-index: 10` fixed layer, so it wins.

You scroll up within the first few seconds of using this app. This is the
first interaction.

### 2 — The composer spends 127px of a 568px window on 48px of input

**Severity: high. 22% of the viewport.**

Measured stack inside `#composer`:

| row | height |
| --- | --- |
| `.composer-hints` (context + Detail) | 24 |
| `.composer-row` | **48** |
| `.composer-hints` (Hand to + Enter hint) | 20 |

The bottom row (`Composer.tsx:547-571`) is permanent chrome carrying two
things that are both already available:

- "Enter sends · Shift+Enter newline" duplicates the send button's own
  `title` at `Composer.tsx:540`, which already reads
  "Send — Enter. Shift+Enter for a new line."
- The "Hand to" chips duplicate the sidebar roster *and* the `@` key sitting
  40px to their left in the same row (`Composer.tsx:505-521`), which opens
  the identical mention list.

Three controls for one action, of which the permanent one is the least
discoverable and costs a full row of vertical space in a scrolling log.

### 3 — Code blocks in agent replies are a foreign object

**Severity: high. It is a §8a violation and it is on the main screen.**

`ui.jsx:97-126` renders every fenced code block — the single most common
thing an agent emits — with raw Tailwind utilities: `rounded-xl`,
`shadow-sm`, `border-zinc-800`, `bg-zinc-950/90`, `bg-zinc-900/60`,
`text-zinc-200`. Measured: 28 `zinc` declarations survive into the built
CSS bundle.

`styles.css:769` already defines the correct treatment for `pre` inside a
transcript — slot background, `--rule` border, `--r-key` radius,
`--slot-shadow`. It is never used, because `CodeBlock` brings its own.

So the transcript contains two code-block languages: the housing's machined
slot, and a zinc card with a soft shadow and a 12px radius. That is the
"second styling system" §8a forbids, and it is the one place a reader is
certain to hit it. It is also a nested card by §8a's own definition.

### 4 — `@mention` has no rule at all

**Severity: high for comprehension, invisible to a sighted scan.**

`ui.jsx:78` rewrites every `@name` in a message body to
`<span class="mention">`. `grep` finds **zero** rules for `.mention` in
`styles.css`, and the built CSS confirms it. Measured on the seeded thread:
5 mentions, all inheriting plain body colour at weight 400, no marker of
any kind — `@swarm` is indistinguishable from the words around it.

Handing a room to a teammate is the product's core verb (§6 of AGENTS.md:
"Triggers: `@mention` in rooms"). It is currently the least marked thing on
screen.

The good news is the precedent already exists: `.agent-card-handle`
(`styles.css:1741`) already treats a handle as machine register — Departure
Mono, `--go-lit`. Matching it is consistency, not invention.

### 5 — `--hold-lit` fails AA as text on every surface it is used on

**Severity: medium. A conformance defect, not a taste call.**

`--hold-lit: #e6393f` (`styles.css:69`) is used as *text* in 20+ rules. As
text it does not clear 4.5:1 anywhere it matters:

| background | ratio | |
| --- | --- | --- |
| `--housing-raised` `#232120` | **3.83** | fail — `.chip-hold`, `.approval-detail-none` |
| `--hold-wash` over housing | **3.70** | fail — `.btn-danger`, `.failure-heading` |
| `--hold-wash` over raised | **3.39** | fail |
| `--housing` `#1b1917` | **4.19** | fail — `.channel-link.needs-you`, delete hover |

`--go-lit` on the same surfaces: 5.23 / 4.80 / 5.71. Green clears everywhere
red does not. The hold hue is doing an identical job and failing where its
opposite succeeds — which is not a case for a third hue, it is a case for
this hue to be legible.

Same class of defect at smaller scale: `.qs-hint` (`styles.css:1100`) is 11px
`--ink-3` on `--housing-raised` at **4.27:1**. The sidebar section counts are
worse still — `Sidebar.tsx:414` puts an inline `opacity: 0.55` on `--ink-3`
over `--housing-deep`, which composites to **2.35:1**. Not on the main
screen, and not fixed in this pass; recorded so it is not lost.

### 6 — The author column sits 3px below the line it labels

**Severity: medium. This is the transcript's reading rhythm.**

`.entry-head` carries `padding-top: 3px` (`styles.css:738`) with nothing
compensating for it. Measured on every row of the seeded thread, the offset
between the top of `.entry-author` and the top of the body's first line box
is **3px, every time** — a hand-applied nudge that never quite lands, rather
than alignment.

The same column has a second problem. `.entry` is a grid and `.entry-head` is
a grid item, so it stretches. Measured on a 216px agent entry with a two-line
job description: the head column is **191px tall**, and `.entry-job` — which
is clamped to 2 lines (`styles.css:747`) — sits at the bottom of it, ~130px
below the name it belongs to. The author and their role read as two unrelated
rows.

Not fixed in this pass; the exact fix is one declaration. Recorded here so it
is the obvious next item.

### 7 — The same six destinations are announced twice, and one number three times

**Severity: medium-low, but it is the first thing a new user sees.**

`Sidebar.tsx:71` (`PRIMARY_NAV`) and `TopBar.tsx:64` (`VIEWS`) are the same
six items with the same six labels — Board, Work, Talk, Roster, Knowledge,
Approvals — rendered as two separate controls in two separate regions.

The pending-approval count then appears three times: a `.sidebar-nav-count`
on the roster row (`Sidebar.tsx:226`), a `.chip-hold` reading "1 to clear" in
the topbar (`TopBar.tsx:179`), and the Approvals flap itself. Measured on the
seeded board: sidebar nav reads `["Board","Work","Talk","Roster","Knowledge","Approvals1"]`,
topbar chips read `["1 to clear"]`.

The world tolerates a lamp repeated — a lamp is how you read a board without
reading. Three *different treatments* of one number (bare mono digit, worded
chip, flap face) is not a lamp, it is three answers.

### 8 — Thirty-plus class names are referenced in JSX with no rule anywhere

**Severity: medium as a systemic risk; low per instance.**

Extracting every `className`/`class=` token from `frontend/src` and diffing
against the rules in `styles.css` yields 145 tokens with no matching rule.
Most are noise from my parser, but these are real:

`text-mono-xs`, `text-subtle` (`App.jsx:1124`, `:1151`, `:1081` — 14 uses),
`.scrollable`, `.card-interactive`, `.template-grid`, `.math-fallback`,
`.avatar-emoji`, `.drawer-sub`, `.mem-title`, `.agent-chip-card`,
`.place-chips`, `.report-card`, `.work-overview`, `.status-card-label`,
`.work-rail-expand-label`, `.work-retry`, `.plugin-tools`, `.file-list`,
`.knowledge-form-card`, `.agent-msg-name`, `.agent-msg-job`, `.mention`.

`.agent-msg-name` and `.agent-msg-job` matter most: they are grouped with
`.agent-card-handle` at `styles.css:1741-1742` as though they were part of the
same rule, and a reader would reasonably believe they are styled. They are
not.

`text-mono-xs` and `text-subtle` are not Tailwind utilities, so Tailwind
emits nothing for them — verified: 0 occurrences in the built CSS. They read
like utility classes, so they survive review, and they render as nothing.

### 9 — 286px of permanently blank gutter inside the reading rhythm

**Severity: low.**

`.entry-foot` (`styles.css:818`) reserves `min-height: 22px` on every single
row for controls that are `opacity: 0` until hover (`styles.css:1043`).
Measured: 13 entries × 22px = **286px of empty space** in a log viewport that
is 389px tall.

The reservation is correct — without it the transcript would reflow on every
hover — and it is correctly disabled under `@media (hover: none)`
(`styles.css:1056`). Not a bug. But it is a lot of nothing, and it is the
thing that makes the roll feel looser than a printed roll should.

---

## What changed

The pass that follows these findings is described in
`docs/design/PRINCIPLES.md`. Findings 1–5 were fixed there; 6–9 were left
deliberately and are the backlog.

### Verified after the pass

Re-measured on the same seeded thread, in the same built app, at the same
three widths. Every figure below is measured, not estimated.

| finding | before | after |
| --- | --- | --- |
| 1 — jump key vs composer | overlapped the context row exactly (448–472 vs 448–472); overlapped the composer row at 390/480 | inside `#log` at every width; `overlapsRow: false`, `overlapsHints: false` at 720, 480, 390 |
| 2 — composer height | 127px (24 + 48 + 20) | **101px** (24 + 48); 26px returned to the log |
| 3 — code block language | 28 `zinc` declarations in the bundle, `rounded-xl` + `shadow-sm` | **0** `zinc` in the bundle and in the DOM; slot `rgb(12,11,10)`, 3px radius, `--slot-shadow` |
| 4 — `@mention` | no rule; 5 mentions rendering as plain body text | 5 mentions in Departure Mono 12.88px at `--go-lit` |
| 5 — `--hold-lit` as text | 3.39 / 3.70 / 3.83 / 4.19 on four surfaces | `#ec6e72` → 4.75 / 5.19 / 5.37 / 5.88; `.qs-hint` 4.27 → 5.96 |

Regression check after the token change: Board, Work, Talk, Roster,
Knowledge and Approvals all render, flap faces intact, zero horizontal
overflow at every width measured.

### Bundle size

The baseline in `docs/FRONTEND-AUDIT.md` (505.45 kB raw / 142.44 kB gzip) was
taken **before** the run-inspector work landed, so the honest comparison is
against a build of the same tree immediately before this pass.

| | raw | gzip |
| --- | --- | --- |
| `docs/FRONTEND-AUDIT.md` baseline | 505.45 kB | 142.44 kB |
| same tree, before this pass | 529.91 kB | 148.56 kB |
| same tree, after this pass | **527.67 kB** | **147.76 kB** |
| delta from this pass | **−2.24 kB** | **−0.80 kB** |

The pass is a net **reduction**. CSS went 81.60 → 80.20 kB and the app chunk
185.51 → 184.67 kB, because deleting Tailwind's `zinc` utilities from
`CodeBlock` and the composer's hint row cost more JS and CSS than the new
rules added. Nothing was code-split or otherwise restructured; the vendor
chunk is byte-identical at 259.61 kB / 81.46 kB gzip.