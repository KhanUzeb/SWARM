"""Shared test fixtures. Uses a temp SQLite file so tests never touch
the developer's local swarm.db. Groq is never called — agent replies
are mocked in tests that need them."""
from __future__ import annotations

import os
from pathlib import Path

import pytest

# db.DB_PATH is read at import time — set the env var before importing the app.
_DB = Path(__file__).parent / "_test.db"


def pytest_configure(config):  # noqa: ARG001
    os.environ["SWARM_DB_PATH"] = str(_DB)
    os.environ["PYTHON_DOTENV_DISABLED"] = "1"
    os.environ.pop("GROQ_API_KEY", None)
    os.environ.pop("LANGFUSE_PUBLIC_KEY", None)
    os.environ.pop("LANGFUSE_SECRET_KEY", None)


@pytest.fixture
def client(tmp_path, monkeypatch):
    db_path = tmp_path / "swarm.db"
    monkeypatch.setenv("SWARM_DB_PATH", str(db_path))

    import backend.db as db_mod
    import backend.main as main_mod

    db_mod.DB_PATH = db_path
    main_mod._last_write.clear()
    main_mod.hub._rooms.clear()

    from fastapi.testclient import TestClient

    with TestClient(main_mod.app) as c:
        yield c

    main_mod.hub._rooms.clear()
    main_mod._last_write.clear()


@pytest.fixture
def token(client) -> str:
    res = client.post("/api/register", json={"handle": "uzeb"})
    assert res.status_code == 200
    return res.json()["token"]


@pytest.fixture
def auth(token) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}
