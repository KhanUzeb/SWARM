"""Hybrid memory + knowledge-graph helpers (plan §15-16).

Adds source/confidence/recency metadata and a tiny entity-relation
store on top of the existing notes/FTS memory. SQLite tables are
created lazily so no global migration is required.
"""
from __future__ import annotations

import time
from typing import Any

import aiosqlite

from . import db

SCHEMA = """
CREATE TABLE IF NOT EXISTS memory_meta (
    memory_id INTEGER PRIMARY KEY, source TEXT NOT NULL DEFAULT 'agent',
    confidence REAL NOT NULL DEFAULT 0.5, scope TEXT NOT NULL DEFAULT 'agent',
    owner TEXT NOT NULL DEFAULT '', sensitivity TEXT NOT NULL DEFAULT 'internal',
    created_at REAL NOT NULL, last_confirmed REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS knowledge_relations (
    id INTEGER PRIMARY KEY AUTOINCREMENT, src TEXT NOT NULL, rel TEXT NOT NULL,
    dst TEXT NOT NULL, metadata TEXT NOT NULL DEFAULT '{}', created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_relations_src ON knowledge_relations(src);
CREATE INDEX IF NOT EXISTS idx_relations_dst ON knowledge_relations(dst);
"""


async def init_tables() -> None:
    async with aiosqlite.connect(db.DB_PATH) as conn:
        await conn.executescript(SCHEMA)
        await conn.commit()


def memory_record(
    memory_id: int,
    *,
    source: str = "agent",
    confidence: float = 0.5,
    scope: str = "agent",
    owner: str = "",
    sensitivity: str = "internal",
    now: float | None = None,
) -> dict[str, Any]:
    ts = now if now is not None else time.time()
    return {
        "memory_id": memory_id,
        "source": source,
        "confidence": max(0.0, min(1.0, float(confidence))),
        "scope": scope,
        "owner": owner,
        "sensitivity": sensitivity,
        "created_at": ts,
        "last_confirmed": ts,
    }


async def save_memory_meta(record: dict[str, Any]) -> dict[str, Any]:
    await init_tables()
    async with aiosqlite.connect(db.DB_PATH) as conn:
        await conn.execute(
            "INSERT OR REPLACE INTO memory_meta(memory_id, source, confidence, scope, owner, sensitivity, created_at, last_confirmed)"
            " VALUES(?,?,?,?,?,?,?,?)",
            (
                int(record["memory_id"]), str(record.get("source", "agent")),
                float(record.get("confidence", 0.5)), str(record.get("scope", "agent")),
                str(record.get("owner", "")), str(record.get("sensitivity", "internal")),
                float(record.get("created_at", time.time())),
                float(record.get("last_confirmed", time.time())),
            ),
        )
        await conn.commit()
    return record


async def add_relation(src: str, rel: str, dst: str, metadata: dict[str, Any] | None = None) -> dict[str, Any]:
    await init_tables()
    import json

    now = time.time()
    async with aiosqlite.connect(db.DB_PATH) as conn:
        cur = await conn.execute(
            "INSERT INTO knowledge_relations(src, rel, dst, metadata, created_at) VALUES(?,?,?,?,?)",
            (src, rel, dst, json.dumps(metadata or {}), now),
        )
        await conn.commit()
        relation_id = cur.lastrowid
    return {"id": relation_id, "src": src, "rel": rel, "dst": dst, "metadata": metadata or {}, "created_at": now}


async def related(entity: str, limit: int = 25) -> list[dict[str, Any]]:
    await init_tables()
    async with aiosqlite.connect(db.DB_PATH) as conn:
        conn.row_factory = aiosqlite.Row
        cur = await conn.execute(
            "SELECT * FROM knowledge_relations WHERE src = ? OR dst = ? ORDER BY created_at DESC LIMIT ?",
            (entity, entity, max(1, min(limit, 100))),
        )
        rows = [dict(r) for r in await cur.fetchall()]
    return rows


def rank_candidates(query: str, candidates: list[dict[str, Any]], limit: int = 10) -> list[dict[str, Any]]:
    """Lexical + recency + trust ranker with a retrieval budget."""
    import json

    terms = {t for t in query.lower().split() if t}
    scored = []
    now = time.time()
    for cand in candidates:
        text = json.dumps(cand).lower()
        lexical = sum(1 for t in terms if t in text) / max(1, len(terms))
        age_days = max(0.0, (now - float(cand.get("created_at") or now)) / 86400.0)
        recency = 1.0 / (1.0 + age_days / 30.0)
        trust = float(cand.get("confidence", 0.5))
        score = 0.5 * lexical + 0.3 * recency + 0.2 * trust
        scored.append((score, cand))
    scored.sort(key=lambda item: item[0], reverse=True)
    return [cand for _, cand in scored[: max(1, min(limit, 50))]]
