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
    # Credential sealing has no public fallback key. Tests exercise the real
    # AES-GCM path, so they need a process-local secret.
    os.environ.setdefault("SWARM_SECRET", "test-seal-key-not-for-deployment")
    for name in (
        "SWARM_SETUP_TOKEN", "SWARM_INVITE_CODE", "SWARM_SYSTEM",
        "SWARM_COMPUTER_PROVIDER",
    ):
        os.environ.pop(name, None)
    os.environ.pop("GROQ_API_KEY", None)
    os.environ.pop("OPENROUTER_API_KEY", None)
    os.environ.pop("COMPOSIO_API_KEY", None)
    os.environ.pop("EXA_API_KEY", None)
    os.environ.pop("TAVILY_API_KEY", None)
    os.environ.pop("FIRECRAWL_API_KEY", None)
    os.environ.pop("SWARM_AGENT_MODEL", None)
    os.environ.pop("LANGFUSE_PUBLIC_KEY", None)
    os.environ.pop("LANGFUSE_SECRET_KEY", None)


def _app_client(tmp_path, monkeypatch, client_addr=("127.0.0.1", 50000)):
    db_path = tmp_path / "swarm.db"
    monkeypatch.setenv("SWARM_DB_PATH", str(db_path))

    import backend.db as db_mod
    import backend.main as main_mod

    db_mod.DB_PATH = db_path
    main_mod._last_write.clear()
    main_mod._register_attempts.clear()
    main_mod._password_failures.clear()
    main_mod.hub._rooms.clear()
    main_mod.hub._presence.clear()

    from fastapi.testclient import TestClient

    with TestClient(main_mod.app, client=client_addr) as c:
        yield c

    main_mod.hub._rooms.clear()
    main_mod.hub._presence.clear()
    main_mod._last_write.clear()
    main_mod._register_attempts.clear()
    main_mod._password_failures.clear()


@pytest.fixture
def client(tmp_path, monkeypatch):
    yield from _app_client(tmp_path, monkeypatch)


@pytest.fixture
def remote_client(tmp_path, monkeypatch):
    """A client that is not on loopback and sends no forwarding headers."""
    yield from _app_client(tmp_path, monkeypatch, ("203.0.113.7", 50000))


@pytest.fixture(autouse=True)
def _reset_system_runtime():
    from backend.tools import system as system_mod
    system_mod.reset_runtime()
    yield
    system_mod.reset_runtime()


@pytest.fixture
def local_client(tmp_path, monkeypatch):
    yield from _app_client(tmp_path, monkeypatch, ("127.0.0.1", 50000))


@pytest.fixture
def token(client) -> str:
    res = client.post("/api/register", json={"handle": "uzeb"})
    assert res.status_code == 200
    return res.json()["token"]


@pytest.fixture
def auth(token) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}
