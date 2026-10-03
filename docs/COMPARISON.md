# Comparison

Swarm is a **product**: a self-hosted workspace with a web UI, channels,
persistent named bots, and an in-channel audit trail. Everything in the table
below is either a **library/framework** you import into your own code, or a
**chat platform** you extend with a bot. Those are different categories, and
the table says so rather than pretending they compete on the same axis.

**How to read this.** Every competitor claim below was checked against the
project's own documentation or repository on the date in the Sources section
and is cited. Anything I could not verify is marked **unverified as of that
date**. Swarm's own claims are all traceable in this repository — see
"Verifying the Swarm column" at the end. There are no benchmarks in this
document, because Swarm has no published benchmarks and neither do most of
these projects; a comparison built on invented numbers would be worthless.

## What each project genuinely does well

Credit where it is due, and it is substantial:

- **OpenAI Swarm** — the clearest minimal expression of agent handoffs.
  Two primitives (`Agent`, handoffs) and a `client.run()` loop you can read in
  one sitting. It is MIT-licensed, runs almost entirely on the client, and
  keeps no state between calls, which makes it genuinely easy to learn from.
- **CrewAI** — the most complete opinionated framework of the group. Crews
  (role-playing agents with goals and tools) plus Flows (event-driven stateful
  orchestration with persistence, branching, and loops) is a real production
  architecture, not a demo. Its current `Memory` class does LLM-scoped
  analysis on write and composite semantic + recency + importance ranking on
  recall — genuinely more sophisticated memory than Swarm's.
- **AutoGen** — the deepest agent-to-agent conversation support of the group,
  including `UserProxyAgent` as a first-class built-in for human feedback
  mid-run, rich termination conditions, and now `GraphFlow`/`DiGraphBuilder`
  for sequential, parallel, conditional, and looping multi-agent workflows.
  Python and .NET, local and distributed runtime. `microsoft/autogen` shows
  ~61k stars and ~3.8k commits as observed.
- **LangGraph** — the strongest durability story. First-class checkpointing
  with per-superstep state snapshots, a separate cross-thread store, time
  travel / forking, and interrupts whose four decision types (`approve`,
  `edit`, `reject`, `respond`) are built into the execution model rather than
  bolted on. It is the reference implementation for "the agent paused, a human
  decided, the graph resumed exactly where it was."
- **Slack / Discord bots** — they inherit an entire battle-tested chat
  product for free: user management, permissions, channels, threads,
  search, mobile/desktop clients, compliance, and a global audience. A Slack
  bot reaches people who will never log into your self-hosted workspace.

## Position table

Rows are capabilities, not a score. "Swarm" column is what the code in this
repository does today.

