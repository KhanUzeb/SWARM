"""
swarm.db — thin async wrapper over SQLite.

No ORM. If this needs to outgrow SQLite, that's a good problem to have
and a different file. Schema growth past CREATE TABLE IF NOT EXISTS
goes through ensure_schema() (PRAGMA + ALTER TABLE).
"""
from __future__ import annotations

import hashlib
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
    created_at  REAL NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_messages_channel ON messages(channel_id, created_at);
CREATE INDEX IF NOT EXISTS idx_messages_parent ON messages(parent_id);

CREATE TABLE IF NOT EXISTS users (
    handle      TEXT PRIMARY KEY,
    token_hash  TEXT NOT NULL,
    created_at  REAL NOT NULL
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
    max_tool_calls  INTEGER NOT NULL DEFAULT 3,
    tools           TEXT NOT NULL DEFAULT '{_DEFAULT_TOOLS_JSON}',
    job             TEXT NOT NULL DEFAULT '{DEFAULT_JOB}',
    status          TEXT NOT NULL DEFAULT 'idle'
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
"""

_DEFAULT_CHANNELS = [
    ("general", "wherever, whatever"),
    ("agents", "the swarm's own channel"),
]

_DEFAULT_AGENTS = [
    (
        "swarm",
        "You are `swarm`, an AI teammate embedded in a chat workspace. "
        "You're in the room like anyone else — terse, direct, no filler, "
        "no 'As an AI...' hedging. Answer the actual question. If you don't "
        "know something, say so in one line and move on. Keep replies short "
        "unless the question genuinely needs length. Do not call tools for "
        "greetings or small talk. Use remember for facts that should stick "
        "across turns.",
        DEFAULT_GROQ_MODEL,
        None,
        12,
        3,
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
        3,
        LEDGER_TOOLS,
        "Decision log",
    ),
]


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


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
    return [n for n in names if n in ALLOWED_TOOLS]


def public_agent(row: dict[str, Any]) -> dict[str, Any]:
    out = dict(row)
    out["tools"] = parse_tools(out.get("tools"))
    out["history_window"] = int(out.get("history_window") or 12)
    out["max_tool_calls"] = int(out.get("max_tool_calls") or 3)
    out["job"] = (out.get("job") or DEFAULT_JOB).strip() or DEFAULT_JOB
    out["status"] = out.get("status") or "idle"
    out["dm_channel_id"] = dm_channel_id(out["name"])
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
            "ALTER TABLE agents ADD COLUMN max_tool_calls INTEGER NOT NULL DEFAULT 3"
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
    for old, new in GROQ_MODEL_ALIASES.items():
        await db.execute("UPDATE agents SET model = ? WHERE model = ?", (new, old))

    cur = await db.execute("PRAGMA table_info(channels)")
    ch_cols = {row[1] for row in await cur.fetchall()}
    if "kind" not in ch_cols:
        await db.execute(
            "ALTER TABLE channels ADD COLUMN kind TEXT NOT NULL DEFAULT 'room'"
        )
    if "owner_agent" not in ch_cols:
        await db.execute("ALTER TABLE channels ADD COLUMN owner_agent TEXT")

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


async def init_db() -> None:
    async with aiosqlite.connect(DB_PATH) as db:
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
                    "created_at, history_window, max_tool_calls, tools, job, status) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'idle')",
                    (name, prompt, model, scope, time.time(), window, cap, json.dumps(tools), job),
                )
            await db.commit()
            await _ensure_schema(db)
        await db.commit()


# ------------------------------------------------------------- channels ---

async def list_channels() -> list[dict[str, Any]]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute("SELECT * FROM channels ORDER BY created_at")
        rows = await cur.fetchall()
        return [dict(r) for r in rows]


async def create_channel(
    channel_id: str,
    name: str,
    topic: str = "",
    *,
    kind: str = "room",
    owner_agent: str | None = None,
) -> dict[str, Any]:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO channels (id, name, topic, created_at, kind, owner_agent) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (channel_id, name, topic, time.time(), kind, owner_agent),
        )
        await db.commit()
    return {
        "id": channel_id, "name": name, "topic": topic,
        "kind": kind, "owner_agent": owner_agent,
    }


async def get_channel(channel_id: str) -> dict[str, Any] | None:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute("SELECT * FROM channels WHERE id = ?", (channel_id,))
        row = await cur.fetchone()
        return dict(row) if row else None


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
) -> dict[str, Any]:
    ts = time.time()
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "INSERT INTO messages (channel_id, parent_id, author, author_kind, body, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (channel_id, parent_id, author, author_kind, body, ts),
        )
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


async def get_history_after(channel_id: str, after_id: int) -> list[dict[str, Any]]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            "SELECT * FROM messages WHERE channel_id = ? AND id > ? ORDER BY id ASC",
            (channel_id, after_id),
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


async def search_history(channel_id: str, query: str, limit: int = 10) -> list[dict[str, Any]]:
    """Naive substring search. Phase 6 (vector search) replaces this
    implementation only — the tool signature in agent.py stays the same."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            "SELECT * FROM messages WHERE channel_id = ? AND body LIKE ? "
            "ORDER BY created_at DESC LIMIT ?",
            (channel_id, f"%{query}%", limit),
        )
        rows = await cur.fetchall()
        return [dict(r) for r in reversed(rows)]


