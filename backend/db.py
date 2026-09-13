"""
swarm.db — thin async wrapper over SQLite.

No ORM. If this needs to outgrow SQLite, that's a good problem to have
and a different file. Schema growth past CREATE TABLE IF NOT EXISTS
goes through ensure_schema() (PRAGMA + ALTER TABLE).
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import time
from pathlib import Path
from typing import Any

import aiosqlite

from .models import (
    ALLOWED_TOOLS,
    DEFAULT_GROQ_MODEL,
    DEFAULT_JOB,
    DEFAULT_TOOLS,
    GROQ_MODEL_ALIASES,
    LEDGER_TOOLS,
    USER_ROLES,
    pretty_name,
    resolve_groq_model,
)

DB_PATH = Path(os.environ.get("SWARM_DB_PATH", str(Path(__file__).parent / "swarm.db")))

_DEFAULT_TOOLS_JSON = json.dumps(DEFAULT_TOOLS)
_LEDGER_TOOLS_JSON = json.dumps(LEDGER_TOOLS)

SCHEMA = f"""
CREATE TABLE IF NOT EXISTS channels (
    id          TEXT PRIMARY KEY,
    name        TEXT UNIQUE NOT NULL,
    topic       TEXT DEFAULT '',
    created_at  REAL NOT NULL,
    kind        TEXT NOT NULL DEFAULT 'room',
    owner_agent TEXT
);

CREATE TABLE IF NOT EXISTS messages (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    channel_id  TEXT NOT NULL REFERENCES channels(id),
    parent_id   INTEGER REFERENCES messages(id),
    author      TEXT NOT NULL,
    author_kind TEXT NOT NULL DEFAULT 'human',   -- 'human' | 'agent' | 'system'
    body        TEXT NOT NULL,
    created_at  REAL NOT NULL,
    model       TEXT                             -- model id that produced an agent reply
);

CREATE INDEX IF NOT EXISTS idx_messages_channel ON messages(channel_id, created_at);
CREATE INDEX IF NOT EXISTS idx_messages_parent ON messages(parent_id);

CREATE TABLE IF NOT EXISTS users (
    handle      TEXT PRIMARY KEY,
    token_hash  TEXT NOT NULL,
    created_at  REAL NOT NULL,
    role        TEXT NOT NULL DEFAULT 'member',
    password_hash TEXT
);

