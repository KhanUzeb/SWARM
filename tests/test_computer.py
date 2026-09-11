"""Computer-use, browser-use, and Composio plugin tests."""
from __future__ import annotations

import asyncio
import os
import subprocess
from pathlib import Path

import backend.main as main
from backend.tools import browser, composio_client, computer
from backend.tools import system as system_mod
from backend.tools.registry import get_registry


def _clear_rate():
    main._last_write.clear()


def test_seeded_agents_get_computer_use(client, auth):
    agents = {a["name"]: a for a in client.get("/api/agents", headers=auth).json()}
    assert "computer_run" in agents["swarm"]["tools"]
    assert "browser_navigate" in agents["swarm"]["tools"]
    assert "plugin:composio:execute" in agents["swarm"]["tools"]
    assert "exa_search" in agents["swarm"]["tools"]
    assert "tavily_search" in agents["swarm"]["tools"]
    assert "firecrawl_scrape" in agents["swarm"]["tools"]
    assert "browser_use" in agents["swarm"]["tools"]
    assert "cua_desktop" in agents["swarm"]["tools"]
    assert "system_run" in agents["swarm"]["tools"]
    assert "system_read" in agents["swarm"]["tools"]
    assert "computer_run" not in agents["ledger"]["tools"]
    assert "system_run" not in agents["ledger"]["tools"]
    assert "plugin:composio:execute" not in agents["ledger"]["tools"]


def test_computer_run_echo(tmp_path, monkeypatch):
    monkeypatch.setattr("backend.agent.SANDBOX_DIR", str(tmp_path))
    out = computer.computer_run("echo swarm-computer")
    assert "swarm-computer" in out
    blocked = computer.computer_run("rm -rf /")
    assert "blocked" in blocked


def test_computer_open_url_points_at_browser(monkeypatch):
    monkeypatch.setenv("SWARM_BROWSER", "1")
    note = computer.computer_open("https://example.com")
    assert "browser_navigate" in note


def test_status_includes_composio_and_browser(client):
    body = client.get("/api/status").json()
    assert body["composio"] is False
    assert body["exa"] is False
    assert body["tavily"] is False
    assert body["firecrawl"] is False
    assert body["browser"] is True
    assert body["system"] is True


def test_system_run_echo(tmp_path, monkeypatch):
    monkeypatch.setenv("SWARM_SYSTEM", "1")
    monkeypatch.setenv("SWARM_SYSTEM_ROOT", str(tmp_path))
    (tmp_path / "hello.txt").write_text("hi-from-host", encoding="utf-8")
    out = system_mod.system_run("echo swarm-system")
    assert "swarm-system" in out
    listed = system_mod.system_ls("")
    assert "hello.txt" in listed
    read = system_mod.system_read("hello.txt")
    assert "hi-from-host" in read
    wrote = system_mod.system_write("note.md", "ok")
    assert "wrote note.md" in wrote
    assert (tmp_path / "note.md").read_text(encoding="utf-8") == "ok"
    blocked = system_mod.system_run("rm -rf /")
    assert "blocked" in blocked
    escaped = system_mod.system_read("../secret")
    assert "invalid path" in escaped
    env_block = system_mod.system_write(".env", "SECRET=1")
    assert "protected" in env_block


def test_system_disabled_at_function_and_api_layers(client, auth, tmp_path, monkeypatch):
    monkeypatch.setenv("SWARM_SYSTEM", "0")
    monkeypatch.setenv("SWARM_SYSTEM_ROOT", str(tmp_path))
    out = system_mod.system_run("echo nope")
    assert "disabled" in out.lower()
    listing = client.get("/api/computer/system", headers=auth).json()
    assert listing["enabled"] is False
    assert client.get(
        "/api/computer/system/file", params={"path": "x"}, headers=auth
    ).status_code == 403


def test_computer_api_includes_system(client, auth, tmp_path, monkeypatch):
    monkeypatch.setenv("SWARM_SYSTEM", "1")
    monkeypatch.setenv("SWARM_SYSTEM_ROOT", str(tmp_path))
    (tmp_path / "host.md").write_text("from-host", encoding="utf-8")
    data = client.get("/api/computer", headers=auth).json()
    assert data["system"]["enabled"] is True
    assert any(e["name"] == "host.md" for e in data["system"]["entries"])
    listing = client.get("/api/computer/system", headers=auth).json()
    assert listing["enabled"] is True
    preview = client.get(
        "/api/computer/system/file", params={"path": "host.md"}, headers=auth
    ).json()
    assert preview["content"] == "from-host"
    assert client.get(
        "/api/computer/system/file", params={"path": "../secret"}, headers=auth
    ).status_code == 404


