# Frontend audit — evidence baseline for Phase 6

Scope: `frontend/` (React 19 + Vite 8, built with bun). Read against
`AGENTS.md` §8a, which is the binding visual contract. Everything below was
measured on a production build or on the running app; nothing is estimated
unless the line says "estimated".

Method, so the numbers are reproducible:

- Bundle figures are verbatim from `cd frontend && bun run build`.
- axe ran against the built `frontend/dist` served by the real backend
  (`vite build` output + `uvicorn backend.main:app`), in Chromium, with
  `axe-core@4.13.0` tags `wcag2a, wcag2aa, wcag21a, wcag21aa, best-practice`.
- DOM figures come from a channel seeded with 119 messages, opened in the
  built app, measured in-page with `document.getElementsByTagName('*')`.
- Mobile figures come from CDP device-metrics overrides at each breakpoint.

Baseline commit state: no frontend source change is proposed here. The only
file this audit adds is this document.

---

## Summary

| # | Area | Severity | Finding | Where |
|---|------|----------|---------|-------|
| 1 | Render | **P0** | Message list is not virtualized; every loaded message is mounted. 24 DOM nodes/message → ~24k nodes at 1000 messages | `MessageList.tsx:290` |
| 2 | Render | **P0** | Per-token stream re-runs the whole `MessageList` body; `streamText` is a prop, so memoized rows still re-reconcile | `MessageList.tsx:180`, `App.jsx:372` |
| 3 | Bundle | **P1** | No route/view code-splitting at all. One 169 kB app chunk carries every view | `vite.config.js:22` |
| 4 | Bundle | **P1** | `manualChunks` sends all of `node_modules` to one `vendor` chunk; no budget gate in CI | `vite.config.js:25`, `.github/workflows/ci.yml:35` |
| 5 | A11y | **P1** | `aria-expanded` on a bare `<textarea>` — axe `aria-allowed-attr`, critical, reproduces on every view | `Composer.tsx:500` |
| 6 | A11y | **P2** | `.qs-hint` and trace step counts sit at 4.27:1, below the 4.5:1 AA threshold | `styles.css:971` |
| 7 | A11y | **P2** | Streaming message has no `aria-live` region; only a `sr-only` span | `MessageList.tsx:349` |
| 8 | A11y | **P2** | Composer mention/slash popovers are `role="listbox"` with no `role="combobox"` owner | `Composer.tsx:380` |
| 9 | Layout | **P2** | `.view-tabs` and `.channel-topic` are `display:none` under 480px — those views have no mobile entry point | `styles.css:1990` |
| 10 | Layout | **P2** | `#work-rail.collapsed` floats above the composer at `132px` offset; overlaps at short viewports | `styles.css:1956` |
| 11 | Keyboard | **P2** | Message log, thread panel, and work rail have no focusable container or skip link | `MessageList.tsx:251` |
| 12 | Keyboard | **P2** | `CommandPalette` has no `aria-activedescendant`, so the active row is announced only visually | `CommandPalette.tsx:129` |
| 13 | Reconnect | **P2** | Backoff caps at 15 s and is not reset on a failed *open*; no jitter | `App.jsx:382` |
| 14 | Reconnect | **P2** | Outbox flush is keyed to `wsStatus` only; a failed flush leaves messages queued with no retry until the next reconnect | `App.jsx:533` |
| 15 | Missing | — | No PWA manifest and no service worker | `frontend/index.html` |

Areas that are genuinely fine, stated once: the reconnect *contract* (backoff,
generation guard, outbox persistence, visible queued state) is well built and
needs no redesign; the mobile layout does not overflow at any breakpoint
measured; focus rings are present and correct globally; the design-system
invariants in §8a (one stylesheet, two signal hues, no blur/canvas/gradient
washes) are respected in the code I read.

---

## 1. Bundle size

### Measured baseline

`cd frontend && bun run build`, verbatim output:

```
vite v8.3.0 building client environment for production...
✓ 1921 modules transformed.
dist/index.html                             2.61 kB │ gzip:  1.05 kB
dist/assets/index-DNELWMxs.css             74.99 kB │ gzip: 13.92 kB
dist/assets/rolldown-runtime-CbXtAM7H.js    0.58 kB │ gzip:  0.36 kB
dist/assets/index-CNLWiqXL.js             169.09 kB │ gzip: 46.02 kB
dist/assets/vendor-BRWYZzom.js            258.18 kB │ gzip: 81.09 kB
✓ built in 863ms
```

