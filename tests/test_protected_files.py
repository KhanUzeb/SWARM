"""Credential and database files are unreadable through every surface.

The check lives in `system.is_protected_path`, and these tests pin the call
sites that used to drift: the sandbox preview route, the system preview route,
the workspace read/write helpers, and `computer_open`.
"""
from __future__ import annotations

import asyncio
from pathlib import Path

import backend.agent as agent_mod
from backend.tools import computer
from backend.tools import system as system_mod


def _write(path: Path, body: str = "SECRET=do-not-leak") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")


def test_sandbox_preview_refuses_credentials(client, auth, tmp_path, monkeypatch):
    monkeypatch.setattr(agent_mod, "SANDBOX_DIR", str(tmp_path))
    _write(tmp_path / ".env")
    res = client.get(
        "/api/computer/file", params={"path": ".env"}, headers=auth,
    )
    assert res.status_code == 403
    assert "do-not-leak" not in res.text

    ok = tmp_path / "notes.md"
    _write(ok, "public notes")
    assert client.get(
        "/api/computer/file", params={"path": "notes.md"}, headers=auth,
    ).json()["content"] == "public notes"


def test_system_preview_refuses_credentials_and_keys(
    client, auth, tmp_path, monkeypatch,
):
    monkeypatch.setenv("SWARM_SYSTEM", "1")
    monkeypatch.setenv("SWARM_SYSTEM_ROOT", str(tmp_path))
    system_mod.reset_runtime()
    _write(tmp_path / ".env")
    _write(tmp_path / ".ssh" / "id_rsa", "PRIVATE KEY")
    _write(tmp_path / ".git" / "config", "[remote]")

    for path in (".env", ".ssh/id_rsa", ".git/config"):
        res = client.get(
            "/api/computer/system/file", params={"path": path}, headers=auth,
        )
        assert res.status_code == 403, path
        assert "PRIVATE KEY" not in res.text

    _write(tmp_path / "readme.md", "safe")
    assert client.get(
        "/api/computer/system/file", params={"path": "readme.md"}, headers=auth,
    ).json()["content"] == "safe"


def test_workspace_read_and_write_refuse_credentials(tmp_path, monkeypatch):
    monkeypatch.setattr(agent_mod, "SANDBOX_DIR", str(tmp_path))
    _write(tmp_path / ".env")

    read = asyncio.run(agent_mod._run_read_workspace(".env"))
    assert "blocked" in read.lower()
    assert "do-not-leak" not in read

    write = asyncio.run(agent_mod._run_write_workspace(".env", "SECRET=overwritten"))
    assert "blocked" in write.lower()
    assert (tmp_path / ".env").read_text(encoding="utf-8") == "SECRET=do-not-leak"


def test_computer_open_refuses_credentials(tmp_path, monkeypatch):
    monkeypatch.setattr(agent_mod, "SANDBOX_DIR", str(tmp_path))
    _write(tmp_path / ".env")
    opened = computer.computer_open(".env")
    assert "blocked" in opened.lower()


def test_the_open_database_is_protected_even_without_the_env_var(
    tmp_path, monkeypatch,
):
    """The default local DB lives at backend/swarm.db with SWARM_DB_PATH unset.

    Comparing only the configured string left the real file unprotected in
    exactly that default install, so the check also looks at the path SQLite
    opened.
    """
    import backend.db as db

    monkeypatch.setenv("SWARM_DB_PATH", "")
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "store.sqlite")

    assert system_mod.is_protected_path(tmp_path / "store.sqlite") is True
    assert system_mod.is_protected_path(tmp_path / "store.sqlite-wal") is True
    assert system_mod.is_protected_path(tmp_path / "store.sqlite-shm") is True
    assert system_mod.is_protected_path(tmp_path / "notes.md") is False
