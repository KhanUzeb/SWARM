import asyncio

import backend.agent as agent
import backend.db as db
import backend.main as main


def _clear_rate():
    main._last_write.clear()


def test_seeded_bots_have_jobs_and_dms(client, auth):
    agents = {a["name"]: a for a in client.get("/api/agents", headers=auth).json()}
    assert agents["swarm"]["job"] == "Generalist"
    assert agents["swarm"]["status"] == "idle"
    assert agents["swarm"]["dm_channel_id"] == "dm-swarm"
    assert agents["ledger"]["job"] == "Decision log"
    assert agents["coder"]["job"] == "Code"
    assert agents["coder"]["dm_channel_id"] == "dm-coder"
    assert "fenced markdown" in agents["coder"]["system_prompt"]
    assert "\\subsection*{Code}" in agents["coder"]["system_prompt"]
    channels = {c["id"]: c for c in client.get("/api/channels", headers=auth).json()}
    assert channels["dm-swarm"]["kind"] == "dm"
    assert channels["dm-swarm"]["owner_agent"] == "swarm"
    assert channels["dm-coder"]["kind"] == "dm"
    assert channels["general"]["kind"] == "room"
    assert channels["code"]["kind"] == "room"


def test_jobs_catalog(client, auth):
    jobs = client.get("/api/jobs", headers=auth).json()
    ids = {j["id"] for j in jobs}
    assert "sales-outbound" in ids
    assert "chief-of-staff" in ids
    assert "code-engineer" in ids
    assert all("prompt" in j and "job" in j for j in jobs)


def test_create_agent_opens_dm(client, auth):
    _clear_rate()
    created = client.post(
        "/api/agents",
        json={"name": "piper", "system_prompt": "Investigate latency.", "job": "Product Performance"},
        headers=auth,
    )
    assert created.status_code == 200
    body = created.json()
    assert body["job"] == "Product Performance"
    assert body["dm_channel_id"] == "dm-piper"
    channels = {c["id"]: c for c in client.get("/api/channels", headers=auth).json()}
    assert channels["dm-piper"]["kind"] == "dm"
    assert channels["dm-piper"]["owner_agent"] == "piper"


def test_dm_message_triggers_without_mention(client, auth, monkeypatch):
    async def fake_reply(agent_row, channel_id, history, on_tools_ready=None, on_stream_start=None, on_token=None):
        if on_stream_start is not None:
            await on_stream_start()
        if on_token is not None:
            await on_token("on it")
        return {"reply": "on it", "tool_events": [], "usage": {}}

    monkeypatch.setattr("backend.agent.generate_reply", fake_reply)
    monkeypatch.setattr("backend.main.agent.generate_reply", fake_reply)

    _clear_rate()
    token = auth["Authorization"].removeprefix("Bearer ")
    with client.websocket_connect("/ws/dm-swarm") as ws:
        ws.send_json({"token": token})
        ws.send_json({"body": "summarize this week"})
        bodies = []
        for _ in range(12):
            event = ws.receive_json()
            if event["type"] == "message" and event["message"].get("author_kind") == "agent":
                bodies.append(event["message"]["body"])
                break
        assert bodies == ["on it"]


def test_room_still_requires_mention(client, auth, monkeypatch):
    async def fake_reply(*_args, **_kwargs):
        return {"reply": "should not run", "tool_events": [], "usage": {}}

    monkeypatch.setattr("backend.agent.generate_reply", fake_reply)
    monkeypatch.setattr("backend.main.agent.generate_reply", fake_reply)
    _clear_rate()
    token = auth["Authorization"].removeprefix("Bearer ")
    with client.websocket_connect("/ws/general") as ws:
        ws.send_json({"token": token})
        ws.send_json({"body": "just chatting"})
        event = ws.receive_json()
        assert event["type"] == "message"
        assert event["message"]["author_kind"] == "human"
        # No agent follow-up: a second receive would block. Check history instead.
    history = client.get("/api/channels/general/messages", headers=auth).json()
    assert not any(m["author_kind"] == "agent" for m in history)


def test_skills_crud(client, auth):
    _clear_rate()
    created = client.post(
        "/api/skills",
        json={"name": "weekly-health", "body": "Rank accounts. Do not contact anyone."},
        headers=auth,
    )
    assert created.status_code == 200
    skill = created.json()
    assert skill["name"] == "weekly-health"
    listed = client.get("/api/skills", headers=auth).json()
    assert any(s["name"] == "weekly-health" for s in listed)
    _clear_rate()
    patched = client.patch(
        f"/api/skills/{skill['id']}",
        json={"body": "Rank accounts. Cite sources."},
        headers=auth,
    )
    assert patched.status_code == 200
    assert "Cite sources" in patched.json()["body"]
    _clear_rate()
    assert client.delete(f"/api/skills/{skill['id']}", headers=auth).status_code == 200
    assert client.get("/api/skills", headers=auth).json() == []