Totals: **505.45 kB raw, 142.44 kB gzip** across HTML + CSS + JS. First paint
ships the CSS plus both JS chunks, so the critical path is roughly
**142 kB gzipped**, all of it blocking.

### What is in the chunks

`vite.config.js:24` has a single rule:

```js
manualChunks(id) {
  if (id.includes("node_modules")) return "vendor";
}
```

So `vendor` (258 kB raw / 81 kB gzip) is *everything* from `node_modules`,
and `index` (169 kB raw / 46 kB gzip) is *all* application code. There is no
split by library, so React, ReactDOM, `lucide-react`, `clsx`,
`tailwind-merge`, and `class-variance-authority` are fused into one blob with
no way to cache one independently of the others.

`lucide-react` is the largest single dependency in `node_modules` (44 MB on
disk unpacked, ~1,500 icon modules). It is imported as named exports in 14
places across the app, which tree-shakes to only the icons actually used — the
measured cost is inside `vendor` and cannot be separated further with the
current `manualChunks`.

### Findings

**P1 — no code-splitting at all.** `App.jsx` statically imports every view:
`CommandCenter`, `WorkHome`, `ComputerPanel`, `KnowledgeView`, `WorkRail`,
`ContextDrawer`, plus the five `ai-support/*` panels. `grep` for
`React.lazy` / `import(` across `frontend/src` returns exactly one hit, and it
is a comment (`ui.jsx:42`) about a future KaTeX load. A user who only ever
chats in Talk still downloads the computer panel, the knowledge browser, the
settings panels, and the whole run inspector.

**P1 — no bundle budget in CI.** `.github/workflows/ci.yml:35` runs
`bun run build` and checks only that it exits 0. Nothing parses the emitted
sizes, so a dependency that adds 100 kB merges silently.

### Obvious split candidates, highest value first

1. `WorkHome.jsx` + `WorkRail.jsx` + `ComputerPanel.jsx` — the run inspector
   and sandbox surfaces. Rarely open, never needed for first paint. Biggest
   single win, and they are exactly what Phase 6.2 will grow.
2. The five `ai-support/*` panels — reachable only through Command Center.
3. `KnowledgeView.jsx` — a whole view behind one nav item.
4. `lucide-react` into its own chunk so an icon change doesn't bust React's
   cache entry.
5. `ComputerPanel`'s file browser pulls no heavy deps today, but it is the
   natural home for anything added later — split it before that happens, not
   after.

---

## 2. Render cost of long threads and token streaming

### How the list renders

`MessageList.tsx:290` maps the entire `order` array into `MessageRow`:

```tsx
{roots.map((m, i) => {
  ...
  return <React.Fragment key={m.id}> ... <MessageRow ... /> </React.Fragment>;
})}
```

**There is no virtualization.** Every message the client has loaded is mounted,
with no windowing, no `content-visibility`, and no upper bound. The only
pagination is user-driven "Load earlier lines"
(`MessageList.tsx:265`), which *prepends* and makes the mount set larger.

### Measured DOM cost

Channel seeded with 119 messages, opened in the built app:

| Loaded | `.entry` nodes | Total DOM nodes |
|--------|----------------|-----------------|
| 50 (initial page) | 50 | 1,468 |
| 100 | 100 | 2,668 |
| 119 (all) | 119 | 3,122 |

Per-message cost is stable at **24 DOM nodes** (min 24, max 28 measured across
the thread; the max-41 case was an agent message carrying a model chip and a
work flap). Extrapolating the measured per-message figure:

> **1000 messages ≈ 24,000 DOM nodes**, all mounted, all reconciled on any
> state change. Heap at 119 messages was 5 MB.

One measured entry, showing where the 24 nodes go:

```
div.entry-head
  div
    div.avatar avatar-sm avatar-user
    span.entry-author
  span.entry-time
div.entry-main
  div.entry-body
    div.body rich
  div.entry-foot agent-msg-actions
    button.msg-mini-action
    div.relative
    button.msg-mini-action is-danger
```

### How streaming re-renders

