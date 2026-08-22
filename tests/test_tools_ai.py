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
    assert "read_workspace" in names
    assert "fetch_url" in names
    assert "channel_digest" in names


def test_ai_provider_catalog_includes_hf(client, auth):
    res = client.get("/api/ai-support/providers", headers=auth)
    assert res.status_code == 200
    ids = {p["id"] for p in res.json()}
    assert "huggingface" in ids
    assert "together" in ids
    hf = next(p for p in res.json() if p["id"] == "huggingface")
    assert hf["kind"] == "openai_compatible"


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


def test_agent_rejects_unknown_tool(client, auth):
    res = client.post(
        "/api/agents",
        json={
            "name": "badtools",
            "system_prompt": "test",
            "tools": ["not_a_real_tool_xyz"],
        },
        headers=auth,
    )
    assert res.status_code == 400


def test_ai_provider_connect_status(client, auth):
    providers = client.get("/api/ai-support/providers", headers=auth)
    assert providers.status_code == 200
    assert any(p["id"] == "groq" for p in providers.json())

    connect = client.post(
        "/api/ai-support/connect/groq",
        json={"api_key": "gsk_test_key_12345678", "model": "openai/gpt-oss-120b"},
        headers=auth,
    )
    assert connect.status_code == 200

    status = client.get("/api/status")
    assert status.status_code == 200
    assert status.json()["groq"] is True

    _clear_rate()
    disconnect = client.delete("/api/ai-support/connect/groq", headers=auth)
    assert disconnect.status_code == 200
