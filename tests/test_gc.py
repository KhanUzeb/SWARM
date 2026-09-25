"""Tests for backend.gc — sandbox garbage collection."""

from __future__ import annotations

import os
import time
from pathlib import Path

import pytest

from backend import gc as gc_mod


@pytest.fixture()
def sandbox(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "sandbox"
    root.mkdir()
    monkeypatch.setattr(gc_mod, "_sandbox_root", lambda: root)
    return root


def _touch(path: Path, age_hours: float = 0.0, size: int = 1) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"x" * size)
    old = time.time() - age_hours * 3600
    os.utime(path, (old, old))
    return path


def test_sweep_stale_deletes_old_files(sandbox: Path) -> None:
    old = _touch(sandbox / "drafts" / "a.txt", age_hours=80)
    fresh = _touch(sandbox / "drafts" / "b.txt", age_hours=1)

    deleted = gc_mod.sweep_stale(sandbox, max_age_hours=72.0)

    assert deleted == 1
    assert not old.exists()
    assert fresh.exists()


def test_sweep_stale_keeps_recent_files(sandbox: Path) -> None:
    _touch(sandbox / "a.txt", age_hours=1)
    _touch(sandbox / "sub" / "b.txt", age_hours=10)

    deleted = gc_mod.sweep_stale(sandbox, max_age_hours=72.0)

    assert deleted == 0
    assert (sandbox / "a.txt").exists()
    assert (sandbox / "sub" / "b.txt").exists()


def test_sweep_oversize_trims_oldest_first(sandbox: Path) -> None:
    old_big = _touch(sandbox / "old.bin", age_hours=50, size=600)
    fresh_small = _touch(sandbox / "new.bin", age_hours=1, size=100)
    cap = 500

    deleted = gc_mod.sweep_oversize(sandbox, max_bytes=cap)

    assert deleted == 1
    assert not old_big.exists()
    assert fresh_small.exists()


def test_sweep_oversize_noop_under_cap(sandbox: Path) -> None:
    _touch(sandbox / "a.txt", size=10)
    _touch(sandbox / "b.txt", size=20)

    deleted = gc_mod.sweep_oversize(sandbox, max_bytes=1024)

    assert deleted == 0


def test_gc_sweep_reports_stats(sandbox: Path) -> None:
    _touch(sandbox / "old.txt", age_hours=100)

    stats = gc_mod.gc_sweep()

    assert stats["deleted"] == 1
    assert stats["stale_deleted"] == 1
    assert stats["max_age_hours"] == 72.0


def test_gc_sweep_skips_missing_sandbox(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(gc_mod, "_sandbox_root", lambda: tmp_path / "missing")

    stats = gc_mod.gc_sweep()

    assert stats["deleted"] == 0
    assert "skipped" in stats