Streaming state lives in `App.jsx` as `streamText`, and `agent_token` frames
append to it (`App.jsx:372`):

```jsx
setStreamText(prev => ({ ...prev, [msg.author]: ((prev[msg.author] || "") + delta).slice(-30000) }));
```

Every token therefore produces a new `streamText` object identity, which is
passed straight down as a prop (`App.jsx:774`).

**The memoization that exists is real and deliberate, and it is not sufficient.**
`MessageList.tsx:90` and `:375` show a previous commit did careful work:

- Shared frozen `EMPTY_REACTIONS` / `EMPTY_WORK_EVENTS` module constants, with
  a comment explaining that `map[id] || []` would allocate per render and
  defeat memoization — correct.
- A `useLatest` ref wrapper plus six `useCallback` stable handlers
  (`MessageList.tsx:96-214`) so parents passing fresh closures don't break
  `React.memo`. Also correct.
- `MessageRow` and `AgentTrace` are both `React.memo`. Correct.

What it does **not** cover: the streaming path bypasses `MessageRow` entirely.
Live lines render in a separate map at `MessageList.tsx:338`, so the
memoized rows are *not* handed new text — that part is well designed. But
`MessageList` is not memoized, and its render body still re-executes on every
token:

- `roots = order.map(...).filter(Boolean)` (`:179`) — full array rebuild
- `liveStreams = Object.entries(streamText).filter(...)` (`:180`)
- `quickStarts = buildQuickStarts(agents, allAgents, channelName)` (`:183`) —
  allocates on every token, and is only used by the empty-state branch
- two `allAgents.find(...)` lookups per row inside the map (`:296`, `:300`)
- the auto-scroll effect keyed on `streamChars` (`:177`) calls
  `scrollIntoView({behavior: "smooth"})` **per token**, which is a layout read
  plus a smooth scroll kickoff on every frame

So the honest verdict: **the streaming message is isolated from the memoized
rows, which was the right call, but the list component itself is not isolated,
and the per-token smooth `scrollIntoView` is the most expensive thing in the
loop.** I could not measure per-token mutation amplification directly — the
demo responder does not emit `agent_token` frames, and injected frames are
rejected because the hub replaces its per-channel socket — so this part is
read from source, not measured.

**P0 findings:**

- `MessageList.tsx:290` — mount-everything. At 1000 messages the browser is
  carrying ~24k nodes for a scrolling log.
- `MessageList.tsx:180` + `App.jsx:372` — `streamText` identity changes per
  token, re-running the whole list body: array rebuild over N messages, plus
  `allAgents.find` per row.
- `MessageList.tsx:177` — `scrollIntoView({behavior:"smooth"})` fires on every
  token. Also worth noting: per §8a there are no `width`/`height` transitions
  in the stylesheet, but this is a JS-driven smooth scroll on an idle surface,
  which the same render-budget rule is aimed at.

The fix for Phase 6 is a windowing layer (any of the usual suspects) plus
moving the stream buffer into its own component so `MessageList` stops
reconciling on token arrival.

---

## 3. Accessibility

**axe was run.** `@axe-core/cli` could not drive a browser on this host (no
Chrome; the bundled chromedriver rejects the installed Edge build), so
`axe-core@4.13.0` was injected directly into the built app served by the real
backend and run against the live DOM across the Talk, Work, Roster, and
Knowledge views plus the open command palette. 39–40 rules pass per view.

### axe results, verbatim

Login screen — 2 violations, 22 rules passing:

```
[serious]  color-contrast (6 nodes)
  <label class="field-label" for="login-handle">Handle</label>
  <label class="field-label" for="login-pass">Password</label>
[moderate] region (5 nodes)
  <div class="login-head">…<h1>Sign in to the desk</h1>…
```

Authenticated Talk view, with a seeded thread and an agent reply — 3
violations, 39 rules passing:

```
[critical] aria-allowed-attr (1)
  <textarea id="msg-input" … aria-expanded="false" class="composer-input" …>
[serious]  color-contrast (4 nodes)
  <span class="qs-hint">Ask the room</span>
  <span class="qs-hint">Generalist</span>
  <span class="qs-hint">Decision log</span>
[moderate] region (1)
  <textarea id="msg-input" …>
```