CREATE TABLE IF NOT EXISTS workspace_meta (
    key         TEXT PRIMARY KEY,
    value       TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS reactions (
    message_id  INTEGER NOT NULL REFERENCES messages(id),
    author      TEXT NOT NULL,
    emoji       TEXT NOT NULL,
    created_at  REAL NOT NULL,
    UNIQUE(message_id, author, emoji)
);

CREATE TABLE IF NOT EXISTS agents (
    name            TEXT PRIMARY KEY,
    system_prompt   TEXT NOT NULL,
    model           TEXT NOT NULL,
    channel_scope   TEXT,   -- NULL = all channels, else a specific channel_id
    created_at      REAL NOT NULL,
    history_window  INTEGER NOT NULL DEFAULT 12,
    max_tool_calls  INTEGER NOT NULL DEFAULT 6,
    tools           TEXT NOT NULL DEFAULT '{_DEFAULT_TOOLS_JSON}',
    job             TEXT NOT NULL DEFAULT '{DEFAULT_JOB}',
    status          TEXT NOT NULL DEFAULT 'idle',
    display_name    TEXT NOT NULL DEFAULT '',
    avatar          TEXT NOT NULL DEFAULT '',
    archived_at     REAL,
    tools_locked    INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS channel_members (
    channel_id  TEXT NOT NULL REFERENCES channels(id),
    agent_name  TEXT NOT NULL,
    sort_order  INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (channel_id, agent_name)
);

CREATE TABLE IF NOT EXISTS agent_memory (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    agent_name  TEXT NOT NULL,
    channel_id  TEXT,
    kind        TEXT NOT NULL,
    body        TEXT NOT NULL,
    created_at  REAL NOT NULL,
    updated_at  REAL NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_agent_memory_agent ON agent_memory(agent_name, created_at);
CREATE INDEX IF NOT EXISTS idx_agent_memory_kind ON agent_memory(agent_name, kind, channel_id);
CREATE INDEX IF NOT EXISTS idx_reactions_message ON reactions(message_id);

CREATE TABLE IF NOT EXISTS skills (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT UNIQUE NOT NULL,
    body        TEXT NOT NULL,
    created_at  REAL NOT NULL,
    updated_at  REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS routines (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    agent_name        TEXT NOT NULL,
    title             TEXT NOT NULL,
    instructions      TEXT NOT NULL,
    interval_minutes  INTEGER NOT NULL,
    enabled           INTEGER NOT NULL DEFAULT 1,
    last_run_at       REAL,
    next_run_at       REAL NOT NULL,
    created_at        REAL NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_routines_next ON routines(enabled, next_run_at);

CREATE TABLE IF NOT EXISTS routine_runs (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    routine_id  INTEGER NOT NULL REFERENCES routines(id),
    started_at  REAL NOT NULL,
    finished_at REAL,
    status      TEXT NOT NULL,
    excerpt     TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS approvals (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    agent_name  TEXT NOT NULL,
    channel_id  TEXT NOT NULL,
    action      TEXT NOT NULL,
    detail      TEXT NOT NULL DEFAULT '',
    status      TEXT NOT NULL DEFAULT 'pending',
    created_at  REAL NOT NULL,
    resolved_at REAL
);

CREATE INDEX IF NOT EXISTS idx_approvals_pending ON approvals(status, channel_id);

CREATE TABLE IF NOT EXISTS custom_tools (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    name            TEXT UNIQUE NOT NULL,
    description     TEXT NOT NULL DEFAULT '',
    parameters      TEXT NOT NULL DEFAULT '{{}}',
    handler_type    TEXT NOT NULL DEFAULT 'template',
    handler_config  TEXT NOT NULL DEFAULT '{{}}',
    enabled         INTEGER NOT NULL DEFAULT 1,
    created_at      REAL NOT NULL,
    updated_at      REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS ai_providers (
    provider_id     TEXT PRIMARY KEY,
    secret          TEXT NOT NULL,
    key_hint        TEXT NOT NULL DEFAULT '',
    model           TEXT,
    connected_at    REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS channel_people (
    channel_id  TEXT NOT NULL,
    handle      TEXT NOT NULL,
    PRIMARY KEY (channel_id, handle)
);

CREATE TABLE IF NOT EXISTS agent_teams (
    id           TEXT PRIMARY KEY,
    name         TEXT NOT NULL,
    description  TEXT NOT NULL DEFAULT '',
    created_at   REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS agent_team_members (
    team_id      TEXT NOT NULL,
    agent_name   TEXT NOT NULL,
    sort_order   INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (team_id, agent_name)
);
"""

_DEFAULT_CHANNELS = [
    ("general", "wherever, whatever"),
    ("agents", "the swarm's own channel"),
    ("code", "programs, proofs, and latex"),
]

_CODER_PROMPT = (
    "You are `coder`, a specialist programming teammate — not swarm. "
    "Different rules apply here.\n"
    "1. Put runnable code in fenced markdown blocks with a language tag "
    "(```python, ```js, ```sql, …). Never dump code as plain prose.\n"
    "2. Put mathematics in LaTeX: inline $...$ or display $$...$$.\n"
    "3. Structure every non-trivial reply as LaTeX-style subsections:\n"
    "   \\subsection*{Approach}\n"
    "   \\subsection*{Code}\n"
    "   \\subsection*{Notes}\n"
    "4. Prefer small, complete, runnable examples over pseudocode.\n"
    "5. For repo and host work, use system_ls / system_read / system_write / "
    "system_run on this machine. Use write_workspace / computer_run only for "
    "the isolated sandbox.\n"
    "6. Inspect with shell tools; never invent command output.\n"
    "7. Do not send outreach or change production; call request_approval "
    "first for anything external.\n"
    "8. Keep chatter out. Tradeoffs live in Approach; the program lives in Code."
)

_DEFAULT_AGENTS = [
    (
        "swarm",
        "You are `swarm`, an AI teammate embedded in a chat workspace. "
        "You're in the room like anyone else — terse, direct, no filler, "
        "no 'As an AI...' hedging. Answer the actual question. If you don't "
        "know something, say so in one line and move on. Keep replies short "
        "unless the question genuinely needs length. Do not call tools for "
        "greetings or small talk. Use remember for facts that should stick "
        "across turns. For code, proofs, or LaTeX, defer to @coder.",
        DEFAULT_GROQ_MODEL,
        None,
        12,
        6,
        DEFAULT_TOOLS,
        "Generalist",
    ),
    (
        "ledger",
        "You are `ledger`. You don't chat, you record. When mentioned, "
        "summarize the decisions and open questions from the recent "
        "channel history in a short bulleted list — nothing else. If "
        "there's nothing decision-shaped in the recent history, say so "
        "in one line. Remember durable decisions with the remember tool.",
        DEFAULT_GROQ_MODEL,
        None,
        12,
        6,
        LEDGER_TOOLS,
        "Decision log",
    ),
    (
        "coder",
        _CODER_PROMPT,
        DEFAULT_GROQ_MODEL,
        None,
        20,
        8,
        DEFAULT_TOOLS,
        "Code",
    ),
]


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


_PBKDF2_ROUNDS = 100_000


def hash_password(password: str) -> str:
    salt = secrets.token_hex(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), _PBKDF2_ROUNDS)
    return f"{salt}${dk.hex()}"


def verify_password(password: str, stored: str | None) -> bool:
    if not password or not stored or "$" not in stored:
        return False
    salt, _, digest = stored.partition("$")
    if not salt or not digest:
        return False
    try:
        dk = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), _PBKDF2_ROUNDS)
    except ValueError:
        return False
    return hmac.compare_digest(dk.hex(), digest)


def parse_tools(raw: Any) -> list[str]:
    if isinstance(raw, list):
        names = raw
    elif isinstance(raw, str) and raw.strip():
        try:
            names = json.loads(raw)
        except json.JSONDecodeError:
            names = list(DEFAULT_TOOLS)
    else:
        names = list(DEFAULT_TOOLS)
    return [n for n in names if n]


def public_agent(row: dict[str, Any]) -> dict[str, Any]:
    out = dict(row)
    out["tools"] = parse_tools(out.get("tools"))
    out["history_window"] = int(out.get("history_window") or 12)
    out["max_tool_calls"] = int(out.get("max_tool_calls") or 6)
    out["job"] = (out.get("job") or DEFAULT_JOB).strip() or DEFAULT_JOB
    out["status"] = out.get("status") or "idle"
    handle = out.get("name") or "bot"
    display = (out.get("display_name") or "").strip()
    out["display_name"] = display or pretty_name(handle)
    out["dm_channel_id"] = dm_channel_id(handle)
    out["archived"] = bool(out.get("archived_at"))
    from .profiles import load_agent_profile, profile_path
    out["profile"] = load_agent_profile(handle, out.get("job"))
    out["profile_path"] = profile_path(handle, out.get("job"))
    return out


def dm_channel_id(agent_name: str) -> str:
    return f"dm-{agent_name}"


async def _ensure_schema(db: aiosqlite.Connection) -> None:
    """Add columns/tables to databases that predate them."""
    cur = await db.execute("PRAGMA table_info(agents)")
    cols = {row[1] for row in await cur.fetchall()}
    if "history_window" not in cols:
        await db.execute(
            "ALTER TABLE agents ADD COLUMN history_window INTEGER NOT NULL DEFAULT 12"
        )
    if "max_tool_calls" not in cols:
        await db.execute(
            "ALTER TABLE agents ADD COLUMN max_tool_calls INTEGER NOT NULL DEFAULT 6"
        )
    if "tools" not in cols:
        await db.execute(
            "ALTER TABLE agents ADD COLUMN tools TEXT NOT NULL DEFAULT "
            f"'{_DEFAULT_TOOLS_JSON}'"
        )
        await db.execute(
            "UPDATE agents SET tools = ? WHERE name = ?",
            (_LEDGER_TOOLS_JSON, "ledger"),
        )
    if "job" not in cols:
        await db.execute(
            f"ALTER TABLE agents ADD COLUMN job TEXT NOT NULL DEFAULT '{DEFAULT_JOB}'"
        )
        await db.execute("UPDATE agents SET job = 'Generalist' WHERE name = 'swarm'")
        await db.execute("UPDATE agents SET job = 'Decision log' WHERE name = 'ledger'")
    if "status" not in cols:
        await db.execute(
            "ALTER TABLE agents ADD COLUMN status TEXT NOT NULL DEFAULT 'idle'"
        )
    if "display_name" not in cols:
        await db.execute(
            "ALTER TABLE agents ADD COLUMN display_name TEXT NOT NULL DEFAULT ''"
        )
        await db.execute(
            "UPDATE agents SET display_name = name WHERE display_name = '' OR display_name IS NULL"
        )
        for handle in ("swarm", "ledger", "coder"):
            await db.execute(
                "UPDATE agents SET display_name = ? WHERE name = ?",
                (pretty_name(handle), handle),
            )
    if "archived_at" not in cols:
        await db.execute("ALTER TABLE agents ADD COLUMN archived_at REAL")
    if "avatar" not in cols:
        await db.execute(
            "ALTER TABLE agents ADD COLUMN avatar TEXT NOT NULL DEFAULT ''"
        )
    if "tools_locked" not in cols:
        await db.execute("ALTER TABLE agents ADD COLUMN tools_locked INTEGER NOT NULL DEFAULT 0")
    await db.execute(
        "CREATE TABLE IF NOT EXISTS channel_members ("
        "channel_id TEXT NOT NULL, agent_name TEXT NOT NULL, "
        "sort_order INTEGER NOT NULL DEFAULT 0, "
        "PRIMARY KEY (channel_id, agent_name))"
    )
    for old, new in GROQ_MODEL_ALIASES.items():
        await db.execute("UPDATE agents SET model = ? WHERE model = ?", (new, old))

    cur = await db.execute("PRAGMA table_info(messages)")
    msg_cols = {row[1] for row in await cur.fetchall()}
    if "model" not in msg_cols:
        await db.execute("ALTER TABLE messages ADD COLUMN model TEXT")

    cur = await db.execute("PRAGMA table_info(channels)")
    ch_cols = {row[1] for row in await cur.fetchall()}
    if "kind" not in ch_cols:
        await db.execute(
            "ALTER TABLE channels ADD COLUMN kind TEXT NOT NULL DEFAULT 'room'"
        )
    if "owner_agent" not in ch_cols:
        await db.execute("ALTER TABLE channels ADD COLUMN owner_agent TEXT")

    for name, topic in _DEFAULT_CHANNELS:
        cur = await db.execute("SELECT 1 FROM channels WHERE id = ?", (name,))
        if await cur.fetchone() is None:
            await db.execute(
                "INSERT INTO channels (id, name, topic, created_at, kind) "
                "VALUES (?, ?, ?, ?, 'room')",
                (name, name, topic, time.time()),
            )

    for name, prompt, model, scope, window, cap, tools, job in _DEFAULT_AGENTS:
        cur = await db.execute("SELECT 1 FROM agents WHERE name = ?", (name,))
        if await cur.fetchone() is None:
            await db.execute(
                "INSERT INTO agents (name, system_prompt, model, channel_scope, "
                "created_at, history_window, max_tool_calls, tools, job, status, display_name) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'idle', ?)",
                (name, prompt, model, scope, time.time(), window, cap, json.dumps(tools), job, pretty_name(name)),
            )

    # The main swarm agent can provision need-based bots; backfill the
    # tool onto pre-existing databases that stored an older tool list.
    cur = await db.execute("SELECT tools FROM agents WHERE name = 'swarm'")
    row = await cur.fetchone()
    if row is not None:
        names = parse_tools(row[0])
        if "create_agent" not in names:
            names.append("create_agent")
            await db.execute(
                "UPDATE agents SET tools = ? WHERE name = 'swarm'",
                (json.dumps(names),),
            )

    cur = await db.execute("SELECT name, job FROM agents")
    agents = await cur.fetchall()
    for name, job in agents:
        dm_id = dm_channel_id(name)
        cur = await db.execute("SELECT 1 FROM channels WHERE id = ?", (dm_id,))
        if await cur.fetchone() is None:
            topic = f"1:1 with {name}" + (f" · {job}" if job else "")
            await db.execute(
                "INSERT INTO channels (id, name, topic, created_at, kind, owner_agent) "
                "VALUES (?, ?, ?, ?, 'dm', ?)",
                (dm_id, dm_id, topic, time.time(), name),
            )

    cur = await db.execute("PRAGMA table_info(users)")
    user_cols = {row[1] for row in await cur.fetchall()}
    if "role" not in user_cols:
        await db.execute(
            "ALTER TABLE users ADD COLUMN role TEXT NOT NULL DEFAULT 'member'"
        )
    if "password_hash" not in user_cols:
        await db.execute("ALTER TABLE users ADD COLUMN password_hash TEXT")
    cur = await db.execute("SELECT handle FROM users WHERE role = 'admin' LIMIT 1")
    if await cur.fetchone() is None:
        cur = await db.execute("SELECT handle FROM users ORDER BY created_at ASC LIMIT 1")
        first = await cur.fetchone()
        if first is not None:
            await db.execute("UPDATE users SET role = 'admin' WHERE handle = ?", (first[0],))
    await db.execute(
        "CREATE TABLE IF NOT EXISTS workspace_meta ("
        "key TEXT PRIMARY KEY, value TEXT NOT NULL)"
    )

    await db.execute(
        "CREATE TABLE IF NOT EXISTS channel_people ("
        "channel_id TEXT NOT NULL, handle TEXT NOT NULL, "
        "PRIMARY KEY (channel_id, handle))"
    )
    await db.execute(
        "CREATE TABLE IF NOT EXISTS agent_teams ("
        "id TEXT PRIMARY KEY, name TEXT NOT NULL, "
        "description TEXT NOT NULL DEFAULT '', created_at REAL NOT NULL)"
    )
    await db.execute(
        "CREATE TABLE IF NOT EXISTS agent_team_members ("
        "team_id TEXT NOT NULL, agent_name TEXT NOT NULL, "
        "sort_order INTEGER NOT NULL DEFAULT 0, "
        "PRIMARY KEY (team_id, agent_name))"
    )
    cur = await db.execute("SELECT 1 FROM agent_teams WHERE id = 'core'")
    if await cur.fetchone() is None:
        await db.execute(
            "INSERT INTO agent_teams (id, name, description, created_at) VALUES (?, ?, ?, ?)",
            ("core", "Core", "Default pod: generalist, decision log, and code.", time.time()),
        )
        for i, agent_name in enumerate(("swarm", "ledger", "coder")):
            await db.execute(
                "INSERT OR IGNORE INTO agent_team_members (team_id, agent_name, sort_order) "
                "VALUES ('core', ?, ?)",
                (agent_name, i),
            )
    await _grant_computer_use_tools(db)
    await _seed_bundled_skills(db)


async def _seed_bundled_skills(db: aiosqlite.Connection) -> None:
    """Insert bundled /commands if missing. Do not overwrite a human-edited body."""
    from .bundled_skills import load_bundled_skills

    now = time.time()
    for name, body in load_bundled_skills():
        await db.execute(
            "INSERT OR IGNORE INTO skills (name, body, created_at, updated_at) "
            "VALUES (?, ?, ?, ?)",
            (name, body, now, now),
        )


async def _grant_computer_use_tools(db: aiosqlite.Connection) -> None:
    """Give existing full-tool Bots computer/browser/Composio without wiping custom lists.

    Agents with tools_locked set (e.g. need-bots spawned with a deliberate
    minimal set) are never touched — the grant must not silently re-expand
    a curated list on every boot or agent creation.
    """
    from .tools.registry import COMPOSIO_PLUGIN_TOOLS, COMPUTER_USE_TOOLS, RESEARCH_TOOLS, SYSTEM_TOOLS

    extras = (
        list(COMPUTER_USE_TOOLS)
        + list(SYSTEM_TOOLS)
        + list(COMPOSIO_PLUGIN_TOOLS)
        + list(RESEARCH_TOOLS)
    )
    cur = await db.execute("SELECT name, tools, tools_locked FROM agents")
    rows = await cur.fetchall()
    for name, tools_raw, locked in rows:
        if name == "ledger" or locked:
            continue
        names = parse_tools(tools_raw)
        if "write_workspace" not in names and "read_only_shell" not in names:
            continue
        added = False
        for tool in extras:
            if tool not in names:
                names.append(tool)
                added = True
        if added:
            await db.execute(
                "UPDATE agents SET tools = ? WHERE name = ?",
                (json.dumps(names), name),
            )


async def init_db() -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("PRAGMA foreign_keys = ON")
        await db.executescript(SCHEMA)
        await _ensure_schema(db)
        await db.commit()

        cur = await db.execute("SELECT COUNT(*) FROM channels WHERE kind = 'room' OR kind IS NULL")
        (count,) = await cur.fetchone()
        if count == 0:
            for name, topic in _DEFAULT_CHANNELS:
                await db.execute(
                    "INSERT INTO channels (id, name, topic, created_at, kind) VALUES (?, ?, ?, ?, 'room')",
                    (name, name, topic, time.time()),
                )

        cur = await db.execute("SELECT COUNT(*) FROM agents")
        (count,) = await cur.fetchone()
        if count == 0:
            for name, prompt, model, scope, window, cap, tools, job in _DEFAULT_AGENTS:
                await db.execute(
                    "INSERT INTO agents (name, system_prompt, model, channel_scope, "
                    "created_at, history_window, max_tool_calls, tools, job, status, display_name) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'idle', ?)",
                    (name, prompt, model, scope, time.time(), window, cap, json.dumps(tools), job, pretty_name(name)),
                )
            await db.commit()
            await _ensure_schema(db)
        await db.commit()

    if _demo_mode():
        await seed_demo_thread()


_DEMO_SEED = [
    (
        "demo",
        "human",
        "What's blocking the release?",
    ),
    (
        "swarm",
        "agent",
        "[demo] Three items: migration script still running in staging, "
        "one flaky integration test on auth refresh, and the changelog "
        "hasn't been reviewed. Highest risk is the migration — I'd verify "
        "rollback before calling ship.",
    ),
    (
        "demo",
        "human",
        "@swarm draft a one-liner for the team",
    ),
    (
        "swarm",
        "agent",
        "[demo] Ship candidate: auth refresh fix is merged; migration finishes "
        "tonight; we'll go green once staging is clean and changelog is approved.",
    ),
]


def _demo_mode() -> bool:
    return (os.environ.get("SWARM_DEMO") or "").strip().lower() in ("1", "true", "yes")


async def seed_demo_thread() -> None:
    """Sample thread in #general when SWARM_DEMO=1 and the channel is empty."""
    if not _demo_mode():
        return
    if not await channel_exists("general"):
        return
    cur_count = await _count_messages("general")
    if cur_count > 0:
        return
    now = time.time()
    async with aiosqlite.connect(DB_PATH) as db:
        for i, (author, kind, body) in enumerate(_DEMO_SEED):
            await db.execute(
                "INSERT INTO messages (channel_id, parent_id, author, author_kind, body, created_at) "
                "VALUES (?, NULL, ?, ?, ?, ?)",
                ("general", author, kind, body, now + i * 0.01),
            )
        await db.commit()


async def _count_messages(channel_id: str) -> int:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "SELECT COUNT(*) FROM messages WHERE channel_id = ?", (channel_id,)
        )
        (count,) = await cur.fetchone()
        return int(count)


# ------------------------------------------------------------- channels ---

def public_channel(
    row: dict[str, Any],
    members: list[str] | None = None,
    people: list[str] | None = None,
) -> dict[str, Any]:
    out = dict(row)
    out["members"] = list(members or [])
    out["people"] = list(people or [])
    return out


async def _members_by_channel(db: aiosqlite.Connection) -> dict[str, list[str]]:
    cur = await db.execute(
        "SELECT channel_id, agent_name FROM channel_members ORDER BY sort_order, agent_name"
    )
    grouped: dict[str, list[str]] = {}
    for channel_id, agent_name in await cur.fetchall():
        grouped.setdefault(channel_id, []).append(agent_name)
    return grouped


async def _people_by_channel(db: aiosqlite.Connection) -> dict[str, list[str]]:
    cur = await db.execute(
        "SELECT channel_id, handle FROM channel_people ORDER BY handle"
    )
    grouped: dict[str, list[str]] = {}
    for channel_id, handle in await cur.fetchall():
        grouped.setdefault(channel_id, []).append(handle)
    return grouped


def can_view_channel(channel: dict[str, Any] | None, handle: str) -> bool:
    if channel is None:
        return False
    if channel.get("kind") == "people":
        return handle in (channel.get("people") or [])
    return True


def people_dm_id(a: str, b: str) -> str:
    x, y = sorted([(a or "").strip().lower(), (b or "").strip().lower()])
    return f"people-{x}-{y}"


async def list_channel_members(channel_id: str) -> list[str]:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "SELECT agent_name FROM channel_members WHERE channel_id = ? "
            "ORDER BY sort_order, agent_name",
            (channel_id,),
        )
        return [row[0] for row in await cur.fetchall()]


async def set_channel_members(channel_id: str, names: list[str]) -> list[str]:
    unique: list[str] = []
    for name in names:
        if name and name not in unique:
            unique.append(name)
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM channel_members WHERE channel_id = ?", (channel_id,))
        for i, name in enumerate(unique):
            await db.execute(
                "INSERT INTO channel_members (channel_id, agent_name, sort_order) VALUES (?, ?, ?)",
                (channel_id, name, i),
            )
        await db.commit()
    return unique


async def list_channels(viewer: str | None = None) -> list[dict[str, Any]]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute("SELECT * FROM channels ORDER BY created_at")
        rows = await cur.fetchall()
        members = await _members_by_channel(db)
        people = await _people_by_channel(db)
        channels = [
            public_channel(dict(r), members.get(r["id"], []), people.get(r["id"], []))
            for r in rows
        ]
    if viewer:
        return [c for c in channels if can_view_channel(c, viewer)]
    return channels


async def create_channel(
    channel_id: str,
    name: str,
    topic: str = "",
    *,
    kind: str = "room",
    owner_agent: str | None = None,
    members: list[str] | None = None,
) -> dict[str, Any]:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO channels (id, name, topic, created_at, kind, owner_agent) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (channel_id, name, topic, time.time(), kind, owner_agent),
        )
        roster: list[str] = []
        for i, agent_name in enumerate(members or []):
            if not agent_name or agent_name in roster:
                continue
            await db.execute(
                "INSERT INTO channel_members (channel_id, agent_name, sort_order) VALUES (?, ?, ?)",
                (channel_id, agent_name, i),
            )
            roster.append(agent_name)
        await db.commit()
    return {
        "id": channel_id, "name": name, "topic": topic,
        "kind": kind, "owner_agent": owner_agent, "members": roster, "people": [],
    }


async def get_channel(channel_id: str) -> dict[str, Any] | None:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute("SELECT * FROM channels WHERE id = ?", (channel_id,))
        row = await cur.fetchone()
        if row is None:
            return None
        cur = await db.execute(
            "SELECT agent_name FROM channel_members WHERE channel_id = ? "
            "ORDER BY sort_order, agent_name",
            (channel_id,),
        )
        members = [r[0] for r in await cur.fetchall()]
        cur = await db.execute(
            "SELECT handle FROM channel_people WHERE channel_id = ? ORDER BY handle",
            (channel_id,),
        )
        people = [r[0] for r in await cur.fetchall()]
        return public_channel(dict(row), members, people)


async def channel_exists(channel_id: str) -> bool:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT 1 FROM channels WHERE id = ?", (channel_id,))
        return await cur.fetchone() is not None


# -------------------------------------------------------------- messages --

async def add_message(
    channel_id: str,
    author: str,
    body: str,
    author_kind: str = "human",
    parent_id: int | None = None,
    model: str | None = None,
) -> dict[str, Any]:
    ts = time.time()
    async with aiosqlite.connect(DB_PATH) as db:
        try:
            cur = await db.execute(
                "INSERT INTO messages (channel_id, parent_id, author, author_kind, body, created_at, model) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (channel_id, parent_id, author, author_kind, body, ts, model),
            )
        except Exception:  # noqa: BLE001 — pre-migration database without the model column
            cur = await db.execute(
                "INSERT INTO messages (channel_id, parent_id, author, author_kind, body, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (channel_id, parent_id, author, author_kind, body, ts),
            )
            model = None
        await db.commit()
        msg_id = cur.lastrowid
    return {
        "id": msg_id,
        "channel_id": channel_id,
        "parent_id": parent_id,
        "author": author,
        "author_kind": author_kind,
        "body": body,
        "created_at": ts,
        "model": model,
    }


async def get_history(
    channel_id: str, limit: int = 50, before_id: int | None = None
) -> list[dict[str, Any]]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        if before_id is None:
            cur = await db.execute(
                "SELECT * FROM messages WHERE channel_id = ? "
                "ORDER BY id DESC LIMIT ?",
                (channel_id, limit),
            )
        else:
            cur = await db.execute(
                "SELECT * FROM messages WHERE channel_id = ? AND id < ? "
                "ORDER BY id DESC LIMIT ?",
                (channel_id, before_id, limit),
            )
        rows = await cur.fetchall()
        return [dict(r) for r in reversed(rows)]


async def get_history_after(
    channel_id: str, after_id: int, limit: int = 200,
) -> list[dict[str, Any]]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            "SELECT * FROM messages WHERE channel_id = ? AND id > ? "
            "ORDER BY id ASC LIMIT ?",
            (channel_id, after_id, min(max(limit, 1), 500)),
        )
        rows = await cur.fetchall()
        return [dict(r) for r in rows]


async def get_message(message_id: int) -> dict[str, Any] | None:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute("SELECT * FROM messages WHERE id = ?", (message_id,))
        row = await cur.fetchone()
        return dict(row) if row else None


async def get_replies(parent_id: int) -> list[dict[str, Any]]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            "SELECT * FROM messages WHERE parent_id = ? ORDER BY id ASC",
            (parent_id,),
        )
        rows = await cur.fetchall()
        return [dict(r) for r in rows]


async def channel_of_message(message_id: int) -> str | None:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT channel_id FROM messages WHERE id = ?", (message_id,))
        row = await cur.fetchone()
        return row[0] if row else None


def _like_escape(needle: str) -> str:
    """Escape LIKE wildcards so a literal %, _ or \\ in user input can't
    widen the match (or, on forget paths, widen the delete)."""
    return (
        (needle or "")
        .replace("\\", "\\\\")
        .replace("%", "\\%")
        .replace("_", "\\_")
    )


async def search_history(channel_id: str, query: str, limit: int = 10) -> list[dict[str, Any]]:
    """Naive substring search. Phase 6 (vector search) replaces this
    implementation only — the tool signature in agent.py stays the same."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            "SELECT * FROM messages WHERE channel_id = ? AND body LIKE ? ESCAPE '\\' "
            "ORDER BY created_at DESC LIMIT ?",
            (channel_id, f"%{_like_escape(query)}%", limit),
        )
        rows = await cur.fetchall()
        return [dict(r) for r in reversed(rows)]


async def message_exists(message_id: int) -> bool:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT 1 FROM messages WHERE id = ?", (message_id,))
        return await cur.fetchone() is not None


async def message_in_channel(message_id: int, channel_id: str) -> bool:
    """Whether a message can be used as a parent in the given channel."""
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "SELECT 1 FROM messages WHERE id = ? AND channel_id = ?",
            (message_id, channel_id),
        )
        return await cur.fetchone() is not None


async def delete_message(message_id: int) -> list[int]:
    """Delete a message and its direct replies. Returns removed ids."""
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT id FROM messages WHERE id = ?", (message_id,))
        if await cur.fetchone() is None:
            return []
        cur = await db.execute("SELECT id FROM messages WHERE parent_id = ?", (message_id,))
        reply_ids = [row[0] for row in await cur.fetchall()]
        ids = [message_id, *reply_ids]
        placeholders = ",".join("?" * len(ids))
        await db.execute(f"DELETE FROM reactions WHERE message_id IN ({placeholders})", ids)
        await db.execute(f"DELETE FROM messages WHERE id IN ({placeholders})", ids)
        await db.commit()
        return ids


async def delete_channel(channel_id: str) -> bool:
    """Remove a room and its messages. DMs are refused by the API layer."""
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT kind FROM channels WHERE id = ?", (channel_id,))
        row = await cur.fetchone()
        if row is None:
            return False
        cur = await db.execute("SELECT id FROM messages WHERE channel_id = ?", (channel_id,))
        ids = [r[0] for r in await cur.fetchall()]
        if ids:
            placeholders = ",".join("?" * len(ids))
            await db.execute(f"DELETE FROM reactions WHERE message_id IN ({placeholders})", ids)
        await db.execute("DELETE FROM messages WHERE channel_id = ?", (channel_id,))
        await db.execute("DELETE FROM agent_memory WHERE channel_id = ?", (channel_id,))
        await db.execute("DELETE FROM approvals WHERE channel_id = ?", (channel_id,))
        await db.execute("DELETE FROM channel_members WHERE channel_id = ?", (channel_id,))
        await db.execute("DELETE FROM channels WHERE id = ?", (channel_id,))
        await db.commit()
        return True


# -------------------------------------------------------------- reactions -

async def add_reaction(message_id: int, author: str, emoji: str) -> bool:
    """Idempotent. Returns True if a new row was inserted, False if it
    already existed (caller treats both as success)."""
    async with aiosqlite.connect(DB_PATH) as db:
        try:
            await db.execute(
                "INSERT INTO reactions (message_id, author, emoji, created_at) VALUES (?, ?, ?, ?)",
                (message_id, author, emoji, time.time()),
            )
            await db.commit()
            return True
        except aiosqlite.IntegrityError:
            return False


async def remove_reaction(message_id: int, author: str, emoji: str) -> bool:
    """Delete one user's emoji reaction. Returns True if a row was removed."""
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "DELETE FROM reactions WHERE message_id = ? AND author = ? AND emoji = ?",
            (message_id, author, emoji),
        )
        await db.commit()
        return cur.rowcount > 0


async def get_reactions(message_id: int) -> list[dict[str, Any]]:
    return (await get_reactions_many([message_id])).get(message_id, [])


async def get_reactions_many(message_ids: list[int]) -> dict[int, list[dict[str, Any]]]:
    """One query for a page of messages (fixes the per-message N+1)."""
    ids = [int(m) for m in message_ids]
    out: dict[int, list[dict[str, Any]]] = {m: [] for m in ids}
    if not ids:
        return out
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            "SELECT message_id, author, emoji, created_at FROM reactions "
            f"WHERE message_id IN ({','.join('?' for _ in ids)})",
            ids,
        )
        for row in await cur.fetchall():
            out.setdefault(int(row["message_id"]), []).append(
                {"author": row["author"], "emoji": row["emoji"],
                 "created_at": row["created_at"]})
        return out


# ------------------------------------------------------------------ users -

async def create_user(
    handle: str, token: str, role: str | None = None, password: str | None = None,
) -> dict[str, Any]:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT COUNT(*) FROM users")
        (count,) = await cur.fetchone()
        if role not in USER_ROLES:
            role = "admin" if int(count) == 0 else "member"
        pw_hash = hash_password(password) if password else None
        await db.execute(
            "INSERT INTO users (handle, token_hash, created_at, role, password_hash) "
            "VALUES (?, ?, ?, ?, ?)",
            (handle, hash_token(token), time.time(), role, pw_hash),
        )
        await db.commit()
    return {"handle": handle, "role": role}


async def user_count() -> int:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT COUNT(*) FROM users")
        (count,) = await cur.fetchone()
        return int(count)


async def user_has_password(handle: str) -> bool:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "SELECT password_hash FROM users WHERE handle = ?", (handle,),
        )
        row = await cur.fetchone()
        return bool(row and row[0])


async def admin_password_ok(handle: str, password: str | None) -> bool:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "SELECT password_hash FROM users WHERE handle = ? AND role = 'admin'",
            (handle,),
        )
        row = await cur.fetchone()
        if row is None:
            return False
        return verify_password(password or "", row[0])


