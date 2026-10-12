"""Sandbox garbage collection (GC).

The sandbox lives under the OS temp directory by default, so it grows
unbounded: drafts, private homes, artifacts, and browser leftovers
accumulate across restarts. This module provides a bounded sweeper:

- ``sweep_stale`` deletes files older than ``SWARM_GC_MAX_AGE_HOURS``
  (default 72) under the sandbox root.
- ``sweep_oversize`` trims the sandbox to ``SWARM_GC_MAX_MB`` (default
  512) by deleting oldest files first when the total exceeds the cap.
- ``gc_sweep`` runs both passes and returns a stats payload for logging.
- Startup and periodic sweeps are wired in ``main.py`` (lifespan +
  routine loop), never blocking a request.
"""

from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Any

from .settings import get_settings

_DEFAULT_MAX_AGE_HOURS = 72.0
_DEFAULT_MAX_MB = 512.0
_SWEEP_BATCH = 512  # files per pass; keeps a sweep short


def _max_age_hours() -> float:
    """SWARM_GC_MAX_AGE_HOURS, default 72. An unusable value keeps the default."""
    return get_settings().effective_gc_max_age_hours()


def _max_bytes() -> int:
    """SWARM_GC_MAX_MB as bytes, default 512MB. Same lenient fallback."""
    return int(get_settings().effective_gc_max_mb() * 1024 * 1024)


def _sandbox_root() -> Path:
    from . import agent

    return Path(agent.SANDBOX_DIR)


def _iter_files(root: Path):
    for dirpath, _dirnames, filenames in os.walk(root):
        # Never descend into version-control metadata, even when the sandbox
        # was rebound to a worktree: deleting .git objects to fit 512MB would
        # corrupt the repo the product runs from.
        if ".git" in Path(dirpath).parts:
            continue
        for name in filenames:
            lowered = name.lower()
            # Secrets and databases are never GC candidates. When the sandbox
            # points at an entire PC these names are user data, not drafts.
            if lowered.startswith(".env") or lowered in {"swarm.db", ".env"} or lowered.endswith(".db-wal") or lowered.endswith(".db-shm"):
                continue
            p = Path(dirpath) / name
            try:
                stat = p.stat()
            except OSError:
                continue
            yield p, stat


def _refuse_gc_reason(root: Path) -> str | None:
    """Why GC must not sweep this root, or None when sweeping is safe.

    The sandbox is fluid now (Places → Sandbox can point it at any folder,
    even an entire drive). The sweeper deletes by age/size, which is only
    sane inside a drafts directory. Filesystem roots, home folders, and
    repo checkouts are refused so a 72h/512MB policy can never eat user data.
    """
    try:
        resolved = root.resolve()
    except OSError:
        return "unresolvable root"
    # Filesystem / drive root: C:\\, /, //.
    try:
        if resolved.parent == resolved:
            return "filesystem root is never garbage-collected"
    except Exception:  # noqa: BLE001
        return "unresolvable root"
    try:
        home = Path.home().resolve()
        if resolved == home:
            return "home folder is never garbage-collected"
    except OSError:
        pass
    # Repo checkout markers: never sweep the product itself.
    try:
        if (resolved / ".git").is_dir() and (resolved / "backend" / "main.py").is_file():
            return "repo checkout is never garbage-collected"
    except OSError:
        pass
    return None


def sweep_stale(root: Path, max_age_hours: float) -> int:
    """Delete files older than max_age_hours. Returns deleted count."""
    cutoff = time.time() - max_age_hours * 3600
    deleted = 0
    for p, stat in _iter_files(root):
        if stat.st_mtime < cutoff:
            try:
                p.unlink()
                deleted += 1
            except OSError:
                continue
            if deleted >= _SWEEP_BATCH:
                break
    return deleted


def sweep_oversize(root: Path, max_bytes: int) -> int:
    """Trim oldest files until total size fits max_bytes. Returns deleted count."""
    files: list[tuple[Path, os.stat_result]] = list(_iter_files(root))
    total = sum(stat.st_size for _p, stat in files)
    if total <= max_bytes:
        return 0
    files.sort(key=lambda pair: pair[1].st_mtime)
    deleted = 0
    for p, stat in files:
        if total <= max_bytes:
            break
        try:
            p.unlink()
            total -= stat.st_size
            deleted += 1
        except OSError:
            continue
        if deleted >= _SWEEP_BATCH:
            break
    return deleted


def gc_sweep() -> dict[str, Any]:
    """Run both passes over the sandbox root. Never raises."""
    root = _sandbox_root()
    if not root.exists():
        return {"root": str(root), "skipped": "sandbox missing", "deleted": 0}
    refused = _refuse_gc_reason(root)
    if refused:
        return {"root": str(root), "skipped": refused, "deleted": 0}
    age_h = _max_age_hours()
    cap_b = _max_bytes()
    try:
        stale = sweep_stale(root, age_h)
        oversize = sweep_oversize(root, cap_b)
    except Exception:  # noqa: BLE001 - GC must never 500
        return {"root": str(root), "error": "sweep failed", "deleted": 0}
    return {
        "root": str(root),
        "deleted": stale + oversize,
        "stale_deleted": stale,
        "oversize_deleted": oversize,
        "max_age_hours": age_h,
        "max_mb": cap_b / (1024 * 1024),
    }
