"""Stored provider credentials are encrypted, versioned, and migrate safely."""
from __future__ import annotations

import asyncio
import base64
import hashlib

import pytest

import backend.db as db
from backend.ai_support import store
from backend.settings import reset_settings_cache


@pytest.fixture(autouse=True)
def _restore_settings_cache():
    """`get_settings` is cached; a test that changes the seal key must not
    leave that key cached for the next test. This teardown runs after
    monkeypatch restores the environment, so the cache is rebuilt from the
    real one."""
    yield
    reset_settings_cache()


def test_round_trip_uses_a_fresh_nonce(client, monkeypatch):
    monkeypatch.setenv("SWARM_SECRET", "unit-test-secret-a")

    async def run():
        await store.connect("groq", "gsk_same_value_1234")
        await store.connect("openrouter", "gsk_same_value_1234")
        first = await db.get_ai_provider("groq")
        second = await db.get_ai_provider("openrouter")
        assert first["secret"].startswith("v2:")
        assert second["secret"].startswith("v2:")
        assert first["secret"] != second["secret"], "nonce reuse is the old bug"
        assert first["key_hint"].endswith("1234")
        assert await store.resolve_key("groq") == "gsk_same_value_1234"

    asyncio.run(run())


def test_missing_secret_refuses_to_store(client, monkeypatch):
    monkeypatch.delenv("SWARM_SECRET", raising=False)
    reset_settings_cache()

    async def run():
        with pytest.raises(store.SealKeyMissing):
            await store.connect("groq", "gsk_should_not_be_stored")
        assert await db.get_ai_provider("groq") is None

    asyncio.run(run())


def test_wrong_secret_marks_needs_reconnect(client, monkeypatch):
    monkeypatch.setenv("SWARM_SECRET", "the-original-secret")

    async def run():
        await store.connect("groq", "gsk_will_be_unreadable")
        monkeypatch.setenv("SWARM_SECRET", "a-different-secret")
        reset_settings_cache()
        assert await store.resolve_key("groq") is None
        status = {row["provider_id"]: row for row in await store.status()}
        assert status["groq"]["connected"] is False
        assert status["groq"]["needs_reconnect"] is True

    asyncio.run(run())


def test_legacy_blob_migrates_only_after_a_successful_decrypt(client, monkeypatch):
    secret = "legacy-migration-secret"
    monkeypatch.setenv("SWARM_SECRET", secret)
    reset_settings_cache()

    def legacy(raw: str) -> str:
        key = hashlib.sha256(secret.encode()).digest()
        data = bytes(
            b ^ key[i % len(key)] for i, b in enumerate(raw.encode())
        )
        return base64.urlsafe_b64encode(data).decode("ascii")

    async def run():
        await db.upsert_ai_provider("groq", legacy("gsk_legacy_key_9876"), hint="…9876")
        assert await store.resolve_key("groq") == "gsk_legacy_key_9876"
        row = await db.get_ai_provider("groq")
        assert row["secret"].startswith("v2:"), "a readable legacy row is re-sealed"
        assert await store.resolve_key("groq") == "gsk_legacy_key_9876"

    asyncio.run(run())


def test_legacy_blob_with_wrong_key_is_left_untouched(client, monkeypatch):
    monkeypatch.setenv("SWARM_SECRET", "first-secret")
    reset_settings_cache()

    def legacy(raw: str) -> str:
        key = hashlib.sha256(b"first-secret").digest()
        data = bytes(b ^ key[i % len(key)] for i, b in enumerate(raw.encode()))
        return base64.urlsafe_b64encode(data).decode("ascii")

    async def run():
        blob = legacy("gsk_old_key_1111")
        await db.upsert_ai_provider("groq", blob, hint="…1111")
        monkeypatch.setenv("SWARM_SECRET", "second-secret")
        reset_settings_cache()
        assert await store.resolve_key("groq") is None
        row = await db.get_ai_provider("groq")
        assert row["secret"] == blob, "a row we could not read must not be replaced"

    asyncio.run(run())


def test_status_never_returns_the_secret(client, monkeypatch):
    monkeypatch.setenv("SWARM_SECRET", "unit-test-secret-b")

    async def run():
        await store.connect("groq", "gsk_do_not_echo_me_4321")
        payload = await store.status()
        text = repr(payload)
        assert "gsk_do_not_echo_me_4321" not in text
        assert all("secret" not in row or row.get("secret") is None for row in payload)

    asyncio.run(run())


def test_connect_without_a_seal_key_names_the_setup_step(
    client, auth, monkeypatch,
):
    """A missing SWARM_SECRET is a configuration error, not a 500.

    `store.connect` refuses to seal without a key on purpose (there is no
    public fallback key). The route has to translate that refusal into the
    API's 4xx contract, because a fresh self-hosted install has no reason to
    know about the variable until it tries.
    """
    monkeypatch.setenv("SWARM_SECRET", "")
    reset_settings_cache()

    res = client.post(
        "/api/ai-support/connect/groq", headers=auth,
        json={"api_key": "gsk_probe_key_123"},
    )
    assert res.status_code == 409
    assert "SWARM_SECRET" in res.json()["detail"]
    assert asyncio.run(db.get_ai_provider("groq")) is None


def test_a_credential_sealed_under_another_key_is_not_advertised(
    client, auth, monkeypatch,
):
    """A row that cannot be decrypted must say so, not look connected.

    `store.status` reports `needs_reconnect`; the catalog and `/api/status`
    used to key off row existence alone, so the first real request failed
    later instead of at the connection list.
    """
    monkeypatch.setenv("SWARM_SECRET", "the-first-key")
    reset_settings_cache()
    asyncio.run(store.connect("groq", "gsk_rotate_me_1234"))

    monkeypatch.setenv("SWARM_SECRET", "the-second-key")
    reset_settings_cache()

    catalog = {
        p["id"]: p for p in client.get("/api/ai-support/providers", headers=auth).json()
    }
    assert catalog["groq"]["connected"] is False
    assert catalog["groq"]["via"] is None

    connections = client.get("/api/ai-support/connections", headers=auth).json()
    row = next(c for c in connections if c["provider_id"] == "groq")
    assert row["connected"] is False
    assert row["needs_reconnect"] is True

    status = client.get("/api/status", headers=auth).json()
    assert status["providers_ready"]["groq"] is False