async def list_admin_handles() -> list[str]:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "SELECT handle FROM users WHERE role = 'admin' ORDER BY created_at"
        )
        return [row[0] for row in await cur.fetchall()]


async def get_workspace_meta(key: str) -> str | None:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "SELECT value FROM workspace_meta WHERE key = ?", (key,),
        )
        row = await cur.fetchone()
        return row[0] if row else None


async def set_workspace_meta(key: str, value: str) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO workspace_meta (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )
        await db.commit()


async def workspace_onboarded() -> bool:
    return (await get_workspace_meta("onboarded")) == "1"


async def mark_workspace_onboarded() -> None:
    await set_workspace_meta("onboarded", "1")


async def user_exists(handle: str) -> bool:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT 1 FROM users WHERE handle = ?", (handle,))
        return await cur.fetchone() is not None


async def verify_token(handle: str, token: str) -> bool:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT token_hash FROM users WHERE handle = ?", (handle,))
        row = await cur.fetchone()
        if row is None:
            return False
        return row[0] == hash_token(token)


def generate_token() -> str:
    return secrets.token_urlsafe(32)


async def rotate_user_token(
    handle: str, token: str, *, role: str | None = None,
) -> dict[str, Any] | None:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        if role in USER_ROLES:
            cur = await db.execute(
                "UPDATE users SET token_hash = ?, role = ? WHERE handle = ?",
                (hash_token(token), role, handle),
            )
        else:
            cur = await db.execute(
                "UPDATE users SET token_hash = ? WHERE handle = ?",
                (hash_token(token), handle),
            )
        if cur.rowcount == 0:
            return None
        await db.commit()
        cur = await db.execute(
            "SELECT handle, role, created_at FROM users WHERE handle = ?", (handle,),
        )
        row = await cur.fetchone()
        return dict(row) if row else None


