"""Local SQLite backup and restore for a stopped or running instance.

The one non-negotiable here: copying ``swarm.db`` while Swarm is running is
not a backup. Swarm uses WAL, so committed rows can still live in
``swarm.db-wal``; a main-file-only copy can open as an empty or malformed
database. ``backup()`` therefore uses SQLite's online backup API, which reads
a consistent snapshot through the WAL. ``restore()`` is deliberately the
opposite: it requires an explicit operator attestation that the instance is
stopped and refuses to touch a target that still has WAL/SHM sidecars.

What this module does *not* claim:

* No SQLite lock can prove that no process will reopen a file a millisecond
  later. ``confirm_stopped`` is an operator attestation, not an observation.
  Restoring over a live database can corrupt it; the CLI makes the operator
  type the flag before it will proceed.
* Swarm never deletes a ``-wal``, ``-shm`` or ``-journal`` file. If one is
  present at the target, handling is uncertain, so restore fails and tells
  the operator to stop the service cleanly and inspect the file themselves.
* ``integrity_check`` proves a file is structurally intact, not that it holds
  the work you expected. ``verify_database`` also reports required-table and
  row counts so a caller can check a known row, and both backup and restore
  report them.
* A ``<target>.swarm-lock`` file serialises operations on one path. A lock
  left behind by a crash is never auto-broken: the operator must confirm no
  process is running and remove it deliberately.
"""
from __future__ import annotations

import hashlib
import os
import shutil
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

# Every Swarm database that has ever been through init_db() has these. A
# backup missing one is not a Swarm backup, even if the bytes are intact.
_REQUIRED_TABLES = ("channels", "messages", "users", "workspace_meta")
_SIDECAR_SUFFIXES = ("-wal", "-shm", "-journal")
_CHUNK = 1024 * 1024


class BackupError(RuntimeError):
    """A backup or restore was refused; no user data was changed."""


def resolve_db_path(explicit: str | os.PathLike[str] | None = None) -> Path:
    """The database path for a local command: explicit --db, settings, default.

    Deliberately imported from ``backend.db`` rather than re-parsing
    ``SWARM_DB_PATH`` here, so the path is the same one the server uses. This
    function never creates a file.
    """
    if explicit:
        return Path(explicit).expanduser()
    from .db import DB_PATH  # local import: CLI does not need aiosqlite at boot

    return Path(DB_PATH)


def sidecar_paths(path: str | os.PathLike[str]) -> list[Path]:
    """WAL/SHM/journal filenames SQLite associates with ``path``."""
    p = Path(path)
    return [Path(str(p) + suffix) for suffix in _SIDECAR_SUFFIXES]


def existing_sidecars(path: str | os.PathLike[str]) -> list[Path]:
    """Sidecars that currently exist. Reported, never deleted, by this module."""
    return [p for p in sidecar_paths(path) if p.exists()]


def _same_file(a: Path, b: Path) -> bool:
    """True when two paths name the same file (including hard links)."""
    if not a.exists() or not b.exists():
        return False
    try:
        return os.path.samefile(a, b)
    except OSError:
        return False


def _refuse_live_overlap(source: Path, target: Path, action: str) -> None:
    """Keep an artifact from being written over the live DB or its sidecars.

    Windows refuses ``os.replace`` on an open file, but Linux can replace it;
    either way, aiming a backup at ``swarm.db`` or ``swarm.db-wal`` puts the
    artifact where the server expects its database. Fail up front with a
    stable ``BackupError`` rather than a platform-dependent ``OSError``.
    """
    if _same_file(source, target):
        raise BackupError(
            f"{action} refused: source and target are the same file: {target}"
        )
    for db, artifact in ((source, target), (target, source)):
        if artifact.resolve() in {p.resolve() for p in sidecar_paths(db)}:
            raise BackupError(
                f"{action} refused: {artifact} is a WAL/SHM sidecar of {db}; "
                "Swarm never overwrites sidecars"
            )


