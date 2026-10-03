# FAQ

Honest answers, grounded in the code as it stands. Where the answer is
"it depends, here is the tradeoff", that is what it says.
`AGENTS.md` and the code are the contract; if this file disagrees with
either, trust them and file the drift as a bug.

## Is it safe to expose Swarm to the internet?

Not as it ships. A default instance is a private-network service, and the
gap is bigger than "add TLS": **any client that can reach the port can
register a handle** (`POST /api/register` has no rate limit and no
invite), and a registered handle can then list channels, read every
message, and post (`db.can_view_channel` only restricts `people` DMs, so
rooms are open to every authenticated member). Treat the port as
trusted-network-only unless you put authentication, TLS, and access
control in front of it.

There is also a code-execution surface: host-system tools
(`backend/tools/system.py`) run shell commands as the backend's OS user
with the full host PATH, bounded only by a working-directory default, a
regex deny-list for a handful of destructive patterns, and a 60s timeout.
That is not a sandbox. `SWARM_SYSTEM=0` turns those tools off and is the
single most important setting for an exposed deployment. The minimum
hardening set:

- terminate TLS in front of port `8000`; Swarm does not do TLS itself
- set a strong `SWARM_SECRET` (see the key-storage question below — the
  default is a public literal)
- set an admin password on the first registration, or the first stranger
  to reach the port can claim admin
- restrict `SWARM_ALLOWED_ORIGINS` and keep the `X-Swarm-Client: web`
  marker working
- set `SWARM_SYSTEM=0` unless you specifically need host access, and
  accept that anyone who reaches the port can use whatever you enabled

`SECURITY.md` says the same thing. A hardened public-demo mode is
planned work, not shipped — see [ROADMAP.md](../ROADMAP.md).

## How is Swarm different from the alternatives?

**Slack / Discord bots.** Those bolt agents onto a chat product someone
else hosts and bills for. Swarm owns the workspace: the channel list, the
message table, the bot identities, and the audit export are all in your
own SQLite file. The tradeoff you take on is that you operate it —
upgrades, backups, TLS, and "the group chat is down" become your job.

**OpenAI Swarm (and other multi-agent frameworks).** Those are Python
libraries: you write the orchestration loop, the state machine, and the
UI, then host it somewhere. Swarm ships that whole layer as a product —
named bots with jobs, `@handoff`, `delegate_task`, 1:1 DMs, durable
workflow graphs with approval pauses, and a WebSocket UI where humans and
agents share one transcript. If you wanted the framework and enjoy
building the surrounding product, Swarm is the wrong starting point.

**CrewAI / AutoGen / LangGraph.** Same family of difference: those are
orchestration runtimes you embed, aimed at pipelines and graphs. Swarm is
a chat-native workspace where orchestration is a visible conversation —
tool calls, handoffs, and approvals land in the channel as audit lines
instead of in a log you have to go read. LangGraph in particular has real
durable-execution machinery that Swarm's workflow layer does not try to
match; Swarm's version is simple enough to read in one sitting.

**Cloud agent products (ChatGPT agent, xAI Grok Bot, etc.).** Those are
hosted. Swarm's differentiator is self-hosting and BYO-everything: your
model provider, your machine, your data. In exchange you get no managed
guarantees, no scale story, and no vendor support.

What Swarm is *not* trying to be is an enterprise chat replacement. There
is no cryptographic event signing, no multi-tenant SaaS, no per-agent
container isolation.

## Can I run Swarm fully offline with Ollama?

Yes, for the core loop. Use the **Custom (OpenAI-compatible)** provider:
point `SWARM_OPENAI_COMPAT_BASE_URL` at your Ollama `/v1` (the default is
already `http://127.0.0.1:11434/v1`) and connect the provider in
Command Center → AI providers with any placeholder key — the field is
validated to be at least 8 characters, and Ollama ignores it. The model
id must match what your server actually serves (`ollama list`), because
Swarm sends it through unchanged. For zero cloud dependencies, leave
`GROQ_API_KEY` and the rest unset: `custom` is the only provider with
credentials, so it is the only one tried.

Four caveats:

- **`custom` is last in the fallback order** (priority 90 versus Groq at
  1). If you also have a cloud key configured, the cloud provider is
  tried first and `custom` only runs when the others fail. Unset the
  cloud keys if you want local-only.
- **Some tools need the internet regardless of your model choice**:
  `fetch_url`, `exa_search`, `tavily_search`, `firecrawl_scrape`, and the
  browser tools all reach out. They fail with a message instead of
  crashing, but they do not work air-gapped.
