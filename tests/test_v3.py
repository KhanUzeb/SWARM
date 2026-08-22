"""V3.2 onboarding helpers + V3.7 demo mode."""
import asyncio

import backend.agent as agent
import backend.db as db
import backend.main as main


def _clear_rate():
    main._last_write.clear()


def test_status_includes_demo(client, monkeypatch):
    monkeypatch.setenv("SWARM_DEMO", "1")
    assert client.get("/api/status").json()["demo"] is True
    monkeypatch.delenv("SWARM_DEMO", raising=False)
    assert client.get("/api/status").json()["demo"] is False


def test_register_includes_created(client):
    res = client.post("/api/register", json={"handle": "newbie"})
    assert res.status_code == 200
    body = res.json()
    assert body["created"] is True
    assert body["token"].startswith("newbie:")


def test_jobs_include_suggested_fields(client, auth):
    jobs = client.get("/api/jobs", headers=auth).json()
    chief = next(j for j in jobs if j["id"] == "chief-of-staff")
    assert chief["suggested_name"] == "chief"
    assert "attention" in chief["suggested_prompt"].lower()
    assert all("suggested_name" in j and "suggested_prompt" in j for j in jobs)


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


def test_dm_triggers_demo_reply(client, auth, monkeypatch):
    monkeypatch.setenv("SWARM_DEMO", "1")
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    monkeypatch.setattr(agent, "DEMO_STREAM_DELAY", 0)

    _clear_rate()
    token = auth["Authorization"].removeprefix("Bearer ")
    with client.websocket_connect("/ws/dm-swarm") as ws:
        ws.send_json({"token": token})
        ws.send_json({"body": "summarize this week"})
        for _ in range(80):
            event = ws.receive_json()
            if event["type"] == "message" and event["message"].get("author_kind") == "agent":
                assert event["message"]["body"].startswith("[demo mode]")
                break
        else:
            raise AssertionError("no demo agent reply")