# ----------------------------------------------------------------- agents -

async def list_agents(
    channel_id: str | None = None, *, include_archived: bool = False,
) -> list[dict[str, Any]]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute("SELECT * FROM agents ORDER BY created_at")
        rows = await cur.fetchall()
        agents = [public_agent(dict(r)) for r in rows]
    if not include_archived:
        agents = [a for a in agents if not a.get("archived")]
    if channel_id is None:
        return agents
    return [a for a in agents if a["channel_scope"] in (None, channel_id)]


async def fetch_agent(name: str) -> dict[str, Any] | None:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute("SELECT * FROM agents WHERE name = ?", (name,))
        row = await cur.fetchone()
        if row is None:
            return None
        return public_agent(dict(row))


async def get_agent(name: str) -> dict[str, Any] | None:
    agent = await fetch_agent(name)
    if agent is None:
        return None
    agent["memories"] = await list_memories(name, limit=20)
    return agent


async def create_agent(
    name: str,
    system_prompt: str,
    model: str,
    channel_scope: str | None,
    history_window: int = 12,
    max_tool_calls: int = 6,
    tools: list[str] | None = None,
    job: str = DEFAULT_JOB,
    display_name: str | None = None,
    avatar: str | None = None,
    tools_locked: bool = False,
) -> dict[str, Any]:
    tool_names = tools if tools is not None else list(DEFAULT_TOOLS)
    job_title = (job or DEFAULT_JOB).strip() or DEFAULT_JOB
    model = resolve_groq_model(model)
    label = (display_name or "").strip() or pretty_name(name)
    pfp = (avatar or "").strip()[:500]
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO agents (name, system_prompt, model, channel_scope, "
            "created_at, history_window, max_tool_calls, tools, job, status, display_name, avatar, tools_locked) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'idle', ?, ?, ?)",
            (
                name, system_prompt, model, channel_scope, time.time(),
                history_window, max_tool_calls, json.dumps(tool_names), job_title, label,
                pfp, 1 if tools_locked else 0,
            ),
        )
        await db.commit()
        await _ensure_schema(db)
        await db.commit()
    return {
        "name": name,
        "display_name": label,
        "avatar": pfp,
        "system_prompt": system_prompt,
        "model": model,
        "channel_scope": channel_scope,
        "history_window": history_window,
        "max_tool_calls": max_tool_calls,
        "tools": tool_names,
        "job": job_title,
        "status": "idle",
        "dm_channel_id": dm_channel_id(name),
        "archived": False,
        "tools_locked": tools_locked,
    }