- **Langfuse tracing is opt-in.** With no keys it is a no-op and nothing
  is sent anywhere.
- **Local models are weaker at tool calling.** Every agent reply path is
  tool-driven, so a small local model will produce fewer, sloppier tool
  calls than a hosted one. That is a model-quality fact, not a Swarm bug.

`SWARM_DEMO=1` is a third path: deterministic mock replies and a seeded
`#general` thread with no model at all, which is what the offline test
suite uses.

## How are my provider API keys stored?

Two paths, and they behave differently.

**Environment variables** (`GROQ_API_KEY`, `ANTHROPIC_API_KEY`,
`SWARM_OPENAI_COMPAT_API_KEY`, …) are read at request time from the
process environment, which `backend/__init__.py` populates from the
project-root `.env` and then `backend/.env` without overriding real
process vars. Nothing is written to the database.

**Keys connected through the UI** are sealed before they touch SQLite.
`backend/ai_support/store.py` derives a key with
`SHA-256(SWARM_SECRET)` and XORs it across the plaintext, then
base64url-encodes the result; `backend/db.py` also stores a `key_hint` of
the last four characters. Be clear-eyed about what that is: **it is
keyed obfuscation, not authenticated encryption.** There is no nonce and
no AEAD, so anyone who has both the database file and `SWARM_SECRET` can
recover the plaintext key with a few lines of Python. It stops a key from
sitting in the database in the clear and stops casual log/backup leakage;
it is not a defense against an attacker who already has your data volume.

Three practical consequences:

- **`SWARM_SECRET` is the master key, and it has a public default**
  (`swarm-local-dev-secret`). Set it explicitly. If you change or lose
  it, previously stored keys are unrecoverable — reconnect the provider.
- **Keep `SWARM_SECRET` out of the same backup as the database**, and
  rotate provider keys if it ever leaks.
- **No API ever returns a secret.** Only `provider_id`, `model`,
  `connected_at`, and `key_hint` come back from `/api/status`,
  `/api/ai-support/*`, and `/api/v2/providers`;
  `tests/test_rakazo_parity.py` guards that. OAuth access and refresh
  tokens go through the same seal, and refresh when the provider exposes
  a token endpoint plus client credentials in the environment.

Connector keys (Composio, Exa, Tavily, Firecrawl) use the same store, and
are workspace-global too — see the roadmap item on per-user credentials.

## How do I add custom tools?

Three levels, in increasing order of power.

**1. A custom tool from the UI (no code).** Command Center → Tools, or
`POST /api/tools/custom` as an admin. Name, description, a JSON
`parameters` schema, and one of three handlers: `template` (format a
string with the call arguments), `http_get` (format a URL with the
arguments and fetch it), or `echo` (return the arguments as JSON).
`POST /api/plugins/reload`, or any change to a tool, rebuilds the
registry; agents see the tool on their next turn if it is in their tool
list.

**2. A plugin (a manifest plus optional Python).** Create
`plugins/<slug>/manifest.json` declaring tools under the `tools` array;
they are exposed to agents as `plugin:<slug>:<tool_name>`. A tool's
`handler` can be `template`, `http_get`, `shell`, or `python`. The
`python` handler loads `<plugin_dir>/<module>.py` with `importlib` and
calls the named function, passing the model's arguments plus
`agent_name` and `channel_id` (intersected with the function's
signature). Return a string or any JSON-serialisable value; the result is
truncated at 8000 characters and errors come back as a
`(plugin python error: …)` message rather than a 500. `plugins/composio/`
is the worked example.

**3. A builtin.** Adding to `BUILTIN_SCHEMAS` in
`backend/tools/registry.py` and dispatching it in `_exec_builtin` is the
highest-leverage and highest-blast-radius option, because builtins are
granted to agents by default. Read `AGENTS.md` §6 on tool budgets and
§2 on provider-neutrality first, and add a test.

Two things to know before you write a `shell` or `python` plugin handler:
they execute as the backend's OS user with no isolation, and a plugin is
trusted code that lives in the repository. `plugins/time-helper/` is a
minimal template-shaped example; `plugins/composio/` is the one that talks
to a network API.

Bots provisioned by `create_agent` get a deliberately reduced default tool
set (no bot-spawning, no host-system tools) unless the caller explicitly
asks for `system_*` tools — keep that in mind if you are debugging why a
newly spawned bot cannot see the tool you just wrote.
