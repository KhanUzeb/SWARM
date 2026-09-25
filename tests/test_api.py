import backend.main as main
from starlette.websockets import WebSocketDisconnect


def _clear_rate():
    main._last_write.clear()


def test_status_reports_provider_readiness(client, monkeypatch):
    res = client.get("/api/status")
    assert res.status_code == 200
    body = res.json()
    assert body["groq"] is False
    assert body["openrouter"] is False
    assert body["demo"] is False
    assert "ai_providers" not in body

    monkeypatch.setenv("GROQ_API_KEY", "gsk_test")
    body = client.get("/api/status").json()
    assert body["groq"] is True
    assert body["openrouter"] is False

    monkeypatch.setenv("SWARM_DEMO", "1")
    assert client.get("/api/status").json()["demo"] is True
    monkeypatch.delenv("SWARM_DEMO", raising=False)
    assert client.get("/api/status").json()["demo"] is False


def test_register_and_409(client):
    res = client.post("/api/register", json={"handle": "uzeb"})
    assert res.status_code == 200
    body = res.json()
    assert body["handle"] == "uzeb"
    assert body["token"].startswith("uzeb:")
    assert body["created"] is True
    assert body["onboarded"] is False

    again = client.post("/api/register", json={"handle": "uzeb"})
    assert again.status_code == 409


def test_auth_is_required(client):
    assert client.get("/api/channels").status_code == 401
    assert client.get("/api/channels/general/messages").status_code == 401
    assert client.post("/api/channels", json={"name": "secret"}).status_code == 401


def test_forbidden_browser_origin(client, auth):
    res = client.get(
        "/api/channels",
        headers={**auth, "Origin": "https://evil.example", "X-Swarm-Client": "web"},
    )
    assert res.status_code == 403


def test_post_guards_reject_spoofing(client, auth):
    res = client.post(
        "/api/channels/general/messages",
        json={"author": "not-uzeb", "body": "hi"},
        headers=auth,
    )
    assert res.status_code == 403
    forged = client.post(
        "/api/channels/general/messages",
        json={"author": "uzeb", "body": "forged", "author_kind": "agent"},
        headers=auth,
    )
    assert forged.status_code == 422


def test_post_history_and_pagination(client, auth):
    res = client.post(
        "/api/channels/general/messages",
        json={"author": "uzeb", "body": "shipping the fix"},
        headers=auth,
    )
    assert res.status_code == 200
    msg = res.json()
    assert msg["author"] == "uzeb"
    assert msg["body"] == "shipping the fix"

    history = client.get("/api/channels/general/messages", headers=auth).json()
    assert any(m["id"] == msg["id"] for m in history)
    assert "reactions" in history[0]

    ids = [msg["id"]]
    for i in range(3):
        _clear_rate()
        ids.append(client.post(
            "/api/channels/general/messages",
            json={"author": "uzeb", "body": f"m{i}"},
            headers=auth,
        ).json()["id"])
    page = client.get(f"/api/channels/general/messages?limit=2&before_id={ids[-1]}", headers=auth)
    assert page.status_code == 200
    bodies = [m["body"] for m in page.json()]
    assert bodies[-1] == "m1"
    assert "m2" not in bodies

    # The after-cursor path caps rows server-side too.
    import asyncio
    import backend.db as db_mod
    rows = asyncio.run(db_mod.get_history_after("general", 0, limit=2))
    assert len(rows) == 2


def test_invalid_message_does_not_consume_write_quota(client, auth):
    _clear_rate()
    invalid = client.post(
        "/api/channels/general/messages",
        json={"author": "uzeb", "body": "bad", "parent_id": 999999},
        headers=auth,
    )
    assert invalid.status_code == 404
    valid = client.post(
        "/api/channels/general/messages",
        json={"author": "uzeb", "body": "valid immediately after"},
        headers=auth,
    )
    assert valid.status_code == 200


def test_rate_limit(client, auth):
    client.post(
        "/api/channels/general/messages",
        json={"author": "uzeb", "body": "one"},
        headers=auth,
    )
    res = client.post(
        "/api/channels/general/messages",
        json={"author": "uzeb", "body": "two"},
        headers=auth,
    )
    assert res.status_code == 429


def test_reactions_idempotent(client, auth):
    _clear_rate()
    posted = client.post(
        "/api/channels/general/messages",
        json={"author": "uzeb", "body": "react to me"},
        headers=auth,
    ).json()
    mid = posted["id"]
    r1 = client.post(
        f"/api/messages/{mid}/reactions",
        json={"author": "uzeb", "emoji": "🔥"},
        headers=auth,
    )
    assert r1.status_code == 204
    _clear_rate()
    r2 = client.post(
        f"/api/messages/{mid}/reactions",
        json={"author": "uzeb", "emoji": "🔥"},
        headers=auth,
    )
    assert r2.status_code == 204
    history = client.get("/api/channels/general/messages", headers=auth).json()
    row = next(m for m in history if m["id"] == mid)
    assert len(row["reactions"]) == 1