async def update_agent(name: str, fields: dict[str, Any]) -> dict[str, Any] | None:
    if not fields:
        return await get_agent(name)
    allowed = {
        "system_prompt", "model", "channel_scope",
        "history_window", "max_tool_calls", "tools", "job", "status",
        "display_name", "avatar",
    }
    sets: list[str] = []
    values: list[Any] = []
    for key, value in fields.items():
        if key not in allowed:
            continue
        if key == "tools":
            value = json.dumps(parse_tools(value))
        if key == "model" and value:
            value = resolve_groq_model(value)
        sets.append(f"{key} = ?")
        values.append(value)
    if not sets:
        return await get_agent(name)
    values.append(name)
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            f"UPDATE agents SET {', '.join(sets)} WHERE name = ?",
            values,
        )
        await db.commit()
    return await get_agent(name)


async def agent_exists(name: str) -> bool:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT 1 FROM agents WHERE name = ?", (name,))
        return await cur.fetchone() is not None


# --------------------------------------------------------------- memory ---

async def add_memory(
    agent_name: str,
    body: str,
    *,
    channel_id: str | None = None,
    kind: str = "note",
) -> dict[str, Any]:
    ts = time.time()
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "INSERT INTO agent_memory (agent_name, channel_id, kind, body, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (agent_name, channel_id, kind, body, ts, ts),
        )
        await db.commit()
        mem_id = cur.lastrowid
    return {
        "id": mem_id,
        "agent_name": agent_name,
        "channel_id": channel_id,
        "kind": kind,
        "body": body,
        "created_at": ts,
        "updated_at": ts,
    }


async def replace_summary(agent_name: str, channel_id: str, body: str) -> dict[str, Any]:
    ts = time.time()
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "DELETE FROM agent_memory WHERE agent_name = ? AND channel_id = ? AND kind = 'summary'",
            (agent_name, channel_id),
        )
        cur = await db.execute(
            "INSERT INTO agent_memory (agent_name, channel_id, kind, body, created_at, updated_at) "
            "VALUES (?, ?, 'summary', ?, ?, ?)",
            (agent_name, channel_id, body, ts, ts),
        )
        await db.commit()
        mem_id = cur.lastrowid
    return {
        "id": mem_id,
        "agent_name": agent_name,
        "channel_id": channel_id,
        "kind": "summary",
        "body": body,
        "created_at": ts,
        "updated_at": ts,
    }