| | Swarm | Slack / Discord bot | OpenAI Swarm | CrewAI | AutoGen | LangGraph |
| --- | --- | --- | --- | --- | --- | --- |
| **Category** | Product (server + UI + data) | Platform you extend | Library (educational) | Framework | Framework | Framework / runtime |
| **You get a UI** | Yes, React build served by FastAPI | Yes, Slack/Discord's | No | AMP (separate commercial platform) | Samples only (FastAPI, ChainLit, Streamlit) | No (LangGraph Agent Server is separate) |
| **Persistent chat history** | SQLite `messages`, channels, threads, reactions | Platform-provided | No — stateless between calls | Via your own storage / Flows state | Via your own storage | Checkpointer (thread-scoped) + store (cross-thread) |
| **Agents as first-class channel members** | Yes — agents post as `author_kind='agent'` rows in the same `messages` table | Yes, as bot users | No (agent-to-agent only) | No | No (agent-to-agent) | No (node-to-node) |
| **Tool calls visible in the channel** | Yes — each call persists a `system` row (`{agent} ran: {tool}({args}) -> {result}`) | No (you build it) | No | No | No | No |
| **Multi-bot trigger** | `@mention`, whole bot 1:1 DMs, group roster, `@team-id`, routine ticks | Only what you implement | Agent-to-agent handoff via function calls | Delegation inside a crew | Conversation turns / handoff messages | Explicit graph edges |
| **Handoff depth caps** | `HANDOFF_DEPTH = 3`, `DELEGATE_DEPTH_CAP = 2` | n/a | No cap in the primitive | Crew composition | Team-defined | Graph-defined |
| **Human approval in the loop** | Agent calls `request_approval` → `approvals` row → in-channel approve/deny message the bot then reads and follows | Build it (Block Kit, modals, buttons) | Build it | Flow-level HITL | `UserProxyAgent` in the team | `interrupt()` + 4 decision types, checkpoint-persisted |
| **Durable workflow runs** | v2 `workflows`/`runs`/`run_events`, approval nodes, artifacts, restart recovery | No | No | Flows, event-driven | GraphFlow (experimental) | Checkpoint + time travel (strongest) |
| **Crash recovery mid-run** | `recoverable_runs()` at startup; `work.recover_interrupted()` settles stale sessions | Platform-dependent | No | Flow persistence | Runtime-dependent | Yes, by design |
| **BYO model provider, no vendor lock** | 16-entry priority catalog + generic `custom` OpenAI-compatible endpoint (Ollama/LM Studio/vLLM via env) | n/a | Chat Completions API only | Provider-agnostic | Provider-agnostic | Provider-agnostic |
| **Credentials sealed at rest** | XOR-over-`sha256(SWARM_SECRET)` in `ai_providers.secret`; only `key_hint` ever returned | Platform secrets manager | You pass keys in code | You pass keys in code | You pass keys in code | You pass keys in code |
| **Streaming to a live UI** | `agent_stream_start` / `agent_token` frames over WS | Platform-provided | Has streaming helpers | Framework-level | Has streaming | Has streaming |
| **Sandbox / host tools** | Sandboxed shell (cwd + timeout + minimal PATH) and `SWARM_SYSTEM_ROOT`-bound host tools — **not** container isolation | n/a | No | No | No | No |
| **Self-hosted, one process** | Yes — FastAPI + SQLite on a volume | No | Yes | Yes | Yes | Yes |
| **Delivery / deployment story** | Docker Compose, `GET /health` | Marketplace install | `pip install` | `pip install` (+ AMP for hosted) | `pip install` / `.NET` | `pip install` (+ Agent Server) |
| **Multi-provider OAuth** | PKCE flow, refresh tokens sealed | n/a | No | No | No | No |

## Where each genuinely beats Swarm

Being straight about this matters more than winning the table.

- **Scaling and reliability are the frameworks' job, not Swarm's.** LangGraph
  checkpointing gives time travel, per-superstep replay, and forking from any
  prior state. Swarm's durability is coarser: a `run_events` log plus
  step-level skip-on-restart. For a long, branching, multi-hour job that you
  need to inspect, fork, or resume mid-node, LangGraph is ahead.
- **Memory sophistication.** CrewAI's current `Memory` scores recall on
  semantic similarity + recency + importance and does LLM analysis on write.
  Swarm's memory is a `notes`/`summary` table plus an FTS5 knowledge base with
  a channel-scope boost, injected under a character budget. It works and it is
  auditable, but it is not a research pipeline. Swarm deliberately gates this
  work (`docs/PRODUCT.md` puts vector/semantic history out of scope until
  keyword search fails in daily use) — that is a scope choice, not a technical
  win.
- **Agent-to-agent conversation mechanics.** AutoGen's `UserProxyAgent`,
  termination conditions, message types, and streaming team runs are a
  substantially deeper toolkit for coordinating agents than Swarm's
  `@mention` + depth-cap handoff. Swarm's is deliberately legible: a bot
  replies with `@other` and the trigger logic picks it up.
- **Ecosystem reach.** Slack and Discord give you an existing audience,
  enterprise identity, compliance, and native notifications. A self-hosted
  workspace has to earn all of that.