def _read_only_uri(path: Path) -> str:
    """A URI that cannot create a file and cannot spawn WAL sidecars.

    ``mode=ro`` alone still lets SQLite create a ``-shm`` for a WAL database.
    ``immutable=1`` tells it the file will not change, which is true for our
    own artifacts (we convert them out of WAL mode before writing them).
    """
    return path.as_uri() + "?mode=ro&immutable=1"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(_CHUNK), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _fsync_file(path: Path) -> None:
    # os.fsync on a Python "rb" handle fails on Windows (bad file descriptor);
    # an os.open descriptor works on both platforms.
    fd = os.open(path, os.O_RDWR)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _fsync_dir(path: Path) -> None:
    """Best effort: opening a directory is not portable to Windows."""
    try:
        fd = os.open(path, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(fd)
    except OSError:
        pass
    finally:
        os.close(fd)


def _temp_path(target: Path, label: str) -> Path:
    """Collision-resistant scratch file beside the target, not in /tmp.

    The uuid matters across processes: two CLI invocations have distinct pids,
    but only a unique name plus O_EXCL (below) stops one from truncating the
    other's staged artifact.
    """
    return target.with_name(f"{target.name}.{label}-{os.getpid()}-{uuid4().hex[:12]}")


def _reserve_temp(path: Path) -> None:
    """Atomically claim a scratch name; never truncate another worker's file."""
    fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    os.close(fd)


def _publish_backup(tmp: Path, dst: Path, overwrite: bool) -> None:
    """Publish a verified artifact without silently replacing another's work.

    ``os.link`` fails when the link name already exists on both POSIX and
    NTFS, so with overwrite=False two processes racing on one destination
    cannot both win. With overwrite=True replacing is the requested behavior.
    """
    if overwrite:
        os.replace(tmp, dst)
        return
    try:
        os.link(tmp, dst)
    except FileExistsError as exc:
        # Same stable message as the early existence check: the outcome of a
        # race is still "the destination already exists".
        raise BackupError(
            f"destination exists: {dst} (pass overwrite=True to replace it)"
        ) from exc
    tmp.unlink()


class _TargetLock:
    """Fail-closed O_EXCL exclusion for one database path.

    A stale lock is *not* silently broken: a crashed process could still be
    writing. It names the file so an operator can confirm and remove it. The
    lock records a token, and release only removes a file holding our token.
    """

    def __init__(self, target: Path) -> None:
        self.path = target.with_name(target.name + ".swarm-lock")
        self.token = f"{os.getpid()}-{uuid4().hex}"
        self.held = False

    def acquire(self) -> None:
        try:
            fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError as exc:
            raise BackupError(
                f"target is locked: {self.path}. Another backup/restore may be "
                "running, or a crash may have left the lock behind. Confirm no "
                "operation is running, then remove that file and retry."
            ) from exc
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(self.token)
        _fsync_dir(self.path.parent)
        self.held = True

    def release(self) -> None:
        if not self.held:
            return
        self.held = False
        try:
            if self.path.read_text(encoding="utf-8") == self.token:
                self.path.unlink()
        except OSError:
            pass  # never mask the backup/restore result with cleanup noise


def _preserve_previous(dst: Path, previous: Path) -> None:
    """Stage a recoverable copy without ever unlinking ``dst`` first.

    Renaming the target aside before publishing left a crash window with no
    database at the target path. A hard link keeps both names valid; on a
    filesystem without hard links, a fully verified copy is published instead.
    """
    try:
        os.link(dst, previous)
    except OSError:
        staged = _temp_path(previous, "swarm-preserve-tmp")
        try:
            shutil.copyfile(dst, staged)
            _fsync_file(staged)
            verify_database(staged)
            os.replace(staged, previous)
        finally:
            if staged.exists():
                staged.unlink()
    _fsync_dir(dst.parent)


def verify_database(path: str | os.PathLike[str]) -> dict[str, Any]:
    """Read-only integrity and structure check. Never creates or repairs.

    Raises ``BackupError`` on a missing file, an integrity failure or a
    database without the required Swarm tables. Returns a report with the
    sidecars it saw (it does not act on them) and row counts for the required
    tables, so a caller can compare a known row before and after.
    """
    db = Path(path).expanduser()
    if not db.is_file():
        raise BackupError(f"database does not exist: {db}")
    sidecars = [str(p) for p in existing_sidecars(db)]
    try:
        conn = sqlite3.connect(_read_only_uri(db), uri=True)
        try:
            integrity = str(conn.execute("PRAGMA integrity_check").fetchone()[0])
            present = {
                row[0]
                for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                )
            }
            counts = {
                table: int(conn.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0])
                for table in _REQUIRED_TABLES
                if table in present
            }
        finally:
            conn.close()
    except sqlite3.Error as exc:
        raise BackupError(f"cannot verify {db}: {exc}") from exc

    missing = [t for t in _REQUIRED_TABLES if t not in counts]
    if integrity != "ok":
        raise BackupError(f"integrity_check failed for {db}: {integrity}")
    if missing:
        raise BackupError(f"{db} is missing Swarm tables: {', '.join(missing)}")

    return {
        "path": str(db),
        "integrity": integrity,
        "tables": len(present),
        "required_tables": list(_REQUIRED_TABLES),
        "row_counts": counts,
        "sidecars": sidecars,
        "sha256": _sha256(db),
        "bytes": db.stat().st_size,
    }