async def get_summary(agent_name: str, channel_id: str) -> dict[str, Any] | None:
    """Latest channel summary for one agent, if any."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            "SELECT * FROM agent_memory WHERE agent_name = ? AND channel_id = ? "
            "AND kind = 'summary' ORDER BY updated_at DESC LIMIT 1",
            (agent_name, channel_id),
        )
        row = await cur.fetchone()
        return dict(row) if row else None


async def append_summary(
    agent_name: str, channel_id: str, body: str, cap_chars: int = 3000
) -> dict[str, Any]:
    """Accumulate onto the channel summary instead of clobbering it.

    Each compaction pass appends its extract and the tail is kept under
    cap_chars, so early history survives across many turns.
    """
    body = (body or "").strip()
    if not body:
        existing = await get_summary(agent_name, channel_id)
        if existing is None:
            raise ValueError("nothing to summarize")
        return existing
    previous = await get_summary(agent_name, channel_id)
    if previous and previous.get("body"):
        if body in previous["body"]:
            return previous  # idempotent: same extract compacted twice
        combined = f"{previous['body']}\n{body}"
    else:
        combined = body
    if len(combined) > cap_chars:
        combined = "…[trimmed]\n" + combined[-cap_chars:]
    return await replace_summary(agent_name, channel_id, combined)


async def list_memories(agent_name: str, limit: int = 20) -> list[dict[str, Any]]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            "SELECT * FROM agent_memory WHERE agent_name = ? "
            "ORDER BY created_at DESC LIMIT ?",
            (agent_name, limit),
        )
        rows = await cur.fetchall()
        return [dict(r) for r in reversed(rows)]


async def get_context_memories(
    agent_name: str, channel_id: str, limit: int = 12
) -> tuple[list[dict[str, Any]], dict[str, Any] | None]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            "SELECT * FROM agent_memory WHERE agent_name = ? AND kind = 'note' "
            "AND (channel_id IS NULL OR channel_id = ?) "
            "ORDER BY created_at DESC LIMIT ?",
            (agent_name, channel_id, limit),
        )
        notes = [dict(r) for r in reversed(await cur.fetchall())]
        cur = await db.execute(
            "SELECT * FROM agent_memory WHERE agent_name = ? AND channel_id = ? "
            "AND kind = 'summary' ORDER BY updated_at DESC LIMIT 1",
            (agent_name, channel_id),
        )
        row = await cur.fetchone()
        summary = dict(row) if row else None
    return notes, summary


async def search_memory(
    agent_name: str, query: str, channel_id: str | None = None, limit: int = 10
) -> list[dict[str, Any]]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        like = f"%{_like_escape(query)}%"
        scope, params = (
            ("AND (channel_id IS NULL OR channel_id = ?) ", (agent_name, channel_id, like, limit))
            if channel_id else
            ("", (agent_name, like, limit))
        )
        cur = await db.execute(
            "SELECT * FROM agent_memory WHERE agent_name = ? AND kind = 'note' "
            f"{scope}AND body LIKE ? ESCAPE '\\' ORDER BY created_at DESC LIMIT ?",
            params,
        )
        rows = await cur.fetchall()
        return [dict(r) for r in reversed(rows)]


async def delete_memory(memory_id: int, agent_name: str) -> bool:
    """Delete one memory note by id. Returns True when a row was removed."""
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "DELETE FROM agent_memory WHERE id = ? AND agent_name = ? AND kind = 'note'",
            (memory_id, agent_name),
        )
        await db.commit()
        return cur.rowcount > 0


async def forget_memory_by_query(
    agent_name: str, query: str, channel_id: str | None = None
) -> int:
    """Delete notes matching a keyword query. Returns the removed count."""
    needle = (query or "").strip()
    if not needle:
        return 0
    like = f"%{_like_escape(needle)}%"
    scope, params = (
        ("AND (channel_id IS NULL OR channel_id = ?) ", (agent_name, channel_id, like))
        if channel_id else
        ("", (agent_name, like))
    )
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "DELETE FROM agent_memory WHERE agent_name = ? AND kind = 'note' "
            f"{scope}AND body LIKE ? ESCAPE '\\'",
            params,
        )
        await db.commit()
        return cur.rowcount


async def count_memories(agent_name: str, channel_id: str | None = None) -> dict[str, int]:
    scope, params = (
        ("AND (channel_id IS NULL OR channel_id = ?) ", (agent_name, channel_id))
        if channel_id else
        ("", (agent_name,))
    )
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "SELECT COUNT(*) FROM agent_memory WHERE agent_name = ? AND kind = 'note' "
            f"{scope}".rstrip(),
            params,
        )
        (notes,) = await cur.fetchone()
        summaries = 0
        if channel_id:
            cur = await db.execute(
                "SELECT COUNT(*) FROM agent_memory WHERE agent_name = ? AND channel_id = ? "
                "AND kind = 'summary'",
                (agent_name, channel_id),
            )
            (summaries,) = await cur.fetchone()
    return {"notes": notes, "summaries": summaries}


async def set_agent_status(name: str, status: str) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE agents SET status = ? WHERE name = ?", (status, name))
        await db.commit()


async def get_recent_system_messages(limit: int = 20) -> list[dict[str, Any]]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            "SELECT * FROM messages WHERE author_kind = 'system' "
            "ORDER BY id DESC LIMIT ?",
            (limit,),
        )
        return [dict(r) for r in await cur.fetchall()]


# ---------------------------------------------------------------- skills --

async def list_skills() -> list[dict[str, Any]]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute("SELECT * FROM skills ORDER BY name")
        return [dict(r) for r in await cur.fetchall()]


async def get_skill(name: str) -> dict[str, Any] | None:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute("SELECT * FROM skills WHERE name = ?", (name,))
        row = await cur.fetchone()
        return dict(row) if row else None


async def get_skill_by_id(skill_id: int) -> dict[str, Any] | None:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute("SELECT * FROM skills WHERE id = ?", (skill_id,))
        row = await cur.fetchone()
        return dict(row) if row else None


async def upsert_skill(name: str, body: str) -> dict[str, Any]:
    ts = time.time()
    existing = await get_skill(name)
    async with aiosqlite.connect(DB_PATH) as db:
        if existing:
            await db.execute(
                "UPDATE skills SET body = ?, updated_at = ? WHERE name = ?",
                (body, ts, name),
            )
            await db.commit()
            return await get_skill(name)  # type: ignore[return-value]
        cur = await db.execute(
            "INSERT INTO skills (name, body, created_at, updated_at) VALUES (?, ?, ?, ?)",
            (name, body, ts, ts),
        )
        await db.commit()
        return {
            "id": cur.lastrowid, "name": name, "body": body,
            "created_at": ts, "updated_at": ts,
        }


async def update_skill(skill_id: int, fields: dict[str, Any]) -> dict[str, Any] | None:
    row = await get_skill_by_id(skill_id)
    if row is None:
        return None
    name = fields.get("name", row["name"])
    body = fields.get("body", row["body"])
    ts = time.time()
    async with aiosqlite.connect(DB_PATH) as db:
        try:
            await db.execute(
                "UPDATE skills SET name = ?, body = ?, updated_at = ? WHERE id = ?",
                (name, body, ts, skill_id),
            )
            await db.commit()
        except aiosqlite.IntegrityError:
            return None
    return await get_skill_by_id(skill_id)


async def delete_skill(skill_id: int) -> bool:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("DELETE FROM skills WHERE id = ?", (skill_id,))
        await db.commit()
        return cur.rowcount > 0


# -------------------------------------------------------------- routines -

def _routine_row(row: dict[str, Any]) -> dict[str, Any]:
    out = dict(row)
    out["enabled"] = bool(out.get("enabled"))
    return out


async def list_routines(agent_name: str | None = None) -> list[dict[str, Any]]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        if agent_name:
            cur = await db.execute(
                "SELECT * FROM routines WHERE agent_name = ? ORDER BY created_at",
                (agent_name,),
            )
        else:
            cur = await db.execute("SELECT * FROM routines ORDER BY created_at")
        return [_routine_row(dict(r)) for r in await cur.fetchall()]


async def count_routines(agent_name: str) -> int:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "SELECT COUNT(*) FROM routines WHERE agent_name = ?", (agent_name,)
        )
        (n,) = await cur.fetchone()
        return int(n)


async def get_routine(routine_id: int) -> dict[str, Any] | None:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute("SELECT * FROM routines WHERE id = ?", (routine_id,))
        row = await cur.fetchone()
        return _routine_row(dict(row)) if row else None


async def create_routine(
    agent_name: str,
    title: str,
    instructions: str,
    interval_minutes: int,
    enabled: bool = True,
) -> dict[str, Any]:
    ts = time.time()
    next_run = ts + interval_minutes * 60
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "INSERT INTO routines (agent_name, title, instructions, interval_minutes, "
            "enabled, last_run_at, next_run_at, created_at) "
            "VALUES (?, ?, ?, ?, ?, NULL, ?, ?)",
            (agent_name, title, instructions, interval_minutes, 1 if enabled else 0, next_run, ts),
        )
        await db.commit()
        rid = cur.lastrowid
    return await get_routine(rid)  # type: ignore[return-value]


async def update_routine(routine_id: int, fields: dict[str, Any]) -> dict[str, Any] | None:
    row = await get_routine(routine_id)
    if row is None:
        return None
    allowed = {"title", "instructions", "interval_minutes", "enabled", "last_run_at", "next_run_at"}
    sets: list[str] = []
    values: list[Any] = []
    for key, value in fields.items():
        if key not in allowed:
            continue
        if key == "enabled":
            value = 1 if value else 0
        sets.append(f"{key} = ?")
        values.append(value)
    if not sets:
        return row
    values.append(routine_id)
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            f"UPDATE routines SET {', '.join(sets)} WHERE id = ?",
            values,
        )
        await db.commit()
    return await get_routine(routine_id)


async def delete_routine(routine_id: int) -> bool:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM routine_runs WHERE routine_id = ?", (routine_id,))
        cur = await db.execute("DELETE FROM routines WHERE id = ?", (routine_id,))
        await db.commit()
        return cur.rowcount > 0


async def due_routines(now: float | None = None) -> list[dict[str, Any]]:
    ts = now if now is not None else time.time()
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            "SELECT * FROM routines WHERE enabled = 1 AND next_run_at <= ? "
            "ORDER BY next_run_at",
            (ts,),
        )
        return [_routine_row(dict(r)) for r in await cur.fetchall()]


async def mark_routine_run(
    routine_id: int, *, status: str, excerpt: str, interval_minutes: int,
    advance_schedule: bool = True,
) -> None:
    ts = time.time()
    next_run = ts + interval_minutes * 60
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO routine_runs (routine_id, started_at, finished_at, status, excerpt) "
            "VALUES (?, ?, ?, ?, ?)",
            (routine_id, ts, ts, status, excerpt[:500]),
        )
        if advance_schedule:
            await db.execute(
                "UPDATE routines SET last_run_at = ?, next_run_at = ? WHERE id = ?",
                (ts, next_run, routine_id),
            )
        await db.commit()


async def list_routine_runs(routine_id: int, limit: int = 20) -> list[dict[str, Any]]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            "SELECT * FROM routine_runs WHERE routine_id = ? ORDER BY id DESC LIMIT ?",
            (routine_id, limit),
        )
        return [dict(r) for r in await cur.fetchall()]


# ------------------------------------------------------------ approvals --

async def create_approval(
    agent_name: str, channel_id: str, action: str, detail: str = ""
) -> dict[str, Any]:
    ts = time.time()
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "INSERT INTO approvals (agent_name, channel_id, action, detail, status, created_at) "
            "VALUES (?, ?, ?, ?, 'pending', ?)",
            (agent_name, channel_id, action, detail, ts),
        )
        await db.commit()
        aid = cur.lastrowid
    return {
        "id": aid, "agent_name": agent_name, "channel_id": channel_id,
        "action": action, "detail": detail, "status": "pending",
        "created_at": ts, "resolved_at": None,
    }


async def get_approval(approval_id: int) -> dict[str, Any] | None:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute("SELECT * FROM approvals WHERE id = ?", (approval_id,))
        row = await cur.fetchone()
        return dict(row) if row else None


async def list_approvals(
    *, channel_id: str | None = None, status: str | None = "pending", limit: int = 50
) -> list[dict[str, Any]]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        clauses = []
        args: list[Any] = []
        if channel_id:
            clauses.append("channel_id = ?")
            args.append(channel_id)
        if status:
            clauses.append("status = ?")
            args.append(status)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        cur = await db.execute(
            f"SELECT * FROM approvals {where} ORDER BY id DESC LIMIT ?",
            (*args, limit),
        )
        return [dict(r) for r in await cur.fetchall()]


async def resolve_approval(approval_id: int, status: str) -> dict[str, Any] | None:
    if status not in {"approved", "denied"}:
        raise ValueError("invalid approval status")
    ts = time.time()
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            "UPDATE approvals SET status = ?, resolved_at = ? "
            "WHERE id = ? AND status = 'pending'",
            (status, ts, approval_id),
        )
        if cur.rowcount == 0:
            cur = await db.execute("SELECT * FROM approvals WHERE id = ?", (approval_id,))
            row = await cur.fetchone()
            return None if row is None else {**dict(row), "_already_resolved": True}
        cur = await db.execute("SELECT * FROM approvals WHERE id = ?", (approval_id,))
        row = await cur.fetchone()
        await db.commit()
    return dict(row) if row else None


# ---------------------------------------------------------- custom tools --

def _json_obj(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        try:
            val = json.loads(raw)
            return val if isinstance(val, dict) else {}
        except json.JSONDecodeError:
            return {}
    return {}


def _public_custom_tool(row: dict[str, Any]) -> dict[str, Any]:
    out = dict(row)
    out["parameters"] = _json_obj(out.get("parameters"))
    out["handler_config"] = _json_obj(out.get("handler_config"))
    out["enabled"] = bool(out.get("enabled"))
    return out


async def list_custom_tools(*, enabled_only: bool = False) -> list[dict[str, Any]]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        q = "SELECT * FROM custom_tools"
        if enabled_only:
            q += " WHERE enabled = 1"
        q += " ORDER BY name"
        cur = await db.execute(q)
        return [_public_custom_tool(dict(r)) for r in await cur.fetchall()]


async def create_custom_tool(
    name: str,
    description: str,
    *,
    parameters: dict[str, Any] | None = None,
    handler_type: str = "template",
    handler_config: dict[str, Any] | None = None,
    enabled: bool = True,
) -> dict[str, Any]:
    ts = time.time()
    params = json.dumps(parameters or {"type": "object", "properties": {}})
    config = json.dumps(handler_config or {})
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "INSERT INTO custom_tools (name, description, parameters, handler_type, "
            "handler_config, enabled, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (name, description, params, handler_type, config, int(enabled), ts, ts),
        )
        await db.commit()
        tid = cur.lastrowid
    return _public_custom_tool({
        "id": tid, "name": name, "description": description,
        "parameters": parameters, "handler_type": handler_type,
        "handler_config": handler_config, "enabled": enabled,
        "created_at": ts, "updated_at": ts,
    })


async def update_custom_tool(tool_id: int, fields: dict[str, Any]) -> dict[str, Any] | None:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute("SELECT * FROM custom_tools WHERE id = ?", (tool_id,))
        row = await cur.fetchone()
        if row is None:
            return None
        sets: list[str] = []
        args: list[Any] = []
        for key, col in (
            ("description", "description"),
            ("handler_type", "handler_type"),
            ("enabled", "enabled"),
        ):
            if key in fields and fields[key] is not None:
                val = fields[key]
                if key == "enabled":
                    val = int(bool(val))
                sets.append(f"{col} = ?")
                args.append(val)
        if "parameters" in fields and fields["parameters"] is not None:
            sets.append("parameters = ?")
            args.append(json.dumps(fields["parameters"]))
        if "handler_config" in fields and fields["handler_config"] is not None:
            sets.append("handler_config = ?")
            args.append(json.dumps(fields["handler_config"]))
        if not sets:
            return _public_custom_tool(dict(row))
        sets.append("updated_at = ?")
        args.append(time.time())
        args.append(tool_id)
        await db.execute(
            f"UPDATE custom_tools SET {', '.join(sets)} WHERE id = ?",
            args,
        )
        await db.commit()
        cur = await db.execute("SELECT * FROM custom_tools WHERE id = ?", (tool_id,))
        updated = await cur.fetchone()
        return _public_custom_tool(dict(updated)) if updated else None


async def delete_custom_tool(tool_id: int) -> bool:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("DELETE FROM custom_tools WHERE id = ?", (tool_id,))
        await db.commit()
        return cur.rowcount > 0


# -------------------------------------------------------- ai providers ---

async def upsert_ai_provider(
    provider_id: str, secret: str, *, model: str | None = None
) -> dict[str, Any]:
    ts = time.time()
    hint = f"…{secret[-4:]}" if len(secret) > 4 else "****"
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO ai_providers (provider_id, secret, key_hint, model, connected_at) "
            "VALUES (?, ?, ?, ?, ?) "
            "ON CONFLICT(provider_id) DO UPDATE SET secret=excluded.secret, "
            "key_hint=excluded.key_hint, model=excluded.model, connected_at=excluded.connected_at",
            (provider_id, secret, hint, model, ts),
        )
        await db.commit()
    return {
        "provider_id": provider_id, "connected": True,
        "model": model, "connected_at": ts, "key_hint": hint,
    }


async def update_ai_provider_oauth(
    provider_id: str, secret: str, refresh_secret: str | None, expires_at: float | None, *, model: str | None = None
) -> dict[str, Any]:
    ts = time.time()
    hint = f"…{secret[-4:]}" if len(secret) > 4 else "****"
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO ai_providers (provider_id, secret, key_hint, model, connected_at, auth_method, refresh_secret, expires_at) VALUES (?, ?, ?, ?, ?, 'oauth', ?, ?) "
            "ON CONFLICT(provider_id) DO UPDATE SET secret=excluded.secret, key_hint=excluded.key_hint, model=COALESCE(excluded.model, ai_providers.model), connected_at=excluded.connected_at, auth_method='oauth', refresh_secret=excluded.refresh_secret, expires_at=excluded.expires_at",
            (provider_id, secret, hint, model, ts, refresh_secret, expires_at),
        )
        await db.commit()
    return {"provider_id": provider_id, "connected": True, "model": model, "connected_at": ts, "key_hint": hint, "auth_method": "oauth", "expires_at": expires_at}


async def list_ai_providers() -> list[dict[str, Any]]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute("SELECT * FROM ai_providers ORDER BY provider_id")
        return [dict(r) for r in await cur.fetchall()]


async def get_ai_provider(provider_id: str) -> dict[str, Any] | None:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            "SELECT * FROM ai_providers WHERE provider_id = ?", (provider_id,)
        )
        row = await cur.fetchone()
        return dict(row) if row else None


async def delete_ai_provider(provider_id: str) -> bool:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "DELETE FROM ai_providers WHERE provider_id = ?", (provider_id,)
        )
        await db.commit()
        return cur.rowcount > 0


async def update_ai_provider_model(provider_id: str, model: str | None) -> dict[str, Any] | None:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            "UPDATE ai_providers SET model = ? WHERE provider_id = ?",
            (model, provider_id),
        )
        await db.commit()
        if cur.rowcount == 0:
            return None
        cur = await db.execute(
            "SELECT * FROM ai_providers WHERE provider_id = ?", (provider_id,)
        )
        row = await cur.fetchone()
        return dict(row) if row else None


# ------------------------------------------------------------------ people -

async def get_user(handle: str) -> dict[str, Any] | None:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            "SELECT handle, role, created_at FROM users WHERE handle = ?", (handle,)
        )
        row = await cur.fetchone()
        return dict(row) if row else None


async def get_user_role(handle: str) -> str:
    user = await get_user(handle)
    return (user or {}).get("role") or "member"


async def list_people() -> list[dict[str, Any]]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            "SELECT handle, role, created_at FROM users ORDER BY created_at"
        )
        return [dict(r) for r in await cur.fetchall()]


async def ensure_people_dm(me: str, other: str) -> dict[str, Any]:
    left = (me or "").strip()
    right = (other or "").strip()
    if not left or not right:
        raise ValueError("both handles are required")
    if left.lower() == right.lower():
        raise ValueError("cannot DM yourself")
    channel_id = people_dm_id(left, right)
    existing = await get_channel(channel_id)
    if existing is not None:
        return existing
    handles = sorted([left, right], key=str.lower)
    topic = f"1:1 · {handles[0]} and {handles[1]}"
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO channels (id, name, topic, created_at, kind, owner_agent) "
            "VALUES (?, ?, ?, ?, 'people', NULL)",
            (channel_id, f"{handles[0]} / {handles[1]}", topic, time.time()),
        )
        for handle in handles:
            await db.execute(
                "INSERT INTO channel_people (channel_id, handle) VALUES (?, ?)",
                (channel_id, handle),
            )
        await db.commit()
    channel = await get_channel(channel_id)
    assert channel is not None
    return channel


async def archive_agent(name: str) -> dict[str, Any] | None:
    row = await fetch_agent(name)
    if row is None:
        return None
    if row.get("archived"):
        return row
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE agents SET archived_at = ?, status = 'idle' WHERE name = ?",
            (time.time(), name),
        )
        await db.commit()
    return await fetch_agent(name)


# ----------------------------------------------------------------- teams ---

def _public_team(row: dict[str, Any], members: list[str] | None = None) -> dict[str, Any]:
    return {
        "id": row["id"],
        "name": row.get("name") or row["id"],
        "description": row.get("description") or "",
        "created_at": row.get("created_at"),
        "members": list(members or []),
    }


async def list_teams() -> list[dict[str, Any]]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute("SELECT * FROM agent_teams ORDER BY created_at")
        rows = [dict(r) for r in await cur.fetchall()]
        cur = await db.execute(
            "SELECT team_id, agent_name FROM agent_team_members "
            "ORDER BY sort_order, agent_name"
        )
        grouped: dict[str, list[str]] = {}
        for team_id, agent_name in await cur.fetchall():
            grouped.setdefault(team_id, []).append(agent_name)
    return [_public_team(r, grouped.get(r["id"], [])) for r in rows]


async def get_team(team_id: str) -> dict[str, Any] | None:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute("SELECT * FROM agent_teams WHERE id = ?", (team_id,))
        row = await cur.fetchone()
        if row is None:
            return None
        cur = await db.execute(
            "SELECT agent_name FROM agent_team_members WHERE team_id = ? "
            "ORDER BY sort_order, agent_name",
            (team_id,),
        )
        members = [r[0] for r in await cur.fetchall()]
        return _public_team(dict(row), members)


async def create_team(
    team_id: str, name: str, members: list[str], description: str = "",
) -> dict[str, Any]:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO agent_teams (id, name, description, created_at) VALUES (?, ?, ?, ?)",
            (team_id, name, description or "", time.time()),
        )
        roster: list[str] = []
        for i, agent_name in enumerate(members):
            if not agent_name or agent_name in roster:
                continue
            await db.execute(
                "INSERT INTO agent_team_members (team_id, agent_name, sort_order) "
                "VALUES (?, ?, ?)",
                (team_id, agent_name, i),
            )
            roster.append(agent_name)
        await db.commit()
    return _public_team(
        {"id": team_id, "name": name, "description": description or ""}, roster,
    )


async def update_team(team_id: str, fields: dict[str, Any]) -> dict[str, Any] | None:
    current = await get_team(team_id)
    if current is None:
        return None
    async with aiosqlite.connect(DB_PATH) as db:
        sets: list[str] = []
        args: list[Any] = []
        if "name" in fields and fields["name"] is not None:
            sets.append("name = ?")
            args.append(fields["name"])
        if "description" in fields and fields["description"] is not None:
            sets.append("description = ?")
            args.append(fields["description"])
        if sets:
            args.append(team_id)
            await db.execute(
                f"UPDATE agent_teams SET {', '.join(sets)} WHERE id = ?",
                args,
            )
        if "members" in fields and fields["members"] is not None:
            await db.execute("DELETE FROM agent_team_members WHERE team_id = ?", (team_id,))
            roster: list[str] = []
            for i, agent_name in enumerate(fields["members"]):
                if not agent_name or agent_name in roster:
                    continue
                await db.execute(
                    "INSERT INTO agent_team_members (team_id, agent_name, sort_order) "
                    "VALUES (?, ?, ?)",
                    (team_id, agent_name, i),
                )
                roster.append(agent_name)
        await db.commit()
    return await get_team(team_id)


async def delete_team(team_id: str) -> bool:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM agent_team_members WHERE team_id = ?", (team_id,))
        cur = await db.execute("DELETE FROM agent_teams WHERE id = ?", (team_id,))
        await db.commit()
        return cur.rowcount > 0


# ---------------------------------------------------------- search/export -

async def export_messages(channel_id: str) -> list[dict[str, Any]]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            "SELECT * FROM messages WHERE channel_id = ? ORDER BY created_at, id",
            (channel_id,),
        )
        return [dict(r) for r in await cur.fetchall()]


async def search_workspace(
    query: str, *, viewer: str, limit: int = 30,
) -> list[dict[str, Any]]:
    needle = (query or "").strip()
    if len(needle) < 2:
        return []
    like = f"%{needle}%"
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            "SELECT m.id, m.channel_id, m.author, m.author_kind, m.body, m.created_at, "
            "m.parent_id, m.model, c.name AS channel_name, c.kind AS channel_kind "
            "FROM messages m JOIN channels c ON c.id = m.channel_id "
            "WHERE m.body LIKE ? COLLATE NOCASE "
            "ORDER BY m.created_at DESC, m.id DESC LIMIT ?",
            (like, max(1, min(int(limit), 50))),
        )
        rows = [dict(r) for r in await cur.fetchall()]
        people = await _people_by_channel(db)
    out: list[dict[str, Any]] = []
    for row in rows:
        channel = {
            "id": row["channel_id"],
            "kind": row.get("channel_kind") or "room",
            "people": people.get(row["channel_id"], []),
        }
        if not can_view_channel(channel, viewer):
            continue
        out.append({
            "id": row["id"],
            "channel_id": row["channel_id"],
            "channel_name": row.get("channel_name") or row["channel_id"],
            "channel_kind": row.get("channel_kind") or "room",
            "author": row["author"],
            "author_kind": row["author_kind"],
            "body": row["body"],
            "parent_id": row.get("parent_id"),
            "created_at": row["created_at"],
        })
    return out