- **Cross-language support.** AutoGen ships Python and .NET; Swarm is Python
  backend plus a React frontend.
- **Loop and conditional execution.** LangGraph and AutoGen's GraphFlow
  handle cycles and branching natively. Swarm's v2 graph runner supports
  `input`, `agent`, `approval`, `conditional` (`always` / `has_output`),
  `parallel`, and `output` node types — no unbounded loops.
- **Interrupt ergonomics.** LangGraph's `edit` decision (change tool arguments
  before execution) has no Swarm equivalent. Swarm's approval is binary
  approve/deny, and the verdict re-enters chat as a human message the bot
  reads — arguably more transparent, but strictly less capable.

## What Swarm does not do

Honest gaps, from this repository's code:

- **No container or network isolation.** The shell tool is a working
  directory plus a timeout plus a minimal `PATH`. Host tools are bound to
  `SWARM_SYSTEM_ROOT`. Never describe it as sandboxing in the isolation sense.
- **No semantic or vector history.** Search is `LIKE`-based
  (`db.search_history`, `db.search_workspace`) with FTS5 only for the
  knowledge base.
- **No native multi-tenancy, SSO, or enterprise identity.** Auth is one
  composite token per handle; the first registered user becomes `admin`.
- **No cryptographic event signing or git-native workflows.** For that, the
  right answer is a different product.
- **No unbounded loops in the workflow runner**, as noted above.
- **No credential vault.** `ai_providers.secret` is sealed with a repeating-key
  XOR keyed by `sha256(SWARM_SECRET)`, and `SWARM_SECRET` has a hardcoded
  development default (`swarm-local-dev-secret`). That is obfuscation of a
  value in your own database, not a managed secret store.
- **No published benchmarks.** `backend/evals.py` has a scoring harness
  (`score_run` computes success, verified success, retries, human effort,
  tool calls) and `/api/v2/evals/*` exposes it, but there are no committed
  result sets. Any "Swarm scores X" claim is not currently supportable.
- **Single-writer SQLite, single process.** Correct for a small team; not a
  multi-region deployment story.
- **`policy.evaluate` and `routing.route` are advisory.** They are reachable
  from `/api/v2/*` but do not gate the chat path. The only approval gate on a
  chat turn is the agent choosing to call `request_approval` — there is no
  automatic "this tool is risky, block it" layer on that path.

## How to choose

- You want agents that are **teammates in a chat your team already uses**,
  with an audit trail nobody has to reconstruct → Swarm.
- You are building a **product** and need agent orchestration as an
  implementation detail → LangGraph (best durability) or CrewAI (most
  opinionated end-to-end framework).
- You are building **deep multi-agent conversation** research or tooling →
  AutoGen.
- You want to **learn** agent handoffs by reading a small repo → OpenAI Swarm.
- Your users are **already in Slack or Discord** and adoption beats audit
  purity → write the bot. Swarm's own `docs/PRODUCT.md` takes this position
  against the Slack-hosted alternatives it lists.

## Sources

Verified on the date these pages were retrieved. Documentation URLs are the
canonical ones; repo facts (star counts, commit counts) are as displayed then
and will drift.

