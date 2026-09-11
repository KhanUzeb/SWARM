"""Admin role, human DMs, teams, audit export, and bot archive."""
import asyncio

import backend.db as db
import backend.main as main


def _clear_rate():
    main._last_write.clear()


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _register(client, handle: str) -> dict:
    res = client.post("/api/register", json={"handle": handle})
    assert res.status_code == 200, res.text
    return res.json()


def test_first_user_is_admin_second_is_member(client):
    first = _register(client, "uzeb")
    assert first["role"] == "admin"
    me = client.get("/api/me", headers=_auth(first["token"])).json()
    assert me["role"] == "admin"

    second = _register(client, "maya")
    assert second["role"] == "member"
    me2 = client.get("/api/me", headers=_auth(second["token"])).json()
    assert me2["role"] == "member"

    people = client.get("/api/people", headers=_auth(first["token"])).json()
    handles = {p["handle"]: p["role"] for p in people}
    assert handles["uzeb"] == "admin"
    assert handles["maya"] == "member"

    gate = client.get("/api/status").json()
    assert gate["onboarded"] is False
    assert gate["admins"] == ["uzeb"]
    marked = client.post("/api/workspace/onboarded", headers=_auth(first["token"]))
    assert marked.status_code == 200
    assert client.get("/api/status").json()["onboarded"] is True


def test_member_cannot_create_or_archive_bots(client, auth):
    member = _register(client, "maya")
    headers = _auth(member["token"])
    res = client.post(
        "/api/agents",
        json={"name": "scribe", "system_prompt": "take notes", "job": "Scribe"},
        headers=headers,
    )
    assert res.status_code == 403

    archive = client.delete("/api/agents/swarm", headers=headers)
    assert archive.status_code == 403

    # admin (uzeb from auth fixture) still can
    created = client.post(
        "/api/agents",
        json={"name": "scribe", "system_prompt": "take notes", "job": "Scribe"},
        headers=auth,
    )
    assert created.status_code == 200
    _clear_rate()
    gone = client.delete("/api/agents/scribe", headers=auth)
    assert gone.status_code == 200
    assert gone.json()["archived"] is True
    listed = client.get("/api/agents", headers=auth).json()
    assert all(a["name"] != "scribe" for a in listed)
    assert client.get("/api/agents/scribe", headers=auth).status_code == 404


def test_admin_password_required_to_reclaim_handle(local_client):
    first = local_client.post(
        "/api/register", json={"handle": "uzeb", "password": "secret12"},
    )
    assert first.status_code == 200
    body = first.json()
    assert body["role"] == "admin"
    assert body["onboarded"] is False

    denied = local_client.post("/api/register", json={"handle": "uzeb"})
    assert denied.status_code == 403

    wrong = local_client.post(
        "/api/register", json={"handle": "uzeb", "password": "nope"},
    )
    assert wrong.status_code == 403

    again = local_client.post(
        "/api/register", json={"handle": "uzeb", "password": "secret12"},
    )
    assert again.status_code == 200
    session = again.json()
    assert session["created"] is False
    assert session["role"] == "admin"
    assert session["token"] != body["token"]
    assert local_client.get("/api/me", headers=_auth(session["token"])).status_code == 200

    # Loopback members are rejected from bot creation exactly like remote ones.
    loopback_member = _register(local_client, "maya")
    assert loopback_member["role"] == "member"
    res = local_client.post(
        "/api/agents",
        json={"name": "chief", "system_prompt": "run the room", "job": "Code"},
        headers=_auth(loopback_member["token"]),
    )
    assert res.status_code == 403