def backup(
    source: str | os.PathLike[str],
    destination: str | os.PathLike[str],
    *,
    overwrite: bool = False,
) -> dict[str, Any]:
    """Write a consistent, self-contained backup of ``source`` to ``destination``.

    Uses ``sqlite3.Connection.backup()`` so rows still in the source WAL are
    included; the source is never copied byte-for-byte while a writer might
    be active. The artifact is converted out of WAL mode before it is placed,
    so a restore needs only the single file. Writing goes to a uniquely named
    temp file in the destination directory and is verified before an atomic
    no-clobber publish (``os.link``; ``os.replace`` only with overwrite=True).
    A per-target lock prevents another backup or restore from interleaving.
    """
    src = Path(source).expanduser()
    dst = Path(destination).expanduser()
    if not src.is_file():
        raise BackupError(f"source database does not exist: {src}")
    _refuse_live_overlap(src, dst, "backup")
    if dst.exists():
        if not dst.is_file():
            raise BackupError(f"destination is not a file: {dst}")
        if not overwrite:
            raise BackupError(f"destination exists: {dst} (pass overwrite=True to replace it)")
    dst.parent.mkdir(parents=True, exist_ok=True)

    lock = _TargetLock(dst)
    lock.acquire()
    tmp = _temp_path(dst, "swarm-backup-tmp")
    src_conn: sqlite3.Connection | None = None
    dst_conn: sqlite3.Connection | None = None
    try:
        _reserve_temp(tmp)
        # Read-only (but NOT immutable): the backup API then reads through
        # the WAL, so rows still in the sidecar are captured, and closing the
        # connection does not checkpoint or remove the source's sidecars.
        # immutable=1 would hide exactly those rows. Never fall back to a
        # read-write connect: on close SQLite can checkpoint a stopped WAL
        # database and delete sidecars, mutating the source this command
        # promises only to read.
        try:
            src_conn = sqlite3.connect(src.as_uri() + "?mode=ro", uri=True)
        except sqlite3.Error as exc:
            raise BackupError(
                f"cannot open {src} read-only through its WAL: {exc}. "
                "Stop the instance and inspect the WAL sidecars yourself; "
                "Swarm will not open the source read-write."
            ) from exc
        dst_conn = sqlite3.connect(str(tmp))
        src_conn.backup(dst_conn)
        journal_mode = str(dst_conn.execute("PRAGMA journal_mode=DELETE").fetchone()[0])
        if journal_mode != "delete":
            raise BackupError(
                f"backup artifact is still in {journal_mode} mode; refusing to "
                "write a file that needs WAL sidecars to be readable"
            )
        dst_conn.close()
        dst_conn = None
        src_conn.close()
        src_conn = None

        verify_database(tmp)
        # Recheck at the last moment as well: a second process may have
        # published its artifact while the snapshot was being taken.
        if dst.exists() and not overwrite:
            raise BackupError(f"destination exists: {dst} (pass overwrite=True to replace it)")
        _publish_backup(tmp, dst, overwrite)
        _fsync_dir(dst.parent)
        # Report on the final path, not the temp name that no longer exists.
        report = verify_database(dst)
    except sqlite3.Error as exc:
        raise BackupError(f"backup failed: {exc}") from exc
    except OSError as exc:
        raise BackupError(f"backup could not be published: {exc}") from exc
    finally:
        if dst_conn is not None:
            dst_conn.close()
        if src_conn is not None:
            src_conn.close()
        if tmp.exists():
            tmp.unlink()
        lock.release()

    report["source"] = str(src)
    report["destination"] = str(dst)
    report["journal_mode"] = journal_mode
    report["source_sidecars"] = [str(p) for p in existing_sidecars(src)]
    return report


