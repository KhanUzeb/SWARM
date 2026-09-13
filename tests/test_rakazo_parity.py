"""Rakazo-parity coverage: health, computer provider, BYO model, secret hygiene."""
from __future__ import annotations

import os


def test_health_endpoint_has_revision(client):
    res = client.get("/health")
    assert res.status_code == 200
    body = res.json()
    assert body["status"] == "ok"
    assert body["service"] == "swarm"
    assert isinstance(body.get("revision"), str) and body["revision"]


def test_status_reports_computer_provider(client):
    res = client.get("/api/status")
    assert res.status_code == 200
    assert res.json().get("computer_provider") in ("local", "none", "fake")


def test_computer_provider_helpers_offline(monkeypatch):
    from backend import computer_providers as computers

    monkeypatch.setenv("SWARM_COMPUTER_PROVIDER", "fake")
    assert computers.get_provider() == "fake"
    assert computers.provider_configured() is True
    payload = computers.status()
    assert payload["provider"] == "fake"
    assert payload["configured"] is True

    monkeypatch.setenv("SWARM_COMPUTER_PROVIDER", "none")
    assert computers.get_provider() == "none"
    assert computers.provider_configured() is False

    monkeypatch.setenv("SWARM_COMPUTER_PROVIDER", "bogus-value")
    assert computers.get_provider() == "local"


def test_custom_provider_in_catalog_and_status(client):
    from backend.ai_support.providers import get_provider

    spec = get_provider("custom")
    assert spec is not None
    assert spec["kind"] == "openai_compatible"

    res = client.get("/api/status")
    assert "custom" in res.json()["providers_ready"]


def test_custom_base_url_env_override(monkeypatch):
    from backend.ai_support.config import RuntimeProviderAuth
    from backend.ai_support.resolver import openai_compatible_config

    monkeypatch.setenv("SWARM_OPENAI_COMPAT_BASE_URL", "http://127.0.0.1:11434/v1")
    auth = RuntimeProviderAuth(
        provider_id="custom",
        api_key="ollama",
        base_url="http://127.0.0.1:9999/v1",
    )
    assert openai_compatible_config(auth).base_url == "http://127.0.0.1:11434/v1"


def test_provider_apis_never_leak_secrets(client, auth):
    handle_headers = auth
    for path in (
        "/api/status",
        "/api/ai-support/providers",
        "/api/ai-support/connections",
        "/api/ai-support/models",
        "/api/v2/providers",
    ):
        res = client.get(path, headers=handle_headers)
        assert res.status_code == 200
        text = res.text.lower()
        assert "sk-" not in text
        assert "secret" not in text or "refresh_secret" not in text
        if isinstance(res.json(), list):
            for row in res.json():
                assert "secret" not in row or row.get("secret") is None
        elif isinstance(res.json(), dict):
            for row in res.json().get("ai_providers", []) or []:
                assert "secret" not in row


def test_computer_api_reports_provider(client, auth):
    headers = auth
    res = client.get("/api/computer", headers=headers)
    assert res.status_code == 200
    body = res.json()
    # Backward compat: legacy keys stay; provider metadata is additive.
    assert "files" in body and "workspace" in body
    assert body["provider"]["provider"] in ("local", "none", "fake")
    assert "team" in body["homes"]
