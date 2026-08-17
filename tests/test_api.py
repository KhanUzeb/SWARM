import backend.main as main
from starlette.websockets import WebSocketDisconnect


def _clear_rate():
    main._last_write.clear()


def test_status_reports_groq_unset(client):
    res = client.get("/api/status")
    assert res.status_code == 200
    assert res.json() == {"groq": False, "openrouter": False}


def test_status_reports_groq_set(client, monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "gsk_test")
    res = client.get("/api/status")
    assert res.status_code == 200
    assert res.json() == {"groq": True, "openrouter": False}


def test_register_and_409(client):
    res = client.post("/api/register", json={"handle": "uzeb"})
    assert res.status_code == 200
    body = res.json()
    assert body["handle"] == "uzeb"
    assert body["token"].startswith("uzeb:")

    again = client.post("/api/register", json={"handle": "uzeb"})
    assert again.status_code == 409


def test_write_requires_bearer(client):
    res = client.post("/api/channels", json={"name": "secret"})
    assert res.status_code == 401


def test_impersonation_rejected(client, auth):
    res = client.post(
        "/api/channels/general/messages",
        json={"author": "not-uzeb", "body": "hi"},
        headers=auth,
    )
    assert res.status_code == 403


def test_post_and_history(client, auth):
    res = client.post(
        "/api/channels/general/messages",
        json={"author": "uzeb", "body": "shipping the fix"},
        headers=auth,
    )
    assert res.status_code == 200
    msg = res.json()
    assert msg["author"] == "uzeb"
    assert msg["body"] == "shipping the fix"

    history = client.get("/api/channels/general/messages").json()
    assert any(m["id"] == msg["id"] for m in history)
    assert "reactions" in history[0]


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
    history = client.get("/api/channels/general/messages").json()
    row = next(m for m in history if m["id"] == mid)
    assert len(row["reactions"]) == 1


def test_pagination_before_id(client, auth):
    ids = []
    for i in range(4):
        _clear_rate()
        msg = client.post(
            "/api/channels/general/messages",
            json={"author": "uzeb", "body": f"m{i}"},
            headers=auth,
        ).json()
        ids.append(msg["id"])
    page = client.get(f"/api/channels/general/messages?limit=2&before_id={ids[-1]}")
    assert page.status_code == 200
    bodies = [m["body"] for m in page.json()]
    assert bodies[-1] == "m2"
    assert "m3" not in bodies


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
    data = client.get(f"/api/messages/{parent['id']}/thread").json()
    assert data["parent"]["id"] == parent["id"]
    assert len(data["replies"]) == 1
    assert data["replies"][0]["id"] == reply["id"]
    assert "reactions" in data["parent"]


def test_thread_404(client):
    assert client.get("/api/messages/99999/thread").status_code == 404


def test_ws_bad_token_closes_4001(client):
    with client.websocket_connect("/ws/general") as ws:
        ws.send_json({"token": "nope:invalid"})
        try:
            ws.receive_json()
            raise AssertionError("expected close")
        except WebSocketDisconnect as exc:
            assert exc.code == 4001


def test_ws_catchup_after_last_seen_id(client, auth, token):
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
    async def fake_reply(agent_row, channel_id, history, on_tools_ready=None, on_stream_start=None, on_token=None):
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
        for _ in range(8):
            event = ws.receive_json()
            types.append(event["type"])
            if event["type"] == "message" and event["message"].get("author_kind") == "agent":
                bodies.append(event["message"]["body"])
                break
        assert "agent_stream_start" in types
        assert "agent_token" in types
        assert bodies == ["hello world"]