- OpenAI Swarm — <https://github.com/openai/swarm>
  (self-described as "experimental, educational"; README states it is
  replaced by the OpenAI Agents SDK and recommends migrating for production;
  states `client.run()` "saves no state between calls" and that Swarm "runs
  (almost) entirely on the client"; MIT license; ~22k stars, 29 commits.)
- CrewAI — <https://docs.crewai.com/en/introduction>,
  <https://docs.crewai.com/en/concepts/memory>,
  <https://docs-platform.crewai.com/platform/en/introduction>
  (Crews + Flows architecture, stateful event-driven Flows, role-playing
  agents with tools; the unified `Memory` class with composite
  semantic + recency + importance recall scoring; AMP is the separate
  deployment/monitoring platform.)
- AutoGen — <https://github.com/microsoft/autogen>,
  <https://microsoft.github.io/autogen/stable/user-guide/agentchat-user-guide/graph-flow.html>,
  <https://microsoft.github.io/autogen/stable/user-guide/agentchat-user-guide/tutorial/human-in-the-loop.html>
  (Core / AgentChat / Extensions layers, message passing and local +
  distributed runtime, Python and .NET; `GraphFlow` follows a `DiGraph` and
  supports sequential, parallel, conditional, and looping behaviour, and is
  labelled an experimental feature; `UserProxyAgent` provides human feedback
  during a run; ~61k stars, ~3.8k commits.)
- LangGraph — <https://docs.langchain.com/oss/python/langgraph/durable-execution>
  (page served as "Persistence"), <https://docs.langchain.com/oss/python/langchain/human-in-the-loop>,
  <https://www.langchain.com/langgraph>
  (checkpointers persist thread-scoped graph state and are used for
  conversation continuity, human-in-the-loop, time travel, and fault
  tolerance; stores persist application-defined data across threads; the
  four interrupt decision types `approve` / `edit` / `reject` / `respond`;
  MIT licensed.)
- Slack — <https://docs.slack.dev/apis/events-api>
  (Events API delivers subscribed events over Socket Mode or a public HTTP
  endpoint; OAuth scopes bound what an app can subscribe to and see.)
- Discord — <https://docs.discord.com/developers/events/gateway>,
  <https://docs.discord.com/developers/topics/rate-limits>
  (Gateway WebSocket event delivery with intents and session/identify
  limits; per-route and global rate limits applied per bot or user.)

## Verifying the Swarm column

Every Swarm claim in this document maps to code in this repository:

| Claim | Where to check |
| --- | --- |
| Tool calls persist as in-channel `system` rows | `backend/main.py::_run_agent` → `persist_tools` → `db.add_message(..., "system")` |
| Agent replies persist as `author_kind='agent'` with the model | `backend/main.py::_run_agent` → `db.add_message(channel_id, name, result["reply"], "agent", model=...)` |
| Streaming is WS-only until the turn ends; cutoff markers | `backend/agent.py::_complete_stream` (`CUTOFF_MARKER`, `CUTOFF_NOTE`), `backend/main.py::_run_agent` cancellation branch |
| Handoff depth 3 / delegation depth 2 | `HANDOFF_DEPTH` in `backend/main.py`, `DELEGATE_DEPTH_CAP` in `backend/agent.py` |
| 16-provider catalog + generic `custom` endpoint | `backend/ai_support/providers.py`, `backend/ai_support/resolver.py` |
| Sealed credentials, key hints only | `backend/ai_support/store.py::_seal`, `backend/db.py::upsert_ai_provider` |
| Approval row + in-channel approve/deny message | `backend/agent.py::_run_approval_tool`, `backend/main.py::api_resolve_approval` |
| Durable runs, approval nodes, artifacts, recovery | `backend/v2.py` (`execute_run`, `recoverable_runs`, `start_run`) |
| No policy gate on the chat path | `backend/main.py::_run_agent` calls neither `policy.evaluate` nor `routing.route` |
| No vector history | `backend/db.py::search_history`, `search_workspace` (SQL `LIKE`); `backend/knowledge.py` is FTS5 with `LIKE` fallback |
| No isolation boundary | `backend/agent.py::_run_shell_tool`, `_shell_env` comments |
| Default `SWARM_SECRET` | `backend/ai_support/store.py` module-level `_SECRET` |

Claims about competitor *relative* strength (durability depth, memory
ranking, conversation mechanics, ecosystem reach) are judgments grounded in
the cited documentation. Where a judgment could reasonably go the other way,
this document says so in "Where each genuinely beats Swarm" rather than
resolving it in Swarm's favour.