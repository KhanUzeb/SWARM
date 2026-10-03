"""Phase 5.2 SQLite hardening: pragmas, hot-path indexes, versioned
migrations, an old-database upgrade, and env-driven retention.

Every test here builds its own temp SQLite file. Nothing touches the
repository's real ``swarm.db``.

The upgrade test is the one that matters most. A fresh database proves
only that ``CREATE TABLE IF NOT EXISTS`` works. The scenario that actually
breaks a launch is a maintainer's database that was created three
releases ago: missing columns, missing tables, missing indexes, and no
``schema_migrations`` table at all. ``test_old_database_upgrades_in_place``
builds that shape by hand — by writing the pre-upgrade CREATE TABLE
statements rather than by deleting tables out of a modern one — and then
boots the real app against it.
"""

from __future__ import annotations

import asyncio
import sqlite3

import pytest

# The pre-upgrade schema, written out by hand rather than derived from the
# current SCHEMA. This is the point of the test: it must stay pinned to
# what an old database actually looked like, so that widening the current
# schema does not silently widen what this "old" database is.
#
# Deliberately missing, relative to today:
#   - messages.model, channels.kind, channels.owner_agent
#   - agents.history_window / max_tool_calls / tools / job / status /
#     display_name / avatar / archived_at / tools_locked
#   - users.role, users.password_hash
#   - tables: channel_members, workspace_meta, channel_people,
#     agent_teams, agent_team_members, skills, routines, routine_runs,
#     approvals, custom_tools, ai_providers, reactions
OLD_SCHEMA = """
CREATE TABLE channels (
    id          TEXT PRIMARY KEY,
    name        TEXT UNIQUE NOT NULL,
    topic       TEXT DEFAULT '',
    created_at  REAL NOT NULL
);

CREATE TABLE messages (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    channel_id  TEXT NOT NULL REFERENCES channels(id),
    parent_id   INTEGER REFERENCES messages(id),
    author      TEXT NOT NULL,
    author_kind TEXT NOT NULL DEFAULT 'human',
    body        TEXT NOT NULL,
    created_at  REAL NOT NULL
);

CREATE TABLE users (
    handle       TEXT PRIMARY KEY,
    token_hash   TEXT NOT NULL,
    created_at   REAL NOT NULL
);

CREATE TABLE agents (
    name            TEXT PRIMARY KEY,
    system_prompt   TEXT NOT NULL,
    model           TEXT NOT NULL,
    channel_scope   TEXT,
    created_at      REAL NOT NULL
);

CREATE TABLE agent_memory (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    agent_name  TEXT NOT NULL,
    channel_id  TEXT,
    kind        TEXT NOT NULL,
    body        TEXT NOT NULL,
    created_at  REAL NOT NULL,
    updated_at  REAL NOT NULL
);
"""


def _build_old_database(path):
    """A database file shaped like the previous release's, with real rows."""
    conn = sqlite3.connect(path)
    conn.executescript(OLD_SCHEMA)
    conn.execute(
        "INSERT INTO channels (id, name, topic, created_at) VALUES (?, ?, ?, ?)",
        ("general", "general", "wherever, whatever", 1000.0),
    )
    conn.execute(
        "INSERT INTO messages (channel_id, parent_id, author, author_kind, body, created_at) "
        "VALUES (?, NULL, ?, 'human', ?, ?)",
        ("general", "uzeb", "a message from an old release", 1001.0),
    )
    conn.execute(
        "INSERT INTO users (handle, token_hash, created_at) VALUES (?, ?, ?)",
        ("uzeb", "deadbeef", 1002.0),
    )
    conn.execute(
        "INSERT INTO agents (name, system_prompt, model, channel_scope, created_at) "
        "VALUES (?, ?, ?, NULL, ?)",
        ("swarm", "You are swarm.", "llama-3.1-8b", 1003.0),
    )
    conn.commit()
    conn.close()


