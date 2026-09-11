"""Tests for tools registry, custom tools, plugins, and AI support API."""
from __future__ import annotations

import backend.main as main


def _clear_rate():
    main._last_write.clear()


def test_list_tools_includes_builtins_and_plugin(client, auth):
    res = client.get("/api/tools", headers=auth)
    assert res.status_code == 200
    data = res.json()
    names = {t["name"] for t in data["tools"]}
    assert {"read_workspace", "fetch_url", "channel_digest", "computer_run",
            "browser_navigate", "plugin:composio:execute", "plugin:time-helper:utc_now",
            "exa_search", "tavily_search", "firecrawl_scrape", "browser_use",
            "cua_desktop", "system_run", "system_ls", "system_read", "system_write"} <= names
    assert "composio" in {p["id"] for p in data["plugins"]}


def test_ai_provider_catalog_and_connect(client, auth):
    res = client.get("/api/ai-support/providers", headers=auth)
    assert res.status_code == 200
    ids = {p["id"] for p in res.json()}
    assert {"huggingface", "together", "groq"} <= ids
    hf = next(p for p in res.json() if p["id"] == "huggingface")
    assert hf["kind"] == "openai_compatible"

    connect = client.post(
        "/api/ai-support/connect/groq",
        json={"api_key": "gsk_test_key_12345678", "model": "openai/gpt-oss-120b"},
        headers=auth,
    )
    assert connect.status_code == 200
    assert client.get("/api/status").json()["groq"] is True

    _clear_rate()
    assert client.delete("/api/ai-support/connect/groq", headers=auth).status_code == 200


def test_custom_tool_crud(client, auth):
    create = client.post(
        "/api/tools/custom",
        json={
            "name": "hello_echo",
            "description": "Echo test tool",
            "handler_type": "echo",
            "handler_config": {},
            "parameters": {"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"]},
        },
        headers=auth,
    )
    assert create.status_code == 200
    row = create.json()
    assert row["name"] == "hello_echo"

    listed = client.get("/api/tools", headers=auth)
    names = {t["name"] for t in listed.json()["tools"]}
    assert "hello_echo" in names

    _clear_rate()
    delete = client.delete(f"/api/tools/custom/{row['id']}", headers=auth)
    assert delete.status_code == 200


def test_ai_models_catalog_offline_live_and_mapping(client, auth, monkeypatch):
    from backend.ai_support.resolver import map_model_for_provider

    res = client.get("/api/ai-support/providers/groq/models", headers=auth)
    assert res.status_code == 200
    body = res.json()
    assert body["live"] is False
    assert "openai/gpt-oss-120b" in {m["id"] for m in body["models"]}

    all_models = client.get("/api/ai-support/models", headers=auth)
    assert all_models.status_code == 200
    payload = all_models.json()
    groq = next(p for p in payload["providers"] if p["id"] == "groq")
    assert any(m["id"] == "openai/gpt-oss-120b" for m in groq["models"])
    assert payload["models"]

    async def fake_list(provider_id, *, api_key=None):
        return {
            "provider_id": provider_id,
            "name": "Groq",
            "live": True,
            "models": [
                {"id": "openai/gpt-oss-20b", "name": "GPT-OSS 20B", "owned_by": "groq", "default": False},
                {"id": "openai/gpt-oss-120b", "name": "GPT-OSS 120B", "owned_by": "groq", "default": True},
            ],
            "default_model": "openai/gpt-oss-120b",
        }

    monkeypatch.setattr("backend.main.list_provider_models", fake_list)
    live = client.get("/api/ai-support/providers/groq/models", headers=auth).json()
    assert live["live"] is True
    assert [m["id"] for m in live["models"]] == ["openai/gpt-oss-20b", "openai/gpt-oss-120b"]

    assert map_model_for_provider("gpt-4o-mini", "openai", stored_model="gpt-4o") == "gpt-4o-mini"
    assert map_model_for_provider("openai/gpt-oss-20b", "huggingface", stored_model="Qwen/Qwen2.5-72B-Instruct") == "Qwen/Qwen2.5-72B-Instruct"
