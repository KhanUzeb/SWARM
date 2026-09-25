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

_DEFAULT_MAX_AGE_HOURS = 72.0
_DEFAULT_MAX_MB = 512.0
_SWEEP_BATCH = 512  # files per pass; keeps a sweep short


def _max_age_hours() -> float:
    raw = (os.environ.get("SWARM_GC_MAX_AGE_HOURS") or "").strip()
    try:
        return float(raw) if raw else _DEFAULT_MAX_AGE_HOURS
    except ValueError:
        return _DEFAULT_MAX_AGE_HOURS


def _max_bytes() -> int:
    raw = (os.environ.get("SWARM_GC_MAX_MB") or "").strip()
    try:
        return int(float(raw) * 1024 * 1024) if raw else int(_DEFAULT_MAX_MB * 1024 * 1024)
    except ValueError:
        return int(_DEFAULT_MAX_MB * 1024 * 1024)


def _sandbox_root() -> Path:
    from . import agent

    return Path(agent.SANDBOX_DIR)


def _iter_files(root: Path):
    for dirpath, _dirnames, filenames in os.walk(root):
        for name in filenames:
            p = Path(dirpath) / name
            try:
                stat = p.stat()
            except OSError:
                continue
            yield p, stat


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