def test_thread_endpoint(client, auth):
    _clear_rate()
    parent = client.post(
        "/api/channels/general/messages",
        json={"author": "uzeb", "body": "root"},
        headers=auth,
    ).json()
    _clear_rate()
    reply = client.post(
        "/api/channels/general/messages",
        json={"author": "uzeb", "body": "child", "parent_id": parent["id"]},
        headers=auth,
    ).json()
    data = client.get(f"/api/messages/{parent['id']}/thread", headers=auth).json()
    assert data["parent"]["id"] == parent["id"]
    assert len(data["replies"]) == 1
    assert data["replies"][0]["id"] == reply["id"]
    assert "reactions" in data["parent"]
    assert client.get("/api/messages/99999/thread", headers=auth).status_code == 404

    # Cascade delete removes the thread; missing ids 404.
    _clear_rate()
    gone = client.delete(f"/api/messages/{parent['id']}", headers=auth)
    assert gone.status_code == 200
    assert parent["id"] in gone.json()["ids"]
    history = client.get("/api/channels/general/messages", headers=auth).json()
    assert all(m["id"] != parent["id"] for m in history)
    _clear_rate()
    assert client.delete("/api/messages/99999", headers=auth).status_code == 404


def test_create_and_delete_channel(client, auth):
    _clear_rate()
    created = client.post(
        "/api/channels",
        json={"name": "release-notes", "topic": "ship talk"},
        headers=auth,
    )
    assert created.status_code == 200
    assert created.json()["id"] == "release-notes"
    _clear_rate()
    client.post(
        "/api/channels/release-notes/messages",
        json={"author": "uzeb", "body": "temp"},
        headers=auth,
    )
    _clear_rate()
    deleted = client.delete("/api/channels/release-notes", headers=auth)
    assert deleted.status_code == 200
    ids = {c["id"] for c in client.get("/api/channels", headers=auth).json()}
    assert "release-notes" not in ids
    _clear_rate()
    dm = client.delete("/api/channels/dm-swarm", headers=auth)
    assert dm.status_code == 400


def test_ws_auth_and_catchup(client, auth, token):
    with client.websocket_connect("/ws/general") as ws:
        ws.send_json({"token": "nope:invalid"})
        try:
            ws.receive_json()
            raise AssertionError("expected close")
        except WebSocketDisconnect as exc:
            assert exc.code == 4001

    _clear_rate()
    first = client.post(
        "/api/channels/general/messages",
        json={"author": "uzeb", "body": "old"},
        headers=auth,
    ).json()
    _clear_rate()
    second = client.post(
        "/api/channels/general/messages",
        json={"author": "uzeb", "body": "new"},
        headers=auth,
    ).json()

    with client.websocket_connect("/ws/general") as ws:
        ws.send_json({"token": token, "last_seen_id": first["id"]})
        event = ws.receive_json()
        assert event["type"] == "message"
        assert event["message"]["id"] == second["id"]
        assert event["message"]["body"] == "new"
        assert "reactions" in event["message"]


def test_mention_streams_then_persists(client, auth, monkeypatch):
    async def fake_reply(agent_row, channel_id, history, on_tools_ready=None, on_stream_start=None, on_token=None, **_kwargs):
        if on_tools_ready is not None:
            await on_tools_ready([])
        if on_stream_start is not None:
            await on_stream_start()
        if on_token is not None:
            await on_token("hello ")
            await on_token("world")
        return {"reply": "hello world", "tool_events": [], "usage": {}}

    monkeypatch.setattr("backend.agent.generate_reply", fake_reply)
    monkeypatch.setattr("backend.main.agent.generate_reply", fake_reply)

    _clear_rate()
    with client.websocket_connect("/ws/general") as ws:
        ws.send_json({"token": auth["Authorization"].removeprefix("Bearer ")})
        ws.send_json({"body": "hey @swarm"})
        types = []
        bodies = []
        # The channel also carries work-lifecycle frames now, so read until
        # the agent reply lands instead of assuming a fixed frame count.
        for _ in range(30):
            event = ws.receive_json()
            types.append(event["type"])
            if event["type"] == "message" and event["message"].get("author_kind") == "agent":
                bodies.append(event["message"]["body"])
                break
        assert "agent_stream_start" in types
        assert "agent_token" in types
        assert bodies == ["hello world"]