def test_people_dm_is_private(client, auth):
    member = _register(client, "maya")
    outsider = _register(client, "rio")
    _clear_rate()
    opened = client.post("/api/dms", json={"handle": "maya"}, headers=auth)
    assert opened.status_code == 200
    dm = opened.json()
    assert dm["kind"] == "people"
    assert set(dm["people"]) == {"uzeb", "maya"}

    _clear_rate()
    posted = client.post(
        f"/api/channels/{dm['id']}/messages",
        json={"author": "uzeb", "body": "private ship plan"},
        headers=auth,
    )
    assert posted.status_code == 200

    maya_channels = {c["id"] for c in client.get("/api/channels", headers=_auth(member["token"])).json()}
    rio_channels = {c["id"] for c in client.get("/api/channels", headers=_auth(outsider["token"])).json()}
    assert dm["id"] in maya_channels
    assert dm["id"] not in rio_channels
    assert client.get(f"/api/channels/{dm['id']}/messages", headers=_auth(outsider["token"])).status_code == 403

    hits = client.get("/api/search?q=ship", headers=_auth(outsider["token"])).json()
    assert hits == []
    found = client.get("/api/search?q=ship", headers=auth).json()
    assert any(h["id"] == posted.json()["id"] for h in found)


def test_seeded_core_team_and_create_team(client, auth, monkeypatch):
    teams = client.get("/api/teams", headers=auth).json()
    core = next(t for t in teams if t["id"] == "core")
    assert core["members"] == ["swarm", "ledger", "coder"]

    created = client.post(
        "/api/teams",
        json={"id": "launch", "name": "Launch", "members": ["swarm", "ledger"], "description": "ship pod"},
        headers=auth,
    )
    assert created.status_code == 200
    assert created.json()["members"] == ["swarm", "ledger"]

    member = _register(client, "maya")
    denied = client.post(
        "/api/teams",
        json={"id": "other", "name": "Other", "members": ["swarm"]},
        headers=_auth(member["token"]),
    )
    assert denied.status_code == 403

    async def fake_reply(agent_row, channel_id, history, on_tools_ready=None, on_stream_start=None, on_token=None):
        return {"reply": f"ok {agent_row['name']}", "tool_events": [], "usage": {}}

    monkeypatch.setattr("backend.agent.generate_reply", fake_reply)
    monkeypatch.setattr("backend.main.agent.generate_reply", fake_reply)

    _clear_rate()
    token = auth["Authorization"].removeprefix("Bearer ")
    with client.websocket_connect("/ws/general") as ws:
        ws.send_json({"token": token})
        ws.send_json({"body": "@launch draft the note"})
        authors = []
        for _ in range(40):
            event = ws.receive_json()
            if event["type"] == "message" and event["message"].get("author_kind") == "agent":
                authors.append(event["message"]["author"])
                if len(authors) >= 2:
                    break
        else:
            raise AssertionError("team mention did not run both bots")
    assert authors[:2] == ["swarm", "ledger"]


def test_export_json_and_csv(client, auth):
    _clear_rate()
    client.post(
        "/api/channels/general/messages",
        json={"author": "uzeb", "body": "audit me"},
        headers=auth,
    )
    payload = client.get("/api/channels/general/export", headers=auth).json()
    assert payload["channel"]["id"] == "general"
    assert any(m["body"] == "audit me" for m in payload["messages"])

    csv_res = client.get("/api/channels/general/export?format=csv", headers=auth)
    assert csv_res.status_code == 200
    assert "text/csv" in csv_res.headers["content-type"]
    assert "audit me" in csv_res.text
    assert csv_res.text.splitlines()[0].startswith("id,created_at")


def test_archived_bot_is_not_mentioned(client, auth, monkeypatch):
    monkeypatch.setenv("SWARM_DEMO", "1")
    monkeypatch.setattr("backend.agent.DEMO_STREAM_DELAY", 0)
    created = client.post(
        "/api/agents",
        json={"name": "ghost", "system_prompt": "haunt", "job": "Ghost"},
        headers=auth,
    )
    assert created.status_code == 200
    _clear_rate()
    client.delete("/api/agents/ghost", headers=auth)

    async def check():
        agents = await db.list_agents()
        assert all(a["name"] != "ghost" for a in agents)
        mentioned = __import__("backend.agent", fromlist=["find_mentioned_agents"]).find_mentioned_agents(
            "@ghost hello", agents,
        )
        assert mentioned == []

    asyncio.run(check())
