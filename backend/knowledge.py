"""Personal + agent knowledge base backed by SQLite FTS5 (LIKE fallback).

Docs are owned by a user handle (`owner="uzeb"`) or by an agent
(`owner="agent:swarm"`) and optionally scoped to a channel. Agents can
save what they learn with `knowledge_save` and look it up with
`knowledge_search`; the context builder injects top hits into replies so
the knowledge base improves everyday answers instead of sitting unused.
"""
from __future__ import annotations

import time
import uuid
from typing import Any

import aiosqlite

from . import db

SCHEMA = """
CREATE TABLE IF NOT EXISTS knowledge_docs (
    id TEXT PRIMARY KEY, owner TEXT NOT NULL, channel_id TEXT,
    title TEXT NOT NULL DEFAULT '', body TEXT NOT NULL DEFAULT '',
    tags TEXT NOT NULL DEFAULT '', source TEXT NOT NULL DEFAULT 'manual',
    created_at REAL NOT NULL, updated_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_knowledge_owner ON knowledge_docs(owner, updated_at);
CREATE INDEX IF NOT EXISTS idx_knowledge_channel ON knowledge_docs(channel_id, updated_at);
"""

_fts_available: bool | None = None


async def init_db() -> None:
    global _fts_available
    async with aiosqlite.connect(db.DB_PATH) as conn:
        await conn.executescript(SCHEMA)
        try:
            await conn.execute(
                "CREATE VIRTUAL TABLE IF NOT EXISTS knowledge_fts "
                "USING fts5(title, body, tags, content='knowledge_docs', content_rowid='rowid')"
            )
            await conn.execute(
                "CREATE TRIGGER IF NOT EXISTS knowledge_fts_insert AFTER INSERT ON knowledge_docs "
                "BEGIN INSERT INTO knowledge_fts(rowid, title, body, tags) "
                "VALUES (new.rowid, new.title, new.body, new.tags); END"
            )
            await conn.execute(
                "CREATE TRIGGER IF NOT EXISTS knowledge_fts_delete AFTER DELETE ON knowledge_docs "
                "BEGIN INSERT INTO knowledge_fts(knowledge_fts, rowid, title, body, tags) "
                "VALUES ('delete', old.rowid, old.title, old.body, old.tags); END"
            )
            await conn.execute(
                "CREATE TRIGGER IF NOT EXISTS knowledge_fts_update AFTER UPDATE ON knowledge_docs "
                "BEGIN INSERT INTO knowledge_fts(knowledge_fts, rowid, title, body, tags) "
                "VALUES ('delete', old.rowid, old.title, old.body, old.tags); "
                "INSERT INTO knowledge_fts(rowid, title, body, tags) "
                "VALUES (new.rowid, new.title, new.body, new.tags); END"
            )
            # Backfill rows written before the FTS table existed.
            await conn.execute(
                "INSERT INTO knowledge_fts(rowid, title, body, tags) "
                "SELECT rowid, title, body, tags FROM knowledge_docs "
                "WHERE rowid NOT IN (SELECT rowid FROM knowledge_fts)"
            )
            await conn.commit()
            _fts_available = True
        except Exception:  # noqa: BLE001 — SQLite without FTS5 falls back to LIKE
            await conn.commit()
            _fts_available = False


async def fts_available() -> bool:
    if _fts_available is None:
        await init_db()
    return bool(_fts_available)


def _row(row: aiosqlite.Row | None) -> dict[str, Any] | None:
    return dict(row) if row else None


async def create_doc(
    owner: str,
    title: str,
    body: str,
    *,
    tags: str = "",
    channel_id: str | None = None,
    source: str = "manual",
) -> dict[str, Any]:
    title = (title or "").strip()[:200] or "Untitled"
    body = (body or "").strip()
    if not body:
        raise ValueError("knowledge body cannot be empty")
    if len(body) > 32_000:
        raise ValueError("knowledge body cap is 32000 characters")
    doc_id = f"kb_{uuid.uuid4().hex[:12]}"
    now = time.time()
    async with aiosqlite.connect(db.DB_PATH) as conn:
        await conn.execute(
            "INSERT INTO knowledge_docs(id, owner, channel_id, title, body, tags, source,"
            " created_at, updated_at) VALUES(?,?,?,?,?,?,?,?,?)",
            (doc_id, owner, channel_id, title, body[:32_000],
             (tags or "")[:500], source, now, now),
        )
        await conn.commit()
    row = await get_doc(doc_id, owner)
    assert row is not None
    return row


