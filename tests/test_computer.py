"""Computer-use, browser-use, and Composio plugin tests."""
from __future__ import annotations

import asyncio

import backend.main as main
from backend.tools import browser, composio_client, computer
from backend.tools.registry import get_registry


def _clear_rate():
    main._last_write.clear()


def test_catalog_includes_computer_browser_composio(client, auth):
    res = client.get("/api/tools", headers=auth)
    assert res.status_code == 200
    names = {t["name"] for t in res.json()["tools"]}
    assert "computer_run" in names
    assert "browser_navigate" in names
    assert "plugin:composio:execute" in names
    assert "exa_search" in names
    assert "tavily_search" in names
    assert "firecrawl_scrape" in names
    assert "browser_use" in names
    assert "cua_desktop" in names
    plugins = {p["id"] for p in res.json()["plugins"]}
    assert "composio" in plugins


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
    assert "computer_run" not in agents["ledger"]["tools"]
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


def test_composio_plugin_execute_without_key(client, monkeypatch):
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

    text = asyncio.run(go())
    assert "composio not connected" in text.lower()


def test_composio_client_without_key(client, monkeypatch):
    monkeypatch.delenv("COMPOSIO_API_KEY", raising=False)
    text = asyncio.run(composio_client.execute("GMAIL_SEND_EMAIL", {}))
    assert "composio not connected" in text.lower()


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


def test_browser_use_without_cli(client):
    text = asyncio.run(
        __import__("backend.tools.connectors", fromlist=["browser_use_cli"]).browser_use_cli("status")
    )
    assert "not installed" in text.lower() or "browser-use" in text.lower()


def test_cua_status_degrades(client):
    text = asyncio.run(
        __import__("backend.tools.connectors", fromlist=["cua_desktop"]).cua_desktop("status")
    )
    assert "cli:" in text.lower() or "cua" in text.lower()
