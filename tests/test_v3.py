"""V3.2 onboarding helpers + V3.7 demo mode."""
import asyncio

import backend.agent as agent
import backend.db as db


def test_demo_mode_reply_without_groq(client, auth, monkeypatch):
    monkeypatch.setenv("SWARM_DEMO", "1")
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)

    async def run():
        row = await db.get_agent("swarm")
        history = [{"author": "uzeb", "author_kind": "human", "body": "What's blocking ship?"}]
        result = await agent.generate_reply(row, "general", history)
        assert result["reply"].startswith("[demo mode]")
        assert "blocking" in result["reply"].lower() or "ship" in result["reply"].lower()
        assert result["tool_events"] == []

    asyncio.run(run())


def test_demo_seed_general_thread(client, auth, monkeypatch):
    monkeypatch.setenv("SWARM_DEMO", "1")
    import backend.db as db_mod

    async def reinit():
        await db_mod.init_db()

    asyncio.run(reinit())
    msgs = client.get("/api/channels/general/messages", headers=auth).json()
    assert len(msgs) >= 4
    assert msgs[0]["body"] == "What's blocking the release?"
    assert msgs[0]["author_kind"] == "human"
    assert any(m["author_kind"] == "agent" for m in msgs)