With the command palette open, the same 3 plus one extra contrast node.
Work, Roster, and Knowledge views: **0 violations, 39 rules passing** each.

### P1 — `aria-expanded` on a bare textarea

`Composer.tsx:500` puts `aria-expanded={mention.open || slash.open}` on the
plain `<textarea>`. `aria-expanded` is not supported on `role="textbox"`, so
axe flags it critical on every view. It should be `role="combobox"` with
`aria-controls` pointing at the popover, which also fixes the related finding
below. Verified in-page: the element has `role=null`,
`aria-expanded="false"`, `aria-controls=null`, `aria-autocomplete=null`, and
there is no `[role="combobox"]` anywhere in the document.

### P2 — contrast at 4.27:1

Measured values from axe:

```
fg #8a8377 on bg #232120 — 4.27:1, 11px, normal weight, needs 4.5:1
```

Four `.qs-hint` elements (`styles.css:971`, `--ink-3`) fail on the empty-room
quick-start cards. The trace step counter is the same failure with an inline
`opacity: 0.6` on top (`MessageList.tsx:603`). Both are small text, so the
AA threshold is 4.5:1, not 3:1.

### P2 — no live region for streaming

`MessageList.tsx:341` renders the streaming line as a plain
`.streaming-entry` div. It contains `<span className="sr-only">is still
writing</span>` (`:349`), which is the right idea but the wrong mechanism: an
`sr-only` span inside a non-live region is not announced. A screen reader user
gets no signal that an agent started or finished writing.

This matters more than a typical live-region gap, because `App.jsx:456`
clears `streamText` on every channel switch — a live region keyed to the
stream would need to survive that to announce completion correctly.

Approvals are handled well and I want to be clear about that: `role="alert"`
on send failure (`App.jsx:811`), `aria-live="polite"` on toasts
(`App.jsx:971`), and in the work rail `role="alert"` for needs-attention
(`WorkRail.jsx:159`) with `aria-live` escalating to `assertive` on the card
itself (`WorkRail.jsx:131`). The approvals path does not need work; the
streaming path does.

### Hand review — what axe does not cover

**Interactive elements are real buttons.** I checked every clickable in the
rendered app: all 42 focusables in the Talk view are native
`button`/`input`/`textarea` elements. No `div` with `onClick` anywhere in the
message list or sidebar. The two non-native cases in the codebase are both
handled: `ui.jsx:227` builds a `Dropdown` trigger as a `span[role="button"]`
with `tabIndex=0`, `aria-haspopup`, `aria-expanded`, and an Enter/Space
handler; `App.jsx:1145` gives the roster `Card` a `role="button"` with
`tabIndex={0}` and an Enter/Space `onKeyDown`.

**One stray focusable that should not be.** `App.jsx:1088` renders each file
row as `<div className="file-row" role="listitem" tabIndex={0}>`. It is in the
tab order, has no click handler, and is a listitem inside a `ScrollArea` that
is not a `role="list"`. It is a keyboard trap-shaped dead stop.

**Keyboard reach.** 42 focusables in document order in Talk: roster collapse,
new message, five nav items, the roster filter, each section's add button,
then every channel and teammate row, then delete affordances. Everything is
reachable and correctly labelled. Two real gaps:

- No skip link, and `#log` (`:251`) is a plain scrollable `div` — a keyboard
  user tabs through the entire roster before reaching the message list, and
  the log itself is not focusable so it cannot be scrolled by keyboard.
- The thread panel and work rail are `aside` elements with no focusable
  container and no focus trap on open; `Escape` closes them
  (`App.jsx:474`) but focus is not moved into them.

**Focus styling is present and correct.** `styles.css:177` sets
`outline: 2px solid var(--go-lit)` with `2px` offset on `:focus-visible`,
verified in-page: computed `outlineWidth 2px`, `outlineStyle solid`,
`outlineColor rgb(23, 168, 106)`, and `:focus-visible` matching on a focused
nav button. `styles.css:183` flips the ring to the housing colour on lit
flaps. `:focus` is correctly reset to `none` at `:176` so mouse users don't
see rings.

