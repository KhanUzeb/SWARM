import asyncio

import backend.agent as agent
import backend.db as db
import backend.main as main


def _clear_rate():
    main._last_write.clear()


def test_is_agent_error():
    assert agent.is_agent_error("[agent error: rate limited — try again in a moment]")
    assert not agent.is_agent_error("hello")
    assert not agent.is_agent_error("")


def test_retry_agent_message_deletes_error_and_reruns(client, auth, monkeypatch):
    ran = {"count": 0}

    async def fake_reply(*_args, **_kwargs):
        ran["count"] += 1
        return {"reply": "recovered", "tool_events": [], "usage": {}}

    monkeypatch.setattr(main.agent, "generate_reply", fake_reply)

    async def seed():
        await db.add_message("general", "uzeb", "please help", "human")
        return await db.add_message(
            "general",
            "swarm",
            "[agent error: couldn't reach the model]",
            "agent",
        )

    error_msg = asyncio.run(seed())
    _clear_rate()

    res = client.post(f"/api/messages/{error_msg['id']}/retry", headers=auth)
    assert res.status_code == 200
    body = res.json()
    assert body["ok"] is True
    assert body["agent"] == "swarm"

    history = client.get("/api/channels/general/messages", headers=auth).json()
    assert not any(m["id"] == error_msg["id"] for m in history)
    assert ran["count"] == 1


def test_retry_rejects_non_error_messages(client, auth):
    posted = client.post(
        "/api/channels/general/messages",
        json={"author": "uzeb", "body": "normal message"},
        headers=auth,
    ).json()
    _clear_rate()
    res = client.post(f"/api/messages/{posted['id']}/retry", headers=auth)
    assert res.status_code == 400
