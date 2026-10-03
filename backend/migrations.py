"""
Versioned schema migrations.

``ensure_schema()`` in ``db.py`` is the original hand-written additive
path — PRAGMA table_info plus ALTER TABLE. It stays exactly as it is,
because it is what upgrades every database that already exists on a
maintainer's disk. This module is layered on top of it for anything that
needs a recorded version: a migration here is skipped once it has run,
so it never re-derives state the additive path already handles.

Deliberately not a framework. There is no dependency graph, no down-
migration, no ORM, and no dynamic SQL generation. A migration is a
numbered tuple of the statements it runs, so the whole change is
readable in one sitting and the version number is the only state the
runner owns.

Version bookkeeping lives in ``schema_migrations``, named for this
module's own vocabulary. Adding a migration means appending one tuple to
``_MIGRATIONS`` with a version above every existing one — never editing
a released migration, because databases in the field have already run it.
"""

from __future__ import annotations

import time
from typing import Any

import aiosqlite

SCHEMA_MIGRATIONS_TABLE = "schema_migrations"


# Ordered list of (version, name, statements). Append only.
_MIGRATIONS: tuple[tuple[int, str, tuple[str, ...]], ...] = (
    (
        1,
        "hot-path message indexes",
        (
            # get_history_after() — the WebSocket handshake catch-up:
            #   SELECT * FROM messages WHERE channel_id = ? AND id > ?
            #   ORDER BY id ASC LIMIT ?
            # idx_messages_channel leads with created_at, so answering an
            # id-ordered range meant sorting the entire channel first.
            "CREATE INDEX IF NOT EXISTS idx_messages_channel_id "
            "ON messages(channel_id, id)",
            # search_workspace():
            #   ... WHERE m.body LIKE ? ORDER BY m.created_at DESC, m.id DESC
            # The LIKE cannot use an index, but walking this one backwards
            # lets the LIMIT stop at the newest rows instead of sorting the
            # whole table on every keystroke of the search box.
            "CREATE INDEX IF NOT EXISTS idx_messages_created_at "
            "ON messages(created_at, id)",
            # get_recent_system_messages() — the dashboard activity feed:
            #   SELECT * FROM messages WHERE author_kind = 'system'
            #   ORDER BY id DESC LIMIT ?
            # Partial, so the index only carries tool-call audit rows
            # instead of a second copy of the whole conversation table.
            "CREATE INDEX IF NOT EXISTS idx_messages_system "
            "ON messages(id) WHERE author_kind = 'system'",
            # Deliberately NOT added: an index on approvals(status,
            # channel_id). idx_approvals_pending already has that exact
            # shape and covers list_approvals(), which always filters on
            # status first.
        ),
    ),
    (
        2,
        "routine run index",
        (
            # list_routine_runs():
            #   SELECT * FROM routine_runs WHERE routine_id = ?
            #   ORDER BY id DESC LIMIT ?
            # routine_runs was the one unbounded table with no index: run
            # history grew forever and every poll scanned it all.
            "CREATE INDEX IF NOT EXISTS idx_routine_runs_routine "
            "ON routine_runs(routine_id, id)",
        ),
    ),
)

LATEST_VERSION = max(version for version, _name, _stmts in _MIGRATIONS)


async def applied_versions(db: aiosqlite.Connection) -> set[int]:
    """Versions recorded in schema_migrations (empty when the table is new)."""
    cur = await db.execute(f"SELECT 1 FROM sqlite_master WHERE type='table' "
                           f"AND name = '{SCHEMA_MIGRATIONS_TABLE}'")
    if await cur.fetchone() is None:
        return set()
    cur = await db.execute(f"SELECT version FROM {SCHEMA_MIGRATIONS_TABLE}")
    return {int(row[0]) for row in await cur.fetchall()}


async def apply_migrations(db: aiosqlite.Connection) -> list[int]:
    """Apply every migration newer than the recorded version.

    Each migration runs in its own transaction that also records its
    version, so a failure part-way through leaves the version unrecorded
    and the database on the previous one — the next boot retries cleanly
    rather than resuming half a change.

    Must be called with no transaction open (init_db() commits the
    additive path first). Raises on a failed migration: a schema that
    half-upgraded is worse than a server that refuses to start.
    """
    await db.execute(
        f"CREATE TABLE IF NOT EXISTS {SCHEMA_MIGRATIONS_TABLE} ("
        "version INTEGER PRIMARY KEY, "
        "name TEXT NOT NULL DEFAULT '', "
        "applied_at REAL NOT NULL)"
    )
    done = await applied_versions(db)
    ran: list[int] = []
    for version, name, statements in _MIGRATIONS:
        if version in done:
            continue
        await db.execute("BEGIN")
        try:
            for statement in statements:
                await db.execute(statement)
            await db.execute(
                f"INSERT INTO {SCHEMA_MIGRATIONS_TABLE} (version, name, applied_at) "
                "VALUES (?, ?, ?)",
                (version, name, time.time()),
            )
        except Exception:
            await db.rollback()
            raise
        await db.commit()
        ran.append(version)
    return ran


def migration_catalog() -> list[dict[str, Any]]:
    """What the runner would do, for /health-style introspection and tests."""
    return [{"version": v, "name": n, "statements": list(s)} for v, n, s in _MIGRATIONS]