**Landmarks.** The `region` violation is the composer textarea sitting outside
any landmark. `#sidebar` is an `aside[aria-label="Roster"]` and the primary
nav has `aria-label="Destinations"` (`Sidebar.tsx:201`), so the sidebar side is
fine; the main content is a bare `<main id="main">` without an accessible name,
and the composer falls below it.

**Reduced motion is handled.** `styles.css:1998` collapses animation and
transition durations and stops the flap rotation while keeping the state
change legible. Good.

---

## 4. Mobile layout

Breakpoints in `styles.css`: `1280px`, `1100px`, `900px`, `760px`, `720px`,
`480px`, plus `hover: none` at `:927` and `prefers-reduced-motion` at `:1998`.

### Measured at each breakpoint

| Viewport | Horizontal overflow | Sidebar | Composer | Log | View tabs | `.btn` height |
|----------|--------------------|---------|----------|-----|-----------|----------------|
| 1280 | none | 260px, docked | 946px | 946px | visible | 24px |
| 900 | none | drawer, offscreen | 900px | 900px | visible | 24px |
| 720 | none | drawer, offscreen | 720px | 720px | visible | 24px |
| 480 | none | drawer, offscreen | 480px | 480px | **hidden** | 32px |
| 390 | none | drawer, offscreen | 390px | 390px | **hidden** | 32px |

**The layout does not overflow horizontally at any width measured**, including
a 119-message thread. The 900px breakpoint is the important one: the grid
collapses to a single column, `#sidebar` becomes a fixed off-canvas drawer
(`styles.css:1912`) with `.topbar-sidebar-trigger` appearing, and the work
rail / computer panel / thread panel become bottom sheets capped at `82dvh`.

**Is the roster usable on a phone?** Yes. `Sidebar.tsx:139` forces
`folded = collapsed && !open`, so the drawer is never the narrow icon-only
rail on mobile — it opens as a full sheet of words with the filter input and
all sections. Verified: drawer width 300px at 390px viewport, fully offscreen
(`right <= 0`) until opened.

**Does the message list reflow sanely?** Yes. At `720px` (`:1977`) `.entry`
drops from two grid columns to one and `.entry-head` goes horizontal, so the
author line stays on one row above the body instead of squeezing the body into
a narrow column.

### P2 — what breaks first

1. **`styles.css:1990` — `.view-tabs { display: none }` under 480px.** The
   Board / Work / Talk / Roster / Knowledge switcher disappears entirely. On a
   phone that is the primary way to move between views, so below 480px the
   sidebar's `PRIMARY_NAV` becomes the *only* navigation. If Phase 6.2's run
   inspector is reachable from a view tab, it is unreachable on a phone unless
   it also gets a sidebar entry.
2. **`styles.css:1956` — `#work-rail.collapsed { inset: auto var(--s3) 132px auto }`.**
   The collapsed rail pill floats 132px above the bottom edge to clear the
   composer. The composer measures 111px tall at 480px, so on a short viewport
   (landscape phone, or any small height) the pill and the composer collide.
   The bottom-sheet form at `:1946` handles tall viewports fine; this fixed
   offset does not.
3. **`styles.css:1993` — `.channel-topic { display: none }` under 480px.**
   Minor, and arguably correct — but it means channel context is phone-only
   in the Command Palette, with no in-app affordance.
4. **`.entry-body` uses `max-width: var(--measure)`** (`:752`) with no narrow
   override, but `--measure-app` goes to `100%` at `720px` (`:1976`) and the
   padding rules at `:1979` cover `.log-inner`, so this resolves correctly.

---

## 5. Keyboard navigation

### Command palette (`Ctrl/Cmd+K`)

Bound at `App.jsx:468`. Reachable today:

- `↑` / `↓` move the active index (`CommandPalette.tsx:75-83`)
- `Enter` opens the selection (`:85`)
- `Escape` closes (`:71`), plus a click-outside handler
- `onMouseEnter` sets the active index, so mouse and keyboard share one index
- the list scrolls the active row into view via `[data-index]` (`:60`)
- commands cover channel creation, agent creation, group/DM creation, computer
  panel toggle, three view switches, every room, and every agent
  (`App.jsx:673-684`)

Other global shortcuts: `Ctrl/Cmd+C` toggles the computer panel, `Ctrl/Cmd+Shift+W`
toggles the work rail (persisted to `localStorage`), and `Escape` closes
palette, thread, run detail, and sidebar at once (`App.jsx:474`).

