"""Backend hardening: batched reactions, LIKE escaping, history caps,
approval-run recovery, OAuth state expiry, JSON error shape."""
from __future__ import annotations

import asyncio

import pytest

import backend.db as db
from backend import v2 as v2mod


def _run(coro):
    return asyncio.run(coro)


def test_reactions_many_batches_and_handles_missing(client, auth):
    posted = client.post(
        "/api/channels/general/messages",
        json={"author": "uzeb", "body": "hello"},
        headers=auth,
    ).json()
    mid = posted["id"]
    assert client.post(
        f"/api/messages/{mid}/reactions",
        json={"author": "uzeb", "emoji": "👍"}, headers=auth,
    ).status_code == 204
    grouped = _run(db.get_reactions_many([mid, mid + 999999]))
    assert [r["emoji"] for r in grouped[mid]] == ["👍"]
    assert grouped[mid + 999999] == []
    assert _run(db.get_reactions_many([])) == {}
    history = client.get("/api/channels/general/messages", headers=auth).json()
    assert [r["emoji"] for r in history[0]["reactions"]] == ["👍"]


def test_like_wildcards_treated_literally(client, auth):
    async def seed():
        await db.add_memory("swarm", "100% coverage", channel_id=None, kind="note")
        await db.add_memory("swarm", "plain note", channel_id=None, kind="note")

    _run(seed())
    hits = _run(db.search_memory("swarm", "100%"))
    assert [h["body"] for h in hits] == ["100% coverage"]
    # A bare % must not match everything.
    assert [h["body"] for h in _run(db.search_memory("swarm", "%"))] == ["100% coverage"]
    # And forget must not nuke unrelated notes on a wildcard query.
    removed = _run(db.forget_memory_by_query("swarm", "%"))
    assert removed == 1
    assert [h["body"] for h in _run(db.search_memory("swarm", "plain"))] == ["plain note"]


def test_history_after_is_capped(client, auth):
    async def seed():
        for i in range(4):
            await db.add_message("general", "uzeb", f"m{i}", "human")

    _run(seed())
    rows = _run(db.get_history_after("general", 0, limit=2))
    assert [r["body"] for r in rows] == ["m0", "m1"]


def test_recoverable_runs_include_approval_waiters(client, auth):
    async def seed():
        run = await v2mod.create_run("uzeb", "do things", None)
        await v2mod.update_run(run["id"], "uzeb", "waiting_for_approval")
        return run["id"]

    run_id = _run(seed())
    ids = [r["id"] for r in _run(v2mod.recoverable_runs())]
    assert run_id in ids


def test_oauth_state_expires(monkeypatch):
    monkeypatch.setenv("SWARM_GOOGLE_OAUTH_CLIENT_ID", "cid")
    spec = {"oauth_authorize_url": "https://x.example/auth"}
    state = v2mod.oauth_start("google", "uzeb", spec).split("state=")[-1].split("&")[0]
    assert state in v2mod._oauth_states
    v2mod._oauth_states[state]["expires_at"] = 0.0
    with pytest.raises(ValueError, match="invalid or expired"):
        _run(v2mod.oauth_callback(state, "code123"))
    assert state not in v2mod._oauth_states


def test_errors_are_json_with_detail(client, auth):
    res = client.get("/api/work/does-not-exist", headers=auth)
    assert res.status_code == 404
    body = res.json()
    assert body["ok"] is False
    assert "detail" in body
