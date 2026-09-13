"""Agent scale: delegation/orchestration, avatars, work budgets."""
from __future__ import annotations

import asyncio


def _run(coro):
    return asyncio.run(coro)


def test_delegate_task_is_a_builtin_tool():
    from backend.tools.registry import BUILTIN_SCHEMAS, get_registry

    assert "delegate_task" in BUILTIN_SCHEMAS
    assert "delegate_task" in get_registry().all_names()
    params = BUILTIN_SCHEMAS["delegate_task"]["function"]["parameters"]
    assert set(params["required"]) == {"agent", "task"}


def test_delegation_guards(client):
    import backend.agent as agent_mod

    async def _go():
        # Unknown bot.
        assert "no active bot" in await agent_mod.generate_delegate_reply(
            "ghost", "do things", "general", parent_name="swarm", depth=0)
        # Self-delegation refused.
        assert "itself" in await agent_mod.generate_delegate_reply(
            "swarm", "do things", "general", parent_name="swarm", depth=0)
        # Depth cap stops chains.
        assert "depth cap" in await agent_mod.generate_delegate_reply(
            "ledger", "do things", "general", parent_name="swarm",
            depth=agent_mod.DELEGATE_DEPTH_CAP)
        # Empty task refused.
        assert "needs a task" in await agent_mod.generate_delegate_reply(
            "ledger", "  ", "general", parent_name="swarm", depth=0)

    _run(_go())


def test_delegation_runs_subagent_in_demo_mode(client, monkeypatch):
    """With SWARM_DEMO=1 the sub-agent answers deterministically headlessly."""
    import backend.agent as agent_mod

    monkeypatch.setenv("SWARM_DEMO", "1")

    async def _go():
        out = await agent_mod.generate_delegate_reply(
            "ledger", "log the decision", "general", parent_name="swarm", depth=0)
        assert out.startswith("@ledger reports:")

    _run(_go())


def test_avatar_create_patch_api(client, auth):
    import backend.main as main

    res = client.post("/api/agents", headers=auth, json={
        "name": "pfpbot", "display_name": "Pfp Bot",
        "system_prompt": "You are Pfp, a helpful teammate.",
        "avatar": "🦊",
    })
    assert res.status_code == 200, res.text
    assert res.json()["avatar"] == "🦊"

    got = client.get("/api/agents/pfpbot", headers=auth)
    assert got.status_code == 200
    assert got.json()["avatar"] == "🦊"

    main._last_write.clear()
    patched = client.patch("/api/agents/pfpbot", headers=auth, json={
        "avatar": "https://example.com/pfp.png",
    })
    assert patched.status_code == 200
    assert patched.json()["avatar"] == "https://example.com/pfp.png"

    listed = client.get("/api/agents", headers=auth)
    row = next(a for a in listed.json() if a["name"] == "pfpbot")
    assert row["avatar"] == "https://example.com/pfp.png"


def test_avatar_too_long_rejected(client, auth):
    res = client.post("/api/agents", headers=auth, json={
        "name": "bigpfp", "system_prompt": "You are Big, a helpful teammate.",
        "avatar": "x" * 501,
    })
    assert res.status_code == 422


def test_delegate_disabled_when_not_allowed():
    from backend.tools.registry import get_registry

    async def _go():
        out = await get_registry().execute(
            "delegate_task", {"agent": "ledger", "task": "hi"},
            agent_name="swarm", channel_id="general",
            allowed=["remember"], sandbox_dir="", shell_runner=lambda c: "",
            workspace_helpers={},
        )
        assert "disabled for this agent" in out

    _run(_go())