async def message_exists(message_id: int) -> bool:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT 1 FROM messages WHERE id = ?", (message_id,))
        return await cur.fetchone() is not None


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


async def get_reactions(message_id: int) -> list[dict[str, Any]]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            "SELECT author, emoji, created_at FROM reactions WHERE message_id = ?",
            (message_id,),
        )
        rows = await cur.fetchall()
        return [dict(r) for r in rows]


# ------------------------------------------------------------------ users -

async def create_user(handle: str, token: str) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO users (handle, token_hash, created_at) VALUES (?, ?, ?)",
            (handle, hash_token(token), time.time()),
        )
        await db.commit()


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


# ----------------------------------------------------------------- agents -

async def list_agents(channel_id: str | None = None) -> list[dict[str, Any]]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute("SELECT * FROM agents ORDER BY created_at")
        rows = await cur.fetchall()
        agents = [public_agent(dict(r)) for r in rows]
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
    max_tool_calls: int = 3,
    tools: list[str] | None = None,
    job: str = DEFAULT_JOB,
) -> dict[str, Any]:
    tool_names = tools if tools is not None else list(DEFAULT_TOOLS)
    job_title = (job or DEFAULT_JOB).strip() or DEFAULT_JOB
    model = resolve_groq_model(model)
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO agents (name, system_prompt, model, channel_scope, "
            "created_at, history_window, max_tool_calls, tools, job, status) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'idle')",
            (
                name, system_prompt, model, channel_scope, time.time(),
                history_window, max_tool_calls, json.dumps(tool_names), job_title,
            ),
        )
        await db.commit()
        await _ensure_schema(db)
        await db.commit()
    return {
        "name": name,
        "system_prompt": system_prompt,
        "model": model,
        "channel_scope": channel_scope,
        "history_window": history_window,
        "max_tool_calls": max_tool_calls,
        "tools": tool_names,
        "job": job_title,
        "status": "idle",
        "dm_channel_id": dm_channel_id(name),
    }


async def update_agent(name: str, fields: dict[str, Any]) -> dict[str, Any] | None:
    if not fields:
        return await get_agent(name)
    allowed = {
        "system_prompt", "model", "channel_scope",
        "history_window", "max_tool_calls", "tools", "job", "status",
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
        if channel_id:
            cur = await db.execute(
                "SELECT * FROM agent_memory WHERE agent_name = ? AND kind = 'note' "
                "AND (channel_id IS NULL OR channel_id = ?) AND body LIKE ? "
                "ORDER BY created_at DESC LIMIT ?",
                (agent_name, channel_id, f"%{query}%", limit),
            )
        else:
            cur = await db.execute(
                "SELECT * FROM agent_memory WHERE agent_name = ? AND kind = 'note' "
                "AND body LIKE ? ORDER BY created_at DESC LIMIT ?",
                (agent_name, f"%{query}%", limit),
            )
        rows = await cur.fetchall()
        return [dict(r) for r in reversed(rows)]


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
    routine_id: int, *, status: str, excerpt: str, interval_minutes: int
) -> None:
    ts = time.time()
    next_run = ts + interval_minutes * 60
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO routine_runs (routine_id, started_at, finished_at, status, excerpt) "
            "VALUES (?, ?, ?, ?, ?)",
            (routine_id, ts, ts, status, excerpt[:500]),
        )
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
    row = await get_approval(approval_id)
    if row is None:
        return None
    ts = time.time()
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE approvals SET status = ?, resolved_at = ? WHERE id = ?",
            (status, ts, approval_id),
        )
        await db.commit()
    row["status"] = status
    row["resolved_at"] = ts
    return row