Composer-local: `↑`/`↓` navigate the mention and slash popovers, `Enter`/`Tab`
accept, `Escape` dismisses (`Composer.tsx:205-254`).

### What has no keyboard path at all

1. **No skip link, and `#log` is not focusable** (`MessageList.tsx:251`). A
   keyboard user tabs the entire roster before reaching any message. This is
   the single biggest navigation gap.
2. **Thread panel and work rail do not move focus on open.** `App.jsx:940`
   renders `<aside id="thread-panel">` with no focusable container and no
   focus trap; `WorkRailSheet` likewise. Opening one leaves focus on whatever
   opened it, and `Escape` closes without returning focus.
3. **`App.jsx:1088` — `.file-row` is `tabIndex={0}` with no handler.** A
   keyboard dead stop in the Files view.
4. **`QuickCreateModal` has no focus trap** (`App.jsx:1272`). It does declare
   `role="dialog" aria-modal="true"` and autofocus the first input (`:1306`),
   and `Escape` closes — but `Tab` will walk out of the dialog into the page
   behind it.
5. **`AgentTrace` is a single toggle button** (`:592`) — correct, and its
   `aria-expanded` is right. No issue.

### P2 — palette active row is visual-only

`CommandPalette.tsx:129` marks the active row with `data-active={isActive}`
and a trailing `CornerDownLeft` glyph. There is no `aria-activedescendant`, no
`role="listbox"`/`role="option"`, and no `aria-selected` — the input keeps
DOM focus throughout, which is correct for typing, but a screen reader user
gets no announcement of which row `Enter` will open. Adding
`role="listbox"` + `aria-activedescendant` would fix it without changing the
focus model.

Focus rings are present everywhere: `:focus-visible` at `styles.css:177`,
verified live at 2px solid `--go-lit`.

---

## 6. Reconnect behaviour

WebSocket client is in `App.jsx:333-394`. This is the best-engineered part of
the frontend; the two findings below are refinements, not defects.

### What exists

**Exponential backoff with a cap** (`App.jsx:378`):

```jsx
ws.onclose = (ev) => {
  if (gen !== wsGen.current) return;                  // superseded socket
  if (intentionalClose.current || ev.code === 4001) { setWsStatus("offline"); return; }
  setWsStatus("offline");
  const delay = Math.min(1000 * 2 ** reconnectAttempt.current, 15000);
  reconnectAttempt.current++;
  reconnectTimer.current = setTimeout(connectWs, delay);
};
```

1s → 2s → 4s → … → 15s cap. `4001` (the server's auth-failure close code from
AGENTS.md §3) is treated as terminal rather than retried, which is right.

**Generation guard.** `wsGen` increments on every connect and is bumped again
in `disconnectWs` (`App.jsx:391`). Every callback checks `gen !== wsGen.current`
and bails, so frames from a stale socket can never land in state — this is what
makes rapid channel switching safe.

**Resume via `last_seen_id`** (`App.jsx:348`), tracked per channel in
`lastSeenId` and advanced in both `remember` (`:130`) and `ingestLive` (`:169`).

**Persistent outbox.** `queueMessage` (`:485`) writes to
`localStorage` under `swarm.outbox` (`App.jsx:72`), so queued messages survive
a tab close, not just a disconnect. Two trigger paths: a `0`/`5xx` response
(`:511`) and a thrown fetch (`:525`) — both queue rather than drop.

**Flush on reconnect** (`App.jsx:533`), gated on `wsStatus === "connected"`,
iterating in order, stopping at the first failure and keeping the remainder
queued (`:547`).

**The user sees all of it.** Offline/connecting/connected is visible in the
sidebar footer (`Sidebar.tsx:361` → "Live" / "Linking" / "No link"), the
composer takes an `offline` prop (`App.jsx:840`), queued messages get a
`role="status"` banner with a count (`App.jsx:827`), and a hard send failure
gets a `role="alert"` banner with Retry/Dismiss (`App.jsx:810`). Backoff also
retries once on the backend's 500 ms write rate limit (`:504`).

**Work/run events are replay-safe independently.** `sessionStore.js:49` keeps
a monotonic per-`work_id` cursor and ignores any event at or below it, so a
reconnect cannot duplicate trace steps; `refresh` (`:59`) replays missed
events for active sessions plus terminal ones never fetched.