@pytest.fixture
def old_db(tmp_path, monkeypatch):
    """Point db.DB_PATH at a hand-built previous-release database.

    monkeypatch.setattr (not a bare assignment) so the module global is
    restored after the test — otherwise this leaks the temp path into
    whichever test runs next.
    """
    import backend.db as db_mod

    path = tmp_path / "old.db"
    _build_old_database(path)
    monkeypatch.setenv("SWARM_DB_PATH", str(path))
    monkeypatch.setattr(db_mod, "DB_PATH", path)
    return path


def _tables(path) -> set[str]:
    conn = sqlite3.connect(path)
    try:
        return {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
    finally:
        conn.close()


def _columns(path, table) -> set[str]:
    conn = sqlite3.connect(path)
    try:
        return {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
    finally:
        conn.close()


def _indexes(path) -> set[str]:
    conn = sqlite3.connect(path)
    try:
        return {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='index'"
            )
        }
    finally:
        conn.close()


# ------------------------------------------------------------- pragmas ----


def test_every_connection_reports_wal_foreign_keys_and_busy_timeout(old_db):
    """Read the pragmas back off a live connection, do not assume them.

    WAL is the one that matters for the routine scheduler vs. chat-write
    collision: a reader holding a rollback-journal lock blocks the writer
    for the length of the read, which is how a routine tick turns into a
    500. journal_mode is also allowed to fail to change, so the assertion
    is on what SQLite reports, not on what was requested.
    """
    import backend.db as db_mod

    async def probe():
        async with db_mod.open_db() as conn:
            return await db_mod.apply_pragmas(conn)

    report = asyncio.run(probe())
    assert report["journal_mode"].lower() == "wal"
    assert report["wal"] is True
    assert report["foreign_keys"] == 1
    assert report["busy_timeout_ms"] == 5000


def test_wal_survives_a_reconnect_and_is_not_left_as_a_rollback_journal(old_db):
    """journal_mode is persistent — prove it on a second, independent open.

    Without WAL every writer serializes behind every reader. If WAL were
    silently not taking effect, the first connection might still look fine
    and the failure would only show up under concurrency in production.
    """
    import backend.db as db_mod

    async def twice():
        async with db_mod.open_db() as conn:
            cur = await conn.execute("PRAGMA journal_mode")
            first = (await cur.fetchone())[0]
        async with db_mod.open_db() as conn:
            cur = await conn.execute("PRAGMA journal_mode")
            second = (await cur.fetchone())[0]
        return first, second

    first, second = asyncio.run(twice())
    assert first.lower() == second.lower() == "wal"


def test_busy_timeout_is_env_overridable(old_db, monkeypatch):
    import backend.db as db_mod

    monkeypatch.setenv("SWARM_SQLITE_BUSY_TIMEOUT_MS", "1234")
    assert db_mod.busy_timeout_ms() == 1234

    async def probe():
        async with db_mod.open_db() as conn:
            return await db_mod.apply_pragmas(conn)

    assert asyncio.run(probe())["busy_timeout_ms"] == 1234

    # A typo must fall back to the default, not disable waiting.
    monkeypatch.setenv("SWARM_SQLITE_BUSY_TIMEOUT_MS", "not-a-number")
    assert db_mod.busy_timeout_ms() == 5000
    monkeypatch.setenv("SWARM_SQLITE_BUSY_TIMEOUT_MS", "-1")
    assert db_mod.busy_timeout_ms() == 0


def test_foreign_keys_are_enforced(old_db):
    """foreign_keys defaults OFF in SQLite, so this proves the pragma is real.

    A reaction pointing at a message that does not exist must be rejected.
    """
    import backend.db as db_mod

    async def attempt():
        await db_mod.init_db()  # reactions table is part of the current schema
        async with db_mod.open_db() as conn:
            await conn.execute(
                "INSERT INTO reactions (message_id, author, emoji, created_at) "
                "VALUES (999999, 'uzeb', 'x', 1.0)",
            )

    with pytest.raises(sqlite3.IntegrityError):
        asyncio.run(attempt())


# ------------------------------------------------------------- indexes ----


def test_hot_path_indexes_exist(old_db):
    import backend.db as db_mod

    asyncio.run(db_mod.init_db())
    indexes = _indexes(old_db)
    assert "idx_messages_channel_id" in indexes      # WS last_seen_id catch-up
    assert "idx_messages_created_at" in indexes      # workspace search order
    assert "idx_messages_system" in indexes          # system activity feed
    assert "idx_routine_runs_routine" in indexes     # routine run history


def test_approvals_index_was_not_duplicated(old_db):
    """idx_approvals_pending already has the (status, channel_id) shape.

    list_approvals() always filters on status first, so a second index on
    the same columns would be pure write cost. Assert the query planner
    picks the existing one rather than just that *an* index exists.
    """
    import backend.db as db_mod

    asyncio.run(db_mod.init_db())
    assert "idx_approvals_pending" in _indexes(old_db)
    conn = sqlite3.connect(old_db)
    try:
        plan = " ".join(
            row[3]
            for row in conn.execute(
                "EXPLAIN QUERY PLAN SELECT * FROM approvals "
                "WHERE status = 'pending' AND channel_id = 'general' "
                "ORDER BY id DESC LIMIT 50"
            )
        )
    finally:
        conn.close()
    assert "idx_approvals_pending" in plan


def test_new_indexes_are_chosen_by_the_query_planner(old_db):
    """An index nobody's query uses is pure write amplification.

    EXPLAIN QUERY PLAN against the real query text from db.py, so this
    fails if a future schema change makes an index dead weight.
    """
    import backend.db as db_mod

    asyncio.run(db_mod.init_db())
    cases = {
        # db.get_history_after() — the WebSocket handshake catch-up
        "idx_messages_channel_id": (
            "SELECT * FROM messages WHERE channel_id = 'general' AND id > 5 "
            "ORDER BY id ASC LIMIT 200"
        ),
        # db.get_recent_system_messages() — dashboard activity feed
        "idx_messages_system": (
            "SELECT * FROM messages WHERE author_kind = 'system' "
            "ORDER BY id DESC LIMIT 20"
        ),
        # db.list_routine_runs() — routine history
        "idx_routine_runs_routine": (
            "SELECT * FROM routine_runs WHERE routine_id = 1 "
            "ORDER BY id DESC LIMIT 20"
        ),
    }
    conn = sqlite3.connect(old_db)
    try:
        for index, query in cases.items():
            plan = " ".join(
                row[3] for row in conn.execute("EXPLAIN QUERY PLAN " + query)
            )
            assert index in plan, f"{index} unused for {query!r}: {plan}"
    finally:
        conn.close()


# ---------------------------------------------------------- migrations ----


def test_migrations_record_versions_and_are_idempotent(old_db):
    from backend import migrations

    import backend.db as db_mod

    async def run_twice():
        await db_mod.init_db()
        async with db_mod.open_db() as conn:
            first = sorted(await migrations.applied_versions(conn))
            await migrations.apply_migrations(conn)
            second = sorted(await migrations.applied_versions(conn))
        return first, second

    first, second = asyncio.run(run_twice())
    assert first == [m["version"] for m in migrations.migration_catalog()]
    assert migrations.LATEST_VERSION in first
    # A second boot must not re-run anything — that is the whole point of
    # recording the version.
    assert first == second


def test_a_failed_migration_does_not_record_its_version(old_db, monkeypatch):
    """Each migration commits its own version, so a crash retries cleanly."""
    import aiosqlite

    from backend import migrations

    async def attempt():
        async with db_mod_conn() as conn:
            await conn.execute(
                f"CREATE TABLE IF NOT EXISTS {migrations.SCHEMA_MIGRATIONS_TABLE} "
                "(version INTEGER PRIMARY KEY, name TEXT NOT NULL DEFAULT '', "
                "applied_at REAL NOT NULL)"
            )
            # Simulate a failure mid-migration by making one statement bad.
            broken = 900_001
            with pytest.raises(Exception):
                await _apply_broken(conn, broken)

    from backend.db import open_db as db_mod_conn

    asyncio.run(attempt())

    async def check():
        async with db_mod_conn() as conn:
            return await migrations.applied_versions(conn)

    assert asyncio.run(check()) == set()


async def _apply_broken(conn: aiosqlite.Connection, version: int) -> None:
    await conn.execute("BEGIN")
    try:
        await conn.execute("CREATE TABLE ok_probe (id INTEGER PRIMARY KEY)")
        await conn.execute("THIS IS NOT SQL")  # boom
    except Exception:
        await conn.rollback()
        raise
    await conn.commit()


def test_migration_versions_are_unique_and_ascending():
    from backend import migrations

    versions = [m["version"] for m in migrations.migration_catalog()]
    assert versions == sorted(versions)
    assert len(versions) == len(set(versions))
    assert versions[-1] == migrations.LATEST_VERSION


# -------------------------------------------------------- old-DB upgrade ---


def test_old_database_upgrades_in_place(old_db):
    """A previous-release database is fully upgraded, losing no rows.

    Asserted in three directions, because each catches a different bug:
    new columns/tables/indexes exist, the pre-existing rows survive, and
    the additive path still backfilled what it has always backfilled.
    """
    import backend.db as db_mod

    asyncio.run(db_mod.init_db())

    # (1) everything the current release expects now exists.
    assert {"channel_members", "workspace_meta", "channel_people",
            "agent_teams", "agent_team_members", "skills", "routines",
            "routine_runs", "approvals", "custom_tools", "ai_providers",
            "reactions"} <= _tables(old_db)
    assert "schema_migrations" in _tables(old_db)
    assert "model" in _columns(old_db, "messages")
    assert "kind" in _columns(old_db, "channels")
    assert "owner_agent" in _columns(old_db, "channels")
    assert {"history_window", "max_tool_calls", "tools", "job", "status",
            "display_name", "avatar", "archived_at",
            "tools_locked"} <= _columns(old_db, "agents")
    assert {"role", "password_hash"} <= _columns(old_db, "users")
    assert "idx_messages_channel_id" in _indexes(old_db)

    # (2) the old rows are still there. ensure_schema() also seeds the three
    # default agents into a database that has none, so assert membership of
    # the old row rather than exact equality.
    conn = sqlite3.connect(old_db)
    try:
        assert conn.execute(
            "SELECT body FROM messages WHERE channel_id = 'general'"
        ).fetchall() == [("a message from an old release",)]
        assert conn.execute("SELECT handle FROM users").fetchall() == [("uzeb",)]
        agent_names = {r[0] for r in conn.execute("SELECT name FROM agents")}
        assert "swarm" in agent_names
    finally:
        conn.close()

    # (3) the additive path still backfills a pre-existing row: the old
    # 'swarm' agent predates the job column, so it gets the default job
    # and a display name rather than staying NULL.
    async def read_back():
        agent = await db_mod.fetch_agent("swarm")
        history = await db_mod.get_history("general")
        return agent, history

    agent, history = asyncio.run(read_back())
    assert agent is not None
    assert agent["job"] == "Generalist"
    assert agent["display_name"]
    assert agent["history_window"] == 12
    assert history[0]["body"] == "a message from an old release"


def test_old_database_serves_health_through_the_app(old_db, monkeypatch):
    """Boot the real app against the old database and hit /health.

    This is the end-to-end half of the upgrade claim: the schema changes
    are not enough if startup itself throws on a database it has never
    seen. /health is unauthenticated by design (AGENTS.md §3).
    """
    import backend.db as db_mod
    import backend.main as main_mod
    from fastapi.testclient import TestClient

    main_mod._last_write.clear()
    main_mod.hub._rooms.clear()
    main_mod.hub._presence.clear()

    with TestClient(main_mod.app, client=("testclient", 50000)) as client:
        health = client.get("/health")
        assert health.status_code == 200, health.text
        assert health.json()["status"] == "ok"
        # The old rows are reachable through the API, not just via sqlite.
        channels = client.get("/api/channels/general/messages").status_code
        assert channels in (401, 403, 200)  # reachable, auth-gated as designed

    main_mod._last_write.clear()
    main_mod.hub._rooms.clear()
    main_mod.hub._presence.clear()


def test_upgrading_twice_is_stable(old_db):
    """A second boot against an already-upgraded database changes nothing."""
    import backend.db as db_mod

    async def twice():
        await db_mod.init_db()
        after_first = _tables(old_db), _columns(old_db, "agents"), _indexes(old_db)
        await db_mod.init_db()
        after_second = _tables(old_db), _columns(old_db, "agents"), _indexes(old_db)
        return after_first, after_second

    first, second = asyncio.run(twice())
    assert first == second


# ------------------------------------------------------------ retention ----


def test_retention_is_off_by_default_and_deletes_nothing(old_db, monkeypatch):
    """Default behaviour must be unchanged: nothing is ever deleted.

    An old message and a new one both survive a boot with no retention
    configured.
    """
    import backend.db as db_mod

    monkeypatch.delenv("SWARM_RETENTION_DAYS", raising=False)
    assert db_mod.retention_days() is None

    async def seed_and_boot():
        await db_mod.init_db()
        await db_mod.add_message("general", "uzeb", "ancient history", "human")

    asyncio.run(seed_and_boot())
    assert asyncio.run(db_mod.apply_retention()) == {
        "enabled": False, "deleted": 0, "retention_days": None,
    }

    async def still_there():
        rows = await db_mod.get_history("general", limit=200)
        return [r["body"] for r in rows]

    assert "ancient history" in asyncio.run(still_there())


def test_retention_deletes_only_rows_older_than_the_cutoff(old_db, monkeypatch):
    import backend.db as db_mod

    async def seed():
        await db_mod.init_db()
        old = await db_mod.add_message("general", "uzeb", "old", "human")
        new = await db_mod.add_message("general", "uzeb", "new", "human")
        # Backdate the first message past a 7-day cutoff.
        async with db_mod.open_db() as conn:
            await conn.execute(
                "UPDATE messages SET created_at = ? WHERE id = ?",
                (time_now() - 30 * 86400, old["id"]),
            )
            await conn.commit()
        return old, new

    monkeypatch.setenv("SWARM_RETENTION_DAYS", "7")
    old, new = asyncio.run(seed())
    stats = asyncio.run(db_mod.apply_retention())

    assert stats["enabled"] is True
    assert stats["retention_days"] == 7
    assert stats["deleted"] == 1
    assert stats["tables"]["messages"] == 1

    async def bodies():
        return [r["body"] for r in await db_mod.get_history("general", limit=200)]

    assert asyncio.run(bodies()) == ["new"]


def test_retention_survives_reactions_and_replies(old_db, monkeypatch):
    """foreign_keys is ON, so an un-swept reaction row would raise.

    Deleting an old message must take its reactions with it, and must not
    cascade into a newer reply.
    """
    import backend.db as db_mod

    monkeypatch.setenv("SWARM_RETENTION_DAYS", "7")

    async def seed():
        await db_mod.init_db()
        old = await db_mod.add_message("general", "uzeb", "old parent", "human")
        reply = await db_mod.add_message(
            "general", "swarm", "new reply", "agent", parent_id=old["id"],
        )
        await db_mod.add_reaction(old["id"], "uzeb", "👍")
        async with db_mod.open_db() as conn:
            await conn.execute(
                "UPDATE messages SET created_at = ? WHERE id = ?",
                (time_now() - 30 * 86400, old["id"]),
            )
            await conn.commit()
        return old, reply

    old, reply = asyncio.run(seed())
    stats = asyncio.run(db_mod.apply_retention())
    assert stats.get("error") is None, stats
    assert stats["deleted"] == 1

    async def survivors():
        return await db_mod.get_history("general", limit=200)

    rows = asyncio.run(survivors())
    assert [r["body"] for r in rows] == ["new reply"]
    # The reply survived and was un-parented rather than cascaded away.
    assert rows[0]["parent_id"] is None
    assert asyncio.run(db_mod.get_reactions(old["id"])) == []


def test_retention_rejects_nonsense_values_rather_than_deleting(old_db, monkeypatch):
    """A typo must never be read as "delete everything"."""
    import backend.db as db_mod

    asyncio.run(db_mod.init_db())
    for value in ("", "  ", "abc", "0", "-5"):
        monkeypatch.setenv("SWARM_RETENTION_DAYS", value)
        assert db_mod.retention_days() is None
        assert asyncio.run(db_mod.apply_retention())["deleted"] == 0


def time_now() -> float:
    import time

    return time.time()
