"""The SWARM_DEMO=1 scene must seed deterministically and offline.

The README's 5-minute demo depends on this scene: a release-blocker question
with a real answer, a bot-to-bot handoff chain, and one pending approval a
visitor can click. No provider call and no network may be involved.
"""
from __future__ import annotations



import pytest

from conftest import _app_client


@pytest.fixture
def demo_client(tmp_path, monkeypatch):
    """A booted app with SWARM_DEMO=1, mirroring conftest's temp-db wiring."""
    monkeypatch.setenv("SWARM_DEMO", "1")
    yield from _app_client(tmp_path, monkeypatch)


@pytest.fixture
def demo(demo_client):
    """Booted demo-mode app plus auth headers for the API reads.

    The token is minted on THIS client, not via conftest's `token` fixture:
    that one depends on the `client` fixture, which boots a second app with a
    second event loop while `_background_tasks` is module-global, so the
    second app's lifespan shutdown gathers tasks belonging to the first loop.
    """
    res = demo_client.post("/api/register", json={"handle": "demo"})
    assert res.status_code == 200
    return demo_client, {"Authorization": f"Bearer {res.json()['token']}"}


def _messages(client, auth, channel: str = "general") -> list[dict]:
    res = client.get(f"/api/channels/{channel}/messages", headers=auth)
    assert res.status_code == 200
    return res.json()


def test_demo_seeds_the_general_channel(demo):
    client, auth = demo
    msgs = _messages(client, auth)
    assert msgs, "demo mode must seed #general"
    bodies = [m["body"] for m in msgs]
    assert any("blocking the release" in b for b in bodies)


def test_demo_seeds_a_handoff_chain(demo):
    client, auth = demo
    msgs = _messages(client, auth)
    authors = [m["author"] for m in msgs]
    # A handoff means one bot replies and names another bot in the thread.
    assert "swarm" in authors
    assert "coder" in authors
    handoff = [m for m in msgs if "@coder" in m["body"]]
    assert handoff, "demo must contain a handoff that names the next bot"


def test_demo_seeds_a_clickable_pending_approval(demo):
    client, auth = demo
    res = client.get("/api/approvals?status=pending", headers=auth)
    assert res.status_code == 200
    pending = res.json()
    assert pending, "demo must leave one pending approval to click"
    row = pending[0]
    assert row["status"] == "pending"
    assert row["channel_id"] == "general"
    assert row["agent_name"]
    assert row["action"], "the approval must say what will run"


def test_demo_approval_can_be_resolved(demo):
    client, auth = demo
    pending = client.get("/api/approvals?status=pending", headers=auth).json()
    approval_id = pending[0]["id"]
    res = client.post(
        f"/api/approvals/{approval_id}/resolve",
        headers=auth,
        json={"status": "approved"},
    )
    assert res.status_code == 200
    still = client.get("/api/approvals?status=pending", headers=auth).json()
    assert all(a["id"] != approval_id for a in still)


def test_seeding_is_idempotent(demo):
    """Re-seeding must not duplicate the scene or a visitor's replies."""
    from backend import db as db_mod

    client, auth = demo
    before = _messages(client, auth)
    # Run on the app's own event loop; a fresh asyncio.run() would orphan the
    # lifespan's background tasks and fail teardown.
    client.portal.call(db_mod.seed_demo_thread)
    after = _messages(client, auth)
    assert len(after) == len(before)


def test_demo_replies_are_deterministic():
    """Two identical mentions in demo mode produce identical reply text."""
    from backend import agent as agent_mod

    row = {"name": "swarm", "job": "Generalist", "system_prompt": ""}
    history = [{"author": "demo", "body": "what is blocking the release?"}]
    first = agent_mod.compose_demo_reply(row, history)
    second = agent_mod.compose_demo_reply(row, history)
    assert first == second
    assert first.strip()