async def get_doc(doc_id: str, owner: str) -> dict[str, Any] | None:
    async with aiosqlite.connect(db.DB_PATH) as conn:
        conn.row_factory = aiosqlite.Row
        cur = await conn.execute(
            "SELECT * FROM knowledge_docs WHERE id = ? AND owner = ?", (doc_id, owner))
        return _row(await cur.fetchone())


async def list_docs(owner: str, limit: int = 50) -> list[dict[str, Any]]:
    async with aiosqlite.connect(db.DB_PATH) as conn:
        conn.row_factory = aiosqlite.Row
        cur = await conn.execute(
            "SELECT * FROM knowledge_docs WHERE owner = ? ORDER BY updated_at DESC LIMIT ?",
            (owner, min(max(limit, 1), 100)),
        )
        return [dict(r) for r in await cur.fetchall()]


async def update_doc(doc_id: str, owner: str, patch: dict[str, Any]) -> dict[str, Any] | None:
    current = await get_doc(doc_id, owner)
    if not current:
        return None
    title = str(patch.get("title", current["title"]))[:200] or current["title"]
    body = str(patch.get("body", current["body"]))[:32_000]
    tags = str(patch.get("tags", current["tags"]))[:500]
    now = time.time()
    async with aiosqlite.connect(db.DB_PATH) as conn:
        await conn.execute(
            "UPDATE knowledge_docs SET title=?, body=?, tags=?, updated_at=? WHERE id=? AND owner=?",
            (title, body, tags, now, doc_id, owner),
        )
        await conn.commit()
    return await get_doc(doc_id, owner)


async def delete_doc(doc_id: str, owner: str) -> bool:
    async with aiosqlite.connect(db.DB_PATH) as conn:
        cur = await conn.execute(
            "DELETE FROM knowledge_docs WHERE id = ? AND owner = ?", (doc_id, owner))
        await conn.commit()
        return cur.rowcount > 0


async def count_docs(owner: str) -> int:
    async with aiosqlite.connect(db.DB_PATH) as conn:
        cur = await conn.execute("SELECT COUNT(*) FROM knowledge_docs WHERE owner = ?", (owner,))
        (count,) = await cur.fetchone()
        return count


def _fts_query(query: str) -> str:
    """Quote individual terms so user input can't break MATCH syntax."""
    terms = [t.strip('"') for t in query.split() if t.strip('"')]
    if not terms:
        return '""'
    return " OR ".join(f'"{t[:60]}"' for t in terms[:10])


async def search_docs(
    query: str,
    *,
    owners: list[str] | None = None,
    channel_id: str | None = None,
    limit: int = 5,
) -> list[dict[str, Any]]:
    """Search across owner scopes, optionally boosting a channel scope.

    `owners` restricts whose docs are visible (personal handle and/or
    `agent:<name>`). When `channel_id` is given, docs scoped to that
    channel sort first.
    """
    needle = (query or "").strip()
    if not needle:
        return []
    limit = min(max(limit, 1), 20)
    clauses = []
    params: list[Any] = []
    if owners:
        clauses.append(f"owner IN ({','.join('?' for _ in owners)})")
        params.extend(owners)
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    async with aiosqlite.connect(db.DB_PATH) as conn:
        conn.row_factory = aiosqlite.Row
        rows: list[dict[str, Any]] = []
        if await fts_available():
            try:
                cur = await conn.execute(
                    "SELECT d.* FROM knowledge_docs d JOIN knowledge_fts f ON f.rowid = d.rowid "
                    f"WHERE knowledge_fts MATCH ? {'AND ' + ' AND '.join(clauses) if clauses else ''} "
                    "ORDER BY rank LIMIT ?",
                    [_fts_query(needle), *params, limit * 2],
                )
                rows = [dict(r) for r in await cur.fetchall()]
            except Exception:  # noqa: BLE001 — fall through to LIKE
                rows = []
        if not rows:
            like = f"%{needle}%"
            cur = await conn.execute(
                f"SELECT * FROM knowledge_docs {where}"
                f"{' AND' if where else 'WHERE'} (title LIKE ? OR body LIKE ? OR tags LIKE ?) "
                "ORDER BY updated_at DESC LIMIT ?",
                [*params, like, like, like, limit * 2],
            )
            rows = [dict(r) for r in await cur.fetchall()]
    if channel_id:
        rows.sort(key=lambda r: (r.get("channel_id") != channel_id, -(r.get("updated_at") or 0)))
    else:
        rows.sort(key=lambda r: -(r.get("updated_at") or 0))
    return rows[:limit]