def restore(
    source: str | os.PathLike[str],
    target: str | os.PathLike[str],
    *,
    confirm_stopped: bool = False,
) -> dict[str, Any]:
    """Restore a verified backup into a stopped instance.

    ``confirm_stopped`` is mandatory: no process-local lock can prove that a
    server will not reopen the file. Sidecars at either the backup or the
    target make the byte layout ambiguous, so this function refuses rather
    than deleting them. An existing target is preserved under
    ``<name>.pre-restore-<stamp>`` — never deleted — by a hard link (or a
    verified copy where hard links are unavailable) *before* the verified
    replacement is published, so a crash never leaves the target path empty.
    If verification of the placed copy fails, the previous target is moved
    back and any rollback failure is reported rather than hidden.
    """
    if not confirm_stopped:
        raise BackupError(
            "refusing to restore without confirm_stopped=True: stop the Swarm "
            "instance first, then confirm explicitly (CLI: --confirm-stopped)"
        )
    src = Path(source).expanduser()
    dst = Path(target).expanduser()
    if not src.is_file():
        raise BackupError(f"backup does not exist: {src}")
    _refuse_live_overlap(src, dst, "restore")
    if dst.exists() and not dst.is_file():
        raise BackupError(f"restore target is not a file: {dst}")

    src_sidecars = existing_sidecars(src)
    if src_sidecars:
        raise BackupError(
            "backup has WAL/SHM sidecars; refusing to guess which bytes are the "
            f"backup: {', '.join(str(p) for p in src_sidecars)}"
        )
    verified = verify_database(src)

    dst_sidecars = existing_sidecars(dst)
    if dst_sidecars:
        raise BackupError(
            "restore target still has WAL/SHM sidecars; stop the instance and "
            "inspect them yourself — Swarm will not delete them: "
            + ", ".join(str(p) for p in dst_sidecars)
        )
    dst.parent.mkdir(parents=True, exist_ok=True)

    # Serialise on the target: two concurrent restores would otherwise each
    # publish and could roll back over the other's successful result.
    lock = _TargetLock(dst)
    lock.acquire()
    tmp = _temp_path(dst, "swarm-restore-tmp")
    previous: Path | None = None
    preserved = False
    placed = False
    try:
        _reserve_temp(tmp)
        shutil.copyfile(src, tmp)
        _fsync_file(tmp)
        verify_database(tmp)

        # A sidecar can appear while the copy is staged (a server restarted
        # against the operator's attestation). Do not publish over it.
        if existing_sidecars(dst):
            raise BackupError(
                "restore target still has WAL/SHM sidecars; stop the instance "
                "and inspect them yourself — Swarm will not delete them: "
                + ", ".join(str(p) for p in existing_sidecars(dst))
            )
        if dst.exists():
            stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%f")
            previous = dst.with_name(
                f"{dst.name}.pre-restore-{stamp}-{os.getpid()}-{uuid4().hex[:8]}"
            )
            if previous.exists():
                raise BackupError(f"previous-backup path already exists: {previous}")
            _preserve_previous(dst, previous)
            preserved = True
        os.replace(tmp, dst)
        placed = True
        _fsync_dir(dst.parent)
        restored = verify_database(dst)
    except Exception as exc:
        # Never let a cleanup failure hide the original error or strand the
        # operator's database without saying where it now lives.
        problems: list[str] = []
        if placed and dst.exists():
            try:
                dst.unlink()
            except OSError as cleanup_exc:
                problems.append(f"could not remove failed restore: {cleanup_exc}")
        if preserved and previous is not None and previous.exists():
            if not placed:
                # Still a duplicate of the intact target.
                try:
                    previous.unlink()
                except OSError as cleanup_exc:
                    problems.append(f"could not remove duplicate: {cleanup_exc}")
            elif dst.exists():
                problems.append(f"previous target is still at {previous}; move it back manually")
            else:
                try:
                    previous.rename(dst)
                except OSError as cleanup_exc:
                    problems.append(f"could not restore previous target from {previous}: {cleanup_exc}")
        if problems:
            raise BackupError(f"{exc}; rollback incomplete: " + " ".join(problems)) from exc
        if isinstance(exc, OSError):
            raise BackupError(f"restore failed: {exc}") from exc
        raise
    finally:
        if tmp.exists():
            tmp.unlink()
        lock.release()

    return {
        "source": str(src),
        "target": str(dst),
        "previous_target": str(previous) if previous else None,
        "verified_backup": verified,
        "restored": restored,
        "still_stopped_attestation": True,
    }
