"""Local SQLite backup/restore — every database here is disposable.

The tests exercise what an operator actually depends on: a backup taken while
Swarm is running still contains rows that live in the WAL; restore never
guesses at WAL/SHM sidecars; and a refused restore leaves the target exactly
as it was. No test opens the developer's ``swarm.db`` (conftest redirects
``SWARM_DB_PATH``, and these tests pass explicit temp paths anyway).
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys
from pathlib import Path

import pytest

from backend import backup as mod

SCHEMA = """
CREATE TABLE channels (id TEXT PRIMARY KEY);
CREATE TABLE messages (id INTEGER PRIMARY KEY, body TEXT);
CREATE TABLE users (handle TEXT PRIMARY KEY);
CREATE TABLE workspace_meta (key TEXT PRIMARY KEY, value TEXT);
"""


def make_db(path, marker, *, wal=True):
    """A small Swarm-shaped database. Return the open connection for WAL tests."""
    con = sqlite3.connect(path)
    if wal:
        con.execute("PRAGMA journal_mode=WAL")
        con.execute("PRAGMA wal_autocheckpoint=0")
    con.executescript(SCHEMA)
    con.execute("INSERT INTO messages VALUES (1, ?)", (marker,))
    con.commit()
    return con


def body_at(path):
    ro = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)
    try:
        return ro.execute("SELECT body FROM messages WHERE id = 1").fetchone()[0]
    finally:
        ro.close()


# ------------------------------------------------------------------ backup --

def test_online_backup_includes_rows_still_in_the_wal(tmp_path):
    live = tmp_path / "live.db"
    con = make_db(live, "in-wal")
    sidecars = [p.name for p in mod.existing_sidecars(live)]
    assert sidecars, "the fixture should leave a committed row in the WAL"

    report = mod.backup(live, tmp_path / "backup.db")

    assert report["integrity"] == "ok"
    assert report["row_counts"]["messages"] == 1
    assert report["journal_mode"] == "delete", "artifact must not need a WAL sidecar"
    assert mod.existing_sidecars(tmp_path / "backup.db") == []
    assert body_at(tmp_path / "backup.db") == "in-wal"
    # A backup must not checkpoint or delete the source's sidecars.
    assert [p.name for p in mod.existing_sidecars(live)] == sidecars
    con.close()


def test_backup_refuses_to_replace_without_explicit_overwrite(tmp_path):
    live = tmp_path / "live.db"
    con = make_db(live, "row")
    con.close()
    dest = tmp_path / "backup.db"
    mod.backup(live, dest)
    first_hash = mod.verify_database(dest)["sha256"]

    with pytest.raises(mod.BackupError, match="destination exists"):
        mod.backup(live, dest)
    assert mod.verify_database(dest)["sha256"] == first_hash

    again = mod.backup(live, dest, overwrite=True)
    assert again["sha256"] == first_hash, "backups of an unchanged file are reproducible"


def test_backup_refuses_to_overwrite_the_live_database_or_its_wal(tmp_path):
    live = tmp_path / "live.db"
    con = make_db(live, "keep-me")
    wal = tmp_path / "live.db-wal"
    assert wal.exists(), "the fixture should leave a committed row in the WAL"

    with pytest.raises(mod.BackupError, match="same file"):
        mod.backup(live, live, overwrite=True)
    with pytest.raises(mod.BackupError, match="WAL/SHM sidecar"):
        mod.backup(live, wal, overwrite=True)

    assert body_at(live) == "keep-me"
    assert wal.exists() and wal.stat().st_size > 0
    assert mod.existing_sidecars(live)
    con.close()


def test_backup_never_falls_back_to_a_read_write_source_open(tmp_path, monkeypatch):
    live = tmp_path / "live.db"
    con = make_db(live, "unchanged")
    con.close()
    before = live.read_bytes()
    real_connect = mod.sqlite3.connect

    def refusing_read_only(target, *args, **kwargs):
        if isinstance(target, str) and "mode=ro" in target:
            raise sqlite3.OperationalError("simulated WAL open failure")
        return real_connect(target, *args, **kwargs)

    monkeypatch.setattr(mod.sqlite3, "connect", refusing_read_only)
    dest = tmp_path / "backup.db"
    with pytest.raises(mod.BackupError, match="read-only"):
        mod.backup(live, dest)
    assert not dest.exists()
    assert live.read_bytes() == before, "a failed backup must not checkpoint the source"
    assert not list(tmp_path.glob("*.swarm-backup-tmp-*"))


def test_backup_no_clobber_collision_keeps_the_destination_exists_message(tmp_path, monkeypatch):
    live = tmp_path / "live.db"
    con = make_db(live, "row")
    con.close()
    dest = tmp_path / "backup.db"
    real_link = mod.os.link

    def collide(src, link_name):
        if Path(link_name) == dest:
            raise FileExistsError("another worker published first")
        return real_link(src, link_name)

    monkeypatch.setattr(mod.os, "link", collide)
    with pytest.raises(mod.BackupError, match="destination exists"):
        mod.backup(live, dest)
    assert not dest.exists()


def test_backup_ignores_a_stale_pid_named_temp_file(tmp_path):
    live = tmp_path / "live.db"
    con = make_db(live, "row")
    con.close()
    (tmp_path / f"backup.db.swarm-backup-tmp-{os.getpid()}").write_bytes(b"crash debris")

    report = mod.backup(live, tmp_path / "backup.db")

    assert report["row_counts"]["messages"] == 1
    assert body_at(tmp_path / "backup.db") == "row"


def test_resolve_db_path_prefers_explicit_then_settings(tmp_path, monkeypatch):
    explicit = tmp_path / "explicit.db"
    assert mod.resolve_db_path(explicit) == explicit
    from backend import db as db_mod

    monkeypatch.setattr(db_mod, "DB_PATH", str(tmp_path / "settings.db"), raising=False)
    assert mod.resolve_db_path() == tmp_path / "settings.db"


def test_verify_fails_closed_on_corrupt_or_missing_file(tmp_path):
    bad = tmp_path / "bad.db"
    bad.write_bytes(b"not a sqlite database" * 20)
    with pytest.raises(mod.BackupError, match="cannot verify"):
        mod.verify_database(bad)
    with pytest.raises(mod.BackupError, match="does not exist"):
        mod.verify_database(tmp_path / "absent.db")


def test_verify_rejects_a_database_without_the_swarm_tables(tmp_path):
    empty = tmp_path / "empty.db"
    con = sqlite3.connect(empty)
    con.execute("CREATE TABLE unrelated (x)")
    con.commit()
    con.close()
    with pytest.raises(mod.BackupError, match="missing Swarm tables"):
        mod.verify_database(empty)


# ------------------------------------------------------------------ restore --

def test_restore_refuses_to_target_its_own_source_or_a_sidecar(tmp_path):
    live = tmp_path / "live.db"
    con = make_db(live, "row")
    con.close()
    backup_file = tmp_path / "backup.db"
    mod.backup(live, backup_file)
    before = mod.verify_database(backup_file)["sha256"]

    with pytest.raises(mod.BackupError, match="same file"):
        mod.restore(backup_file, backup_file, confirm_stopped=True)

    wal = backup_file.with_name(backup_file.name + "-wal")
    wal.write_bytes(b"ambiguous backup sidecar")
    with pytest.raises(mod.BackupError, match="WAL/SHM sidecar"):
        mod.restore(backup_file, wal, confirm_stopped=True)

    assert wal.read_bytes() == b"ambiguous backup sidecar"
    assert mod.verify_database(backup_file)["sha256"] == before


def test_restore_refuses_a_locked_target_and_never_breaks_the_lock(tmp_path):
    live = tmp_path / "live.db"
    con = make_db(live, "backed-up")
    con.close()
    backup = tmp_path / "backup.db"
    mod.backup(live, backup)

    target = tmp_path / "target.db"
    con2 = make_db(target, "original")
    con2.close()
    lock = target.with_name(target.name + ".swarm-lock")
    lock.write_text("9999-someone-else", encoding="utf-8")

    with pytest.raises(mod.BackupError, match="target is locked"):
        mod.restore(backup, target, confirm_stopped=True)

    assert lock.read_text(encoding="utf-8") == "9999-someone-else"
    assert body_at(target) == "original"


def test_restore_requires_an_explicit_stopped_attestation(tmp_path):
    live = tmp_path / "live.db"
    con = make_db(live, "row")
    con.close()
    backup = tmp_path / "backup.db"
    mod.backup(live, backup)

    target = tmp_path / "target.db"
    con2 = make_db(target, "original")
    con2.close()
    before = mod.verify_database(target)["sha256"]

    with pytest.raises(mod.BackupError, match="confirm_stopped"):
        mod.restore(backup, target)

    assert body_at(target) == "original"
    assert mod.verify_database(target)["sha256"] == before


def test_restore_refuses_a_target_with_wal_sidecars_and_preserves_them(tmp_path):
    live = tmp_path / "live.db"
    con = make_db(live, "row")
    con.close()
    backup = tmp_path / "backup.db"
    mod.backup(live, backup)

    target = tmp_path / "target.db"
    con2 = make_db(target, "original")
    con2.close()
    wal = tmp_path / "target.db-wal"
    wal.write_bytes(b"stale wal from the old database")
    before = mod.verify_database(target)["sha256"]

    with pytest.raises(mod.BackupError, match="WAL/SHM sidecars"):
        mod.restore(backup, target, confirm_stopped=True)

    assert wal.read_bytes() == b"stale wal from the old database"
    assert body_at(target) == "original"
    assert mod.verify_database(target)["sha256"] == before


def test_restore_refuses_a_backup_with_its_own_sidecars(tmp_path):
    live = tmp_path / "live.db"
    con = make_db(live, "row")
    con.close()
    backup = tmp_path / "backup.db"
    mod.backup(live, backup)
    (tmp_path / "backup.db-wal").write_bytes(b"ambiguous backup")

    target = tmp_path / "target.db"
    with pytest.raises(mod.BackupError, match="backup has WAL/SHM sidecars"):
        mod.restore(backup, target, confirm_stopped=True)
    assert not target.exists()


def test_restore_roundtrip_keeps_the_previous_database(tmp_path):
    live = tmp_path / "live.db"
    con = make_db(live, "backed-up")
    con.close()
    backup = tmp_path / "backup.db"
    mod.backup(live, backup)

    target = tmp_path / "target.db"
    con2 = make_db(target, "old-target")
    con2.execute("UPDATE messages SET body = 'newer-than-backup'")
    con2.commit()
    con2.close()

    report = mod.restore(backup, target, confirm_stopped=True)

    assert report["restored"]["integrity"] == "ok"
    assert body_at(target) == "backed-up"
    assert mod.existing_sidecars(target) == []
    previous = report["previous_target"]
    assert previous and (tmp_path / previous).is_file()
    assert body_at(tmp_path / previous) == "newer-than-backup"


def test_restore_rolls_back_if_the_final_check_fails(tmp_path, monkeypatch):
    live = tmp_path / "live.db"
    con = make_db(live, "backed-up")
    con.close()
    backup = tmp_path / "backup.db"
    mod.backup(live, backup)

    target = tmp_path / "target.db"
    con2 = make_db(target, "keep-me")
    con2.close()
    before = mod.verify_database(target)["sha256"]

    real_verify = mod.verify_database

    def fail_only_final_check(path):
        if path == target:
            raise mod.BackupError("simulated verification failure")
        return real_verify(path)

    monkeypatch.setattr(mod, "verify_database", fail_only_final_check)
    with pytest.raises(mod.BackupError, match="simulated verification failure"):
        mod.restore(backup, target, confirm_stopped=True)

    assert body_at(target) == "keep-me"
    assert real_verify(target)["sha256"] == before  # monkeypatch still active
    assert not list(tmp_path.glob("*.swarm-restore-tmp-*"))
    assert len(list(tmp_path.glob("target.db.pre-restore-*"))) == 0


def test_restore_keeps_target_readable_if_publish_fails(tmp_path, monkeypatch):
    live = tmp_path / "live.db"
    con = make_db(live, "backed-up")
    con.close()
    backup = tmp_path / "backup.db"
    mod.backup(live, backup)

    target = tmp_path / "target.db"
    con2 = make_db(target, "original")
    con2.close()
    before = target.read_bytes()

    real_replace = mod.os.replace

    def fail_at_publish(src, dst):
        if Path(dst) == target:
            raise OSError("simulated crash before publish")
        return real_replace(src, dst)

    monkeypatch.setattr(mod.os, "replace", fail_at_publish)
    with pytest.raises(mod.BackupError, match="restore failed"):
        mod.restore(backup, target, confirm_stopped=True)

    assert target.read_bytes() == before
    assert body_at(target) == "original"
    assert not list(tmp_path.glob("target.db.pre-restore-*"))
    assert not list(tmp_path.glob("*.swarm-restore-tmp-*"))


# --------------------------------------------------------------------- CLI --

def test_cli_backup_and_restore_are_json_in_json_out(tmp_path):
    """Subprocess, not main() in-process: the CLI owns its own streams."""
    import subprocess
    from pathlib import Path

    repo = Path(__file__).resolve().parents[1]
    cli = repo / "cli" / "swarm_cli.py"
    env = dict(os.environ, PYTHON_DOTENV_DISABLED="1")

    def run(*args):
        return subprocess.run(
            [sys.executable, str(cli), *args], capture_output=True, text=True,
            cwd=str(repo), env=env,
        )

    live = tmp_path / "live.db"
    con = make_db(live, "cli-row")
    con.close()
    backup = tmp_path / "backup.db"

    result = run("backup", str(backup), "--db", str(live))
    assert result.returncode == 0, result.stderr
    report = json.loads(result.stdout)
    assert report["row_counts"]["messages"] == 1

    target = tmp_path / "target.db"
    refused = run("restore", str(backup), "--db", str(target))
    assert refused.returncode == 1
    assert "confirm" in json.loads(refused.stderr)["error"]
    assert not target.exists()

    restored_run = run(
        "restore", str(backup), "--db", str(target), "--confirm-stopped"
    )
    assert restored_run.returncode == 0, restored_run.stderr
    restored = json.loads(restored_run.stdout)
    assert restored["restored"]["integrity"] == "ok"
    assert body_at(target) == "cli-row"


def test_cli_reports_os_errors_as_json_not_tracebacks(tmp_path):
    import subprocess

    repo = Path(__file__).resolve().parents[1]
    cli = repo / "cli" / "swarm_cli.py"
    env = dict(os.environ, PYTHON_DOTENV_DISABLED="1")
    live = tmp_path / "live.db"
    con = make_db(live, "row")
    con.close()

    blocked = tmp_path / "not-a-directory"
    blocked.write_bytes(b"a file, not a folder")
    result = subprocess.run(
        [sys.executable, str(cli), "backup", str(blocked / "backup.db"), "--db", str(live)],
        capture_output=True, text=True, cwd=str(repo), env=env,
    )
    assert result.returncode == 1
    assert "error" in json.loads(result.stderr)
    assert "Traceback" not in result.stderr


def test_cli_reports_refused_connection_as_json(tmp_path):
    import subprocess

    repo = Path(__file__).resolve().parents[1]
    cli = repo / "cli" / "swarm_cli.py"
    env = dict(os.environ, PYTHON_DOTENV_DISABLED="1", SWARM_URL="http://127.0.0.1:1")
    result = subprocess.run(
        [sys.executable, str(cli), "channels"],
        capture_output=True, text=True, cwd=str(repo), env=env,
    )
    assert result.returncode == 1
    assert "error" in json.loads(result.stderr)
    assert "Traceback" not in result.stderr
