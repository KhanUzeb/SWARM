"""
swarm.db — thin async wrapper over SQLite.

No ORM. If this needs to outgrow SQLite, that's a good problem to have
and a different file.
"""
from __future__ import annotations

import hashlib
import os
import secrets
import time
from pathlib import Path
from typing import Any

import aiosqlite

DB_PATH = Path(os.environ.get("SWARM_DB_PATH", str(Path(__file__).parent / "swarm.db")))

SCHEMA = """
CREATE TABLE IF NOT EXISTS channels (
    id          TEXT PRIMARY KEY,
    name        TEXT UNIQUE NOT NULL,
    topic       TEXT DEFAULT '',
    created_at  REAL NOT NULL
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
    created_at      REAL NOT NULL
);
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
        "greetings or small talk.",
        "llama-3.3-70b-versatile",
        None,
    ),
    (
        "ledger",
        "You are `ledger`. You don't chat, you record. When mentioned, "
        "summarize the decisions and open questions from the recent "
        "channel history in a short bulleted list — nothing else. If "
        "there's nothing decision-shaped in the recent history, say so "
        "in one line.",
        "llama-3.3-70b-versatile",
        None,
    ),
]


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


async def init_db() -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.executescript(SCHEMA)
        await db.commit()

        cur = await db.execute("SELECT COUNT(*) FROM channels")
        (count,) = await cur.fetchone()
        if count == 0:
            for name, topic in _DEFAULT_CHANNELS:
                await db.execute(
                    "INSERT INTO channels (id, name, topic, created_at) VALUES (?, ?, ?, ?)",
                    (name, name, topic, time.time()),
                )

        cur = await db.execute("SELECT COUNT(*) FROM agents")
        (count,) = await cur.fetchone()
        if count == 0:
            for name, prompt, model, scope in _DEFAULT_AGENTS:
                await db.execute(
                    "INSERT INTO agents (name, system_prompt, model, channel_scope, created_at) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (name, prompt, model, scope, time.time()),
                )
        await db.commit()


# ------------------------------------------------------------- channels ---

async def list_channels() -> list[dict[str, Any]]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute("SELECT * FROM channels ORDER BY created_at")
        rows = await cur.fetchall()
        return [dict(r) for r in rows]


async def create_channel(channel_id: str, name: str, topic: str = "") -> dict[str, Any]:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO channels (id, name, topic, created_at) VALUES (?, ?, ?, ?)",
            (channel_id, name, topic, time.time()),
        )
        await db.commit()
    return {"id": channel_id, "name": name, "topic": topic}


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
        agents = [dict(r) for r in rows]
    if channel_id is None:
        return agents
    return [a for a in agents if a["channel_scope"] in (None, channel_id)]


async def create_agent(name: str, system_prompt: str, model: str, channel_scope: str | None) -> dict[str, Any]:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO agents (name, system_prompt, model, channel_scope, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (name, system_prompt, model, channel_scope, time.time()),
        )
        await db.commit()
    return {"name": name, "system_prompt": system_prompt, "model": model, "channel_scope": channel_scope}


async def agent_exists(name: str) -> bool:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT 1 FROM agents WHERE name = ?", (name,))
        return await cur.fetchone() is not None
