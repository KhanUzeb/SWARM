"""Need-based bot provisioning, reaction toggle, summary accumulation."""
from __future__ import annotations

import asyncio
import json

import backend.agent as agent_mod
import backend.db as db
from backend.tools.registry import get_registry


def _run(coro):
    return asyncio.run(coro)


def _exec(args, allowed=("create_agent",)):
    reg = get_registry()
    return _run(reg.execute(
        "create_agent", args, agent_name="swarm", channel_id="general",
        allowed=list(allowed), sandbox_dir="", shell_runner=None,
        workspace_helpers={}))


def test_create_agent_happy_path(client, auth):
    out = _exec({
        "name": "coach-fit", "job": "Fitness coach",
        "system_prompt": "You are a fitness coach. Track workouts with remember.",
    })
    assert "created bot 'coach-fit'" in out
    assert "#dm-coach-fit" in out
    row = _run(db.fetch_agent("coach-fit"))
    assert row["job"] == "Fitness coach"
    tools = db.parse_tools(row["tools"])
    assert "create_agent" not in tools  # no spawn chains
    assert not any(t.startswith("system_") for t in tools)
    # The boot backfill must not silently re-expand the locked list.
    _run(db.init_db())
    tools = db.parse_tools(_run(db.fetch_agent("coach-fit"))["tools"])
    assert "create_agent" not in tools
    assert not any(t.startswith("system_") for t in tools)
    assert _run(db.get_channel("dm-coach-fit"))["kind"] == "dm"
    # Sidebar agents list shows the new bot.
    agents = client.get("/api/agents", headers=auth).json()
    assert "coach-fit" in [a["name"] for a in agents]


def test_create_agent_rejects_bad_and_duplicate_names(client, auth):
    assert "need a bot name" in _exec({"name": "No Good!", "job": "j", "system_prompt": "x" * 40})
    assert "need a system_prompt" in _exec({"name": "thin", "job": "j", "system_prompt": "short"})
    _exec({"name": "once", "job": "j", "system_prompt": "a proper role description here"})
    assert "already exists" in _exec({"name": "once", "job": "j", "system_prompt": "another proper role description"})
    assert "user handle" in _exec({"name": "uzeb", "job": "j", "system_prompt": "a proper role description here"})
    assert "collides with a channel" in _exec(
        {"name": "general", "job": "j", "system_prompt": "a proper role description here"})
    assert "safe default set" in _exec({
        "name": "pick", "job": "j", "system_prompt": "a proper role description here",
        "tools": ["nope-not-a-tool"],
    })


def test_swarm_backfilled_with_create_agent(client):
    async def strip():
        row = await db.fetch_agent("swarm")
        names = [t for t in db.parse_tools(row["tools"]) if t != "create_agent"]
        async with __import__("aiosqlite").connect(db.DB_PATH) as conn:
            await conn.execute("UPDATE agents SET tools = ? WHERE name = 'swarm'", (json.dumps(names),))
            await conn.commit()

    _run(strip())
    _run(db.init_db())
    assert "create_agent" in db.parse_tools(_run(db.fetch_agent("swarm"))["tools"])


def test_tool_gate_opens_for_bot_requests():
    assert agent_mod.should_offer_tools(
        [{"author_kind": "human", "author": "u", "body": "create a bot to track my workouts"}])
    assert agent_mod.should_offer_tools(
        [{"author_kind": "human", "author": "u", "body": "I need a research assistant for this"}])
    assert not agent_mod.should_offer_tools(
        [{"author_kind": "human", "author": "u", "body": "hi"}])


def test_reaction_add_and_remove_toggle(client, auth):
    posted = client.post(
        "/api/channels/general/messages",
        json={"author": "uzeb", "body": "toggle me"}, headers=auth).json()
    mid = posted["id"]
    assert client.post(
        f"/api/messages/{mid}/reactions",
        json={"author": "uzeb", "emoji": "🎉"}, headers=auth).status_code == 204
    history = client.get("/api/channels/general/messages", headers=auth).json()
    assert [r["emoji"] for r in history[0]["reactions"]] == ["🎉"]
    res = client.delete(f"/api/messages/{mid}/reactions?emoji=🎉", headers=auth)
    assert res.json() == {"ok": True, "removed": True}
    res = client.delete(f"/api/messages/{mid}/reactions?emoji=🎉", headers=auth)
    assert res.json() == {"ok": True, "removed": False}
    assert client.delete("/api/messages/999999/reactions?emoji=🎉", headers=auth).status_code == 404


def test_summary_accumulates_and_dedupes(client):
    first = _run(db.append_summary("swarm", "general", "Earlier: discussed launch."))
    assert first["body"] == "Earlier: discussed launch."
    second = _run(db.append_summary("swarm", "general", "Later: picked a date."))
    assert "discussed launch" in second["body"] and "picked a date" in second["body"]
    # Same extract twice is a no-op.
    assert _run(db.append_summary("swarm", "general", "Later: picked a date."))["id"] == second["id"]
    # Long histories stay capped.
    big = _run(db.append_summary("swarm", "general", "x" * 5000))
    assert len(big["body"]) <= 3100
    assert "picked a date" not in big["body"]  # oldest content trimmed first