def test_routines_and_due(client, auth, monkeypatch):
    async def fake_reply(agent_row, channel_id, history, on_tools_ready=None, on_stream_start=None, on_token=None):
        return {"reply": "digest ready", "tool_events": [], "usage": {}}

    monkeypatch.setattr("backend.agent.generate_reply", fake_reply)
    monkeypatch.setattr("backend.main.agent.generate_reply", fake_reply)

    _clear_rate()
    created = client.post(
        "/api/routines",
        json={
            "agent_name": "swarm",
            "title": "Morning digest",
            "instructions": "Summarize open questions.",
            "interval_minutes": 60,
            "enabled": True,
        },
        headers=auth,
    )
    assert created.status_code == 200
    row = created.json()
    assert row["agent_name"] == "swarm"
    assert row["enabled"] is True

    async def _force_due():
        await db.update_routine(row["id"], {"next_run_at": 1})
        return await main.run_due_routines()

    ran = asyncio.run(_force_due())
    assert ran == 1
    history = client.get("/api/channels/dm-swarm/messages", headers=auth).json()
    assert any("[routine:Morning digest]" in (m["body"] or "") for m in history)
    assert any(m["author_kind"] == "agent" and m["body"] == "digest ready" for m in history)


def test_approvals_roundtrip(client, auth, monkeypatch):
    async def fake_reply(agent_row, channel_id, history, on_tools_ready=None, on_stream_start=None, on_token=None):
        events = [{
            "tool": "request_approval",
            "args": {"action": "send outreach", "detail": "email Dana"},
            "result": "pending",
        }]
        if on_tools_ready is not None:
            await on_tools_ready(events)
        return {"reply": "waiting on you", "tool_events": events, "usage": {}}

    monkeypatch.setattr("backend.agent.generate_reply", fake_reply)
    monkeypatch.setattr("backend.main.agent.generate_reply", fake_reply)

    async def _make():
        return await db.create_approval("swarm", "dm-swarm", "send outreach", "email Dana")

    row = asyncio.run(_make())
    pending = client.get("/api/approvals", headers=auth).json()
    assert any(a["id"] == row["id"] for a in pending)
    _clear_rate()
    resolved = client.post(
        f"/api/approvals/{row['id']}/resolve",
        json={"status": "approved"},
        headers=auth,
    )
    assert resolved.status_code == 200
    assert resolved.json()["status"] == "approved"
    history = client.get("/api/channels/dm-swarm/messages", headers=auth).json()
    assert any("Approved: send outreach" in m["body"] for m in history)


def test_computer_workspace(client, auth, tmp_path, monkeypatch):
    sandbox = tmp_path / "box"
    sandbox.mkdir()
    (sandbox / "notes.md").write_text("hello", encoding="utf-8")
    monkeypatch.setattr(agent, "SANDBOX_DIR", str(sandbox))
    data = client.get("/api/computer", headers=auth).json()
    assert data["shared"] is True
    assert any(f["path"] == "notes.md" for f in data["files"])
    preview = client.get("/api/computer/file", params={"path": "notes.md"}, headers=auth).json()
    assert preview["content"] == "hello"
    assert client.get("/api/computer/file", params={"path": "../secret"}, headers=auth).status_code == 404


def test_handoff_from_agent_mention(client, auth, monkeypatch):
    async def fake_reply(agent_row, channel_id, history, on_tools_ready=None, on_stream_start=None, on_token=None):
        name = agent_row["name"]
        reply = "handoff to @ledger" if name == "swarm" else "logged"
        if on_stream_start is not None:
            await on_stream_start()
        return {"reply": reply, "tool_events": [], "usage": {}}

    monkeypatch.setattr("backend.agent.generate_reply", fake_reply)
    monkeypatch.setattr("backend.main.agent.generate_reply", fake_reply)
    _clear_rate()
    token = auth["Authorization"].removeprefix("Bearer ")
    with client.websocket_connect("/ws/general") as ws:
        ws.send_json({"token": token})
        ws.send_json({"body": "please @swarm then pass it on"})
        authors = []
        for _ in range(16):
            event = ws.receive_json()
            if event["type"] == "message" and event["message"].get("author_kind") == "agent":
                authors.append(event["message"]["author"])
                if "swarm" in authors and "ledger" in authors:
                    break
        assert "swarm" in authors
        assert "ledger" in authors


def test_dm_offers_tools():
    assert agent.should_offer_tools(
        [{"author_kind": "human", "body": "hi"}],
        channel_kind="dm",
    )
    assert not agent.should_offer_tools(
        [{"author_kind": "human", "body": "hi"}],
        channel_kind="room",
    )
    assert agent.should_offer_tools(
        [{"author_kind": "system", "body": "[routine:digest] go"}],
        channel_kind="room",
    )


def test_workspace_write_stays_in_sandbox(tmp_path, monkeypatch):
    sandbox = tmp_path / "box"
    sandbox.mkdir()
    monkeypatch.setattr(agent, "SANDBOX_DIR", str(sandbox))
    result = asyncio.run(agent._run_write_workspace("reports/a.md", "# hi"))
    assert "wrote reports/a.md" in result
    assert (sandbox / "reports" / "a.md").read_text(encoding="utf-8") == "# hi"
    denied = asyncio.run(agent._run_write_workspace("../escape.md", "nope"))
    assert "invalid" in denied
    assert not (tmp_path / "escape.md").exists()
