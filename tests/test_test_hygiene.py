"""Lane D hygiene guards: temp-SQLite isolation and overlap map.

1. Every test runs against a fresh temp SQLite file; the repo ``swarm.db``
   (AGENTS.md section 7) is never touched. ``client``/``remote_client``/
   ``local_client`` redirect to ``tmp_path / "swarm.db"``; the autouse
   ``_isolate_db_file`` fixture redirects everything else to a fresh temp
   file too, so direct-``backend.db`` tests cannot pass only because an
   earlier test seeded ``tests/_test.db``.

2. Overlap audit for the four lane files (flagship_flows / agent_lane /
   agent_safety / backend_core_lane): beside the one true duplicate pair
   (the two handoff tests, removed from ``test_handoff_orchestration`` with
   replacement coverage in ``test_flagship_flows``), the remaining overlaps
   are deliberate boundary tests at different layers — loop vs stall vs
   polling with fresh vs stale results, ``search_workspace`` vs
   ``search_memory`` LIKE-escaping parity, routine helper vs scheduler E2E.
   They pin different behavior; none was removed. No flaky/slow tests were
   removed: the slowest (handoff depth-cap polling, ~2.3s) assert a negative
   (the chain stops) on a 0.5s settle window and are deterministic.
"""
from __future__ import annotations

import os
from pathlib import Path

import backend.db as db_mod


REPO_ROOT = Path(__file__).resolve().parent.parent


def test_repo_swarm_db_is_not_the_test_database():
    """The live DB path in the test process points into a temp dir, never the repo."""
    db_path = Path(db_mod.DB_PATH).resolve()
    repo_db = (REPO_ROOT / "swarm.db").resolve()
    backend_db = (REPO_ROOT / "backend" / "swarm.db").resolve()
    assert db_path != repo_db, f"tests touched the repo swarm.db: {db_path}"
    assert db_path != backend_db, f"tests touched backend/swarm.db: {db_path}"
    env_path = os.environ.get("SWARM_DB_PATH", "")
    if env_path:
        assert Path(env_path).resolve() != repo_db
        assert Path(env_path).resolve() != backend_db


def test_every_test_gets_a_fresh_temp_sqlite_file():
    """The autouse isolation fixture plus client fixtures guarantee tmp files."""
    conftest_src = (Path(__file__).parent / "conftest.py").read_text(encoding="utf-8")
    assert "_isolate_db_file" in conftest_src
    assert 'tmp_path / "autouse-swarm.db"' in conftest_src
    # API fixtures serve each test from its own tmp dir.
    assert 'tmp_path / "swarm.db"' in conftest_src
    assert 'monkeypatch.setenv("SWARM_DB_PATH"' in conftest_src


def test_default_test_db_is_isolated_under_tests_dir():
    """The import-time default is tests/_test.db, not a repo-root swarm.db."""
    conftest_src = (Path(__file__).parent / "conftest.py").read_text(encoding="utf-8")
    assert '_DB = Path(__file__).parent / "_test.db"' in conftest_src


def test_overlap_map_documents_what_was_kept_and_why():
    """Next pruner: the kept overlaps are boundary tests, not copies."""
    handoff_src = (Path(__file__).parent / "test_handoff_orchestration.py").read_text(encoding="utf-8")
    # The two handoff copies were removed; their replacement coverage lives in flagship.
    assert "def test_handoff_routes_to_mentioned_recipient_in_order" not in handoff_src
    assert "def test_handoff_stops_at_depth_boundary" not in handoff_src
    assert "test_flagship_flows" in handoff_src
    assert "assertion was lost" in handoff_src
    # Kept boundary cases still exist at their respective layers.
    safety_src = (Path(__file__).parent / "test_agent_safety.py").read_text(encoding="utf-8")
    assert "test_legitimate_repetition_does_not_trip_the_loop_guard" in safety_src
    assert "test_polling_with_a_varying_argument_is_not_treated_as_a_loop" in safety_src
    assert "test_watching_a_file_that_is_being_appended_to_is_not_a_loop" in safety_src
    core_src = (Path(__file__).parent / "test_backend_core_lane.py").read_text(encoding="utf-8")
    assert "test_search_workspace_treats_wildcards_literally" in core_src
