# Roadmap

Five things. Each one names what it is, why it matters, and where the
code actually stands today. Status is one of **planned**, **in
progress**, or **shipped** — and "shipped" only appears where a test or a
route proves it.

`docs/PRODUCT.md` gates speculative work: build it when the simpler
thing has failed in daily use. These are the known sharp edges, in the
order they start to hurt.

---

## 1. Per-user provider credentials

**What:** today a connected provider is workspace-global. One Groq key,
one OpenRouter key, one Composio key for every human and every bot —
`ai_providers` is keyed by `provider_id` alone, and any admin's connect
call overwrites it. Resolution walks a fixed priority order across all
connected providers with no notion of who asked.

**Why:** it blocks the two things a small team actually wants. Nobody
wants to hand a teammate their personal API key just so a bot can answer
a question, and per-seat billing or spend limits are impossible when
everyone shares one key. It is also the biggest obstacle to treating a
Swarm instance as anything other than a strictly private tool.

**Status: planned.** No per-user column on `ai_providers`, no user id in
the provider resolve path, no scoping on the connect routes. The seal
itself (`SWARM_SECRET`) is already the right primitive, so this is
mostly schema and resolution work: an additive column or table, a
user-scoped lookup with workspace fallback, and a decision about
whether a bot inherits its owner's credentials or a named bot's.

---

## 2. Container-isolated sandbox

**What:** run agent commands inside a throwaway container instead of on
the host. Today the sandbox is a working directory plus a timeout
(`SWARM_SANDBOX_DIR`, 30s in `backend/tools/computer.py`) and host tools
are a separate, broader surface (`SWARM_SYSTEM_ROOT`, full host PATH,
60s). The deny-list is a regex over a handful of destructive patterns.

**Why:** it is the honest answer to the "is it safe to expose this"
question, and the reason that question currently has a long list of
caveats. It is also what makes it defensible to let more than one team
share an instance, and what unblocks running agents that handle untrusted
input. `docs/PRODUCT.md` lists container-per-agent isolation as
deliberately out of scope until something forces it; a container per
workspace run is the cheapest version that would force it.

**Status: planned.** There is no container runtime anywhere in the tree.
`backend/computer_providers.py` provides `local | none | fake` — the
interface is the right shape (a container backend would slot in beside
`local`), but no provider beyond `local` is implemented. The Docker
image runs the whole app as an unprivileged user with `SWARM_SYSTEM_ROOT=/app`;
that is process-level hygiene, not agent isolation.

---

## 3. Semantic knowledge search

**What:** embeddings-based retrieval over the knowledge base and channel
history, behind the interfaces that already exist.

**Why:** keyword search is the current honest limit and it is visible in
use. `backend/knowledge.py` is FTS5 (with a `LIKE` fallback when SQLite
lacks FTS5) and quotes terms into an `OR` query — there is no stemming,
no ranking beyond `rank` and recency, and a query for a concept you never
wrote down returns nothing. `search_channel_history` is a substring
search. The context builder is budget-aware and trims, so better
retrieval is exactly the lever that makes long-lived bots feel better.

**Status: planned.** No embedding client, no vector store, no migration.
The retrieval seams are already in place — `knowledge.search_docs` takes
`owners`/`channel_id`/`limit`, `backend/context.py` assembles the budgeted
package, and `knowledge_search` is already an agent tool — so the work is
a new retrieval implementation plus a decision about where vectors live
(SQLite has no native vector type, so this means a table, an extension,
or a sidecar). `docs/PRODUCT.md` gates this explicitly: keyword search
stands until it fails in daily use.

---

## 4. Hardened public-demo mode

**What:** a mode where a Swarm instance can be safely reachable by
strangers — registration restricted, host tools off, storage and
computer surfaces disabled or read-only, and a visible banner saying what
is and isn't real.

**Why:** it turns "here is a repo, clone it" into "here is a link", which
is how most people will meet this. Today the honest posture is
trusted-network-only: `POST /api/register` is open (first handle becomes
admin), rooms are readable by any authenticated member
(`db.can_view_channel` only gates `people` DMs), and `SWARM_SYSTEM`
controls whether anyone reaching the port gets code execution. A public
demo that ships without closing those is a liability for the operator.

**Status: planned.** `SWARM_DEMO=1` exists and does something different —
deterministic mock replies plus a seeded `#general` thread, for local
development and the offline test suite. It does not restrict
registration, disable tools, or bound the data. `warn_if_exposed` in
`backend/tools/system.py` logs a loud warning when host tools are enabled
on a non-loopback bind, which is the right direction and a log line, not
a mode.

---

## 5. MCP client and server support

**What:** speak the Model Context Protocol in both directions — let
agents call tools exposed by external MCP servers, and let external MCP
clients read a Swarm channel and trigger bots.

**Why:** the tool registry is already the right seam (a tool is a schema
plus a handler plus a name in `ToolRegistry`), MCP servers are the
fastest-moving source of new tools, and a Swarm channel is a legible
audit log that an external agent could read with the same guarantees the
UI gets.

**Status: planned, with groundwork in place.** There is **no MCP code in
the tree today** — nothing in `backend/`, `frontend/src/`, or
`requirements.txt` references MCP, so treat this as unimplemented. What
does exist is the shape it would plug into: `ToolRegistry._load_plugins`
already scans `plugins/*/manifest.json` and keys tools as
`plugin:<slug>:<name>`, and `_exec_plugin` already dispatches `python`
handlers, so an MCP-backed plugin is a new manifest plus a handler that
speaks the protocol. The design questions worth settling first: whether
remote MCP servers are allowed at all (they are an outbound-credential
and SSRF surface), and whether the audit trail stays as strict for
plugin- and MCP-sourced tool calls as for builtins.

---

## Not on this list

Deliberately out of scope per `docs/PRODUCT.md`: Nostr signing, git
hosting, multi-tenant SaaS, cloud VM / remote desktop, voice huddles,
canvases. `OpenAI Swarm`-style library ergonomics are also not coming —
Swarm is a workspace, not a framework you embed.