def test_system_root_mobility(client, auth, tmp_path, monkeypatch):
    project = tmp_path / "project"
    other = tmp_path / "elsewhere"
    project.mkdir()
    other.mkdir()
    (other / "outside.md").write_text("hello-outside", encoding="utf-8")
    monkeypatch.setenv("SWARM_SYSTEM", "1")
    monkeypatch.setenv("SWARM_SYSTEM_ROOT", str(project))

    listing = client.get("/api/computer/system", headers=auth).json()
    assert listing["enabled"] is True
    assert Path(listing["root"]) == project.resolve()
    assert any(p["id"] == "home" for p in listing["places"])
    assert "crumbs" in listing

    _clear_rate()
    moved = client.post(
        "/api/computer/system/root",
        json={"path": str(other)},
        headers=auth,
    )
    assert moved.status_code == 200
    body = moved.json()
    assert Path(body["root"]) == other.resolve()
    assert any(e["name"] == "outside.md" for e in body["entries"])
    preview = client.get(
        "/api/computer/system/file", params={"path": "outside.md"}, headers=auth
    ).json()
    assert preview["content"] == "hello-outside"

    _clear_rate()
    up = client.post(
        "/api/computer/system/root",
        json={"path": body["parent_abs"]},
        headers=auth,
    )
    assert up.status_code == 200
    assert Path(up.json()["root"]) == other.parent.resolve()

    _clear_rate()
    denied = client.post(
        "/api/computer/system/root",
        json={"path": tmp_path.anchor},
        headers=auth,
    )
    assert denied.status_code == 400


def test_system_listing_scopes_and_hides(tmp_path, monkeypatch):
    """Listing shows only the bound folder, hides legacy shell folders,
    and skips escaping junctions."""
    project = tmp_path / "recall"
    outside = tmp_path / "Documents"
    project.mkdir()
    outside.mkdir()
    (project / "app").mkdir()
    (project / "data").mkdir()
    (project / "readme.md").write_text("ok", encoding="utf-8")
    (project / "My Music").mkdir()
    (project / "My Documents").mkdir()
    decoy = project / "Documents"
    if os.name == "nt":
        created = subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(decoy), str(outside)],
            capture_output=True,
            text=True,
            check=False,
        )
        if created.returncode != 0:
            import pytest
            pytest.skip(created.stderr or created.stdout or "could not create junction")
    else:
        decoy.symlink_to(outside, target_is_directory=True)
    monkeypatch.setenv("SWARM_SYSTEM", "1")
    monkeypatch.setenv("SWARM_SYSTEM_ROOT", str(project))
    system_mod.reset_runtime()
    names = {e["name"] for e in system_mod.listing("")["entries"]}
    assert {"app", "data", "readme.md"} <= names
    assert names.isdisjoint({"Documents", "Music", "My Music", "My Documents"})


def test_browser_status_and_disabled(client, auth, monkeypatch):
    monkeypatch.setenv("SWARM_BROWSER", "0")
    res = client.get("/api/browser/status", headers=auth)
    assert res.status_code == 200
    assert res.json()["enabled"] is False
    text = asyncio.run(browser.navigate("https://example.com"))
    assert "unavailable" in text.lower()


def test_composio_status_and_connect(client, auth, monkeypatch):
    monkeypatch.delenv("COMPOSIO_API_KEY", raising=False)
    status = client.get("/api/composio/status", headers=auth)
    assert status.status_code == 200
    assert status.json()["connected"] is False

    connect = client.post(
        "/api/composio/connect",
        json={"api_key": "ak_test_composio_key"},
        headers=auth,
    )
    assert connect.status_code == 200
    assert client.get("/api/composio/status", headers=auth).json()["connected"] is True
    assert client.get("/api/status").json()["composio"] is True

    _clear_rate()
    disconnect = client.delete("/api/composio/connect", headers=auth)
    assert disconnect.status_code == 200
    assert client.get("/api/composio/status", headers=auth).json()["connected"] is False


def test_composio_without_key_at_both_layers(client, monkeypatch):
    monkeypatch.delenv("COMPOSIO_API_KEY", raising=False)

    async def go():
        return await get_registry().execute(
            "plugin:composio:execute",
            {"tool_slug": "GMAIL_SEND_EMAIL"},
            agent_name="swarm",
            channel_id="general",
            allowed=["plugin:composio:execute"],
            sandbox_dir="/tmp",
            shell_runner=lambda _cmd: "",
            workspace_helpers={},
        )

    assert "composio not connected" in asyncio.run(go()).lower()
    assert "composio not connected" in asyncio.run(composio_client.execute("GMAIL_SEND_EMAIL", {})).lower()


def test_connectors_catalog_and_exa_without_key(client, auth, monkeypatch):
    monkeypatch.delenv("EXA_API_KEY", raising=False)
    res = client.get("/api/connectors", headers=auth)
    assert res.status_code == 200
    ids = {c["id"] for c in res.json()}
    assert ids >= {"composio", "exa", "tavily", "firecrawl", "browser_use", "cua"}
    text = asyncio.run(__import__("backend.tools.connectors", fromlist=["exa_search"]).exa_search("latest llm papers"))
    assert "exa not connected" in text.lower()


def test_connector_cli_rejects_api_key(client, auth):
    res = client.post(
        "/api/connectors/browser_use/connect",
        json={"api_key": "not-a-real-key-here"},
        headers=auth,
    )
    assert res.status_code == 400


def test_optional_clis_degrade_gracefully(client):
    connectors = __import__("backend.tools.connectors", fromlist=["browser_use_cli", "cua_desktop"])
    browser_text = asyncio.run(connectors.browser_use_cli("status"))
    assert "not installed" in browser_text.lower() or "browser-use" in browser_text.lower()
    cua_text = asyncio.run(connectors.cua_desktop("status"))
    assert "cli:" in cua_text.lower() or "cua" in cua_text.lower()