### P2 findings

1. **No jitter, and `reconnectAttempt` only resets on a successful open**
   (`App.jsx:346`). Every tab that dropped at the same moment retries in
   lockstep forever — thundering herd against a single-process FastAPI. A
   ±20% jitter and a reset on a failed *open* attempt are both one-liners.
2. **Outbox flush has no retry trigger beyond `wsStatus`.** The effect depends
   on `[wsStatus]` (`:564`), and a flush that fails mid-way returns with the
   remainder queued (`:547`) — but nothing re-runs it until the *next*
   reconnect transition. If the socket stays up while the HTTP POST keeps
   failing (proxy up, backend route down), those messages sit queued with a
   "sending on reconnect" banner that will never resolve. A bounded retry with
   backoff, or hooking the flush to the existing work poller, closes this.
3. `reconnectAttempt` is unbounded (`App.jsx:383`) — harmless while the
   delay is capped, but worth capping alongside the jitter.

---

## 7. Phase 6 gaps

| Piece | Today | Missing for Phase 6 |
|-------|-------|--------------------|
| **Virtualization** | None. Every loaded message mounts; 24 DOM nodes each; ~24k nodes at 1000 messages | A windowing layer for `#log`, plus moving the stream buffer out of `MessageList` so token arrival stops reconciling the whole list |
| **Run inspector panel** | `WorkHome.jsx` + `WorkDetail` render run detail in a modal (`App.jsx:889`); `WorkRail` shows a collapsed card list | No dedicated side panel, no deep-linkable route, no event-level filtering or time-range control, and it lives in the app chunk that should be split anyway |
| **Approvals inbox** | Approvals surface three ways: a pending count flap on the roster (`Sidebar.tsx:217`), the Work rail's `role="alert"` attention row (`WorkRail.jsx:159`), and inline resolve in the rail/detail. `loadApprovals` polls at 10 s TTL (`App.jsx:245`) | No single inbox view; no cross-channel digest; no approve-all; no keyboard path to resolve without opening a card; approval state is fetched, not pushed, so it can lag the socket |
| **Search** | Roster filter (`Sidebar.tsx:138`), file filter (`App.jsx:1066`), roster-grid filter (`App.jsx:1106`) — all local, per-view. Command palette filters commands only. `searchOpen` state exists at `App.jsx:56` and is **never read or set** | No message search at all. The dead `searchOpen` state is a stub someone started. Backend needs an index; frontend needs a debounced query surface |
| **Code-splitting** | Zero. One 169 kB app chunk carries all views; `manualChunks` sends all of `node_modules` to one 258 kB `vendor` blob | Split Work/Computer/Knowledge/ai-support out of the initial chunk; split `lucide-react` from React; see §1 for the ranked list |
| **PWA manifest** | None. No `<link rel="manifest">` in `index.html`, no `frontend/public/` directory, no service worker, no `theme-color`-linked icon set (a `<meta name="theme-color">` exists but nothing consumes it) | Manifest, icons, service worker with a precache + runtime cache for the app shell. Note the tradeoff: a SW caching `/api` responses would fight the `CACHE_TTL` layer in `lib/cache.js` — cache the shell only |
| **Bundle budget in CI** | `bun run build` runs and only its exit code is checked (`.github/workflows/ci.yml:35`) | Parse emitted sizes and fail over a threshold. Suggested first gate, from the measured baseline plus headroom: **≤ 170 kB gzip total JS**, and **≤ 260 kB raw per chunk**. Vendor's 81 kB gzip is the line to watch as dependencies land |

### Suggested order

1. Fix the two `aria-expanded`/`combobox` findings and the contrast values —
   small, self-contained, and they become harder to retrofit once 6.2/6.3 add
   surfaces.
2. Add the bundle budget to CI now, so 6.2's inspector work is measured from
   the start rather than after.
3. Split the inspector/computer/knowledge surfaces out of the app chunk as part
   of building them, not after.
4. Virtualize `#log` before the run inspector ships — a long trace thread
   rendered next to an inspector panel is where the unmounted-everything cost
   stops being theoretical.
5. Then the inspector panel, the approvals inbox, and message search.