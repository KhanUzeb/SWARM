import asyncio

import backend.agent as agent
import backend.db as db
import backend.main as main


def _clear_rate():
    main._last_write.clear()


def test_seeded_bots_have_jobs_dms_and_tool_splits(client, auth):
    agents = {a["name"]: a for a in client.get("/api/agents", headers=auth).json()}
    assert agents["swarm"]["display_name"] == "Swarm"
    assert agents["swarm"]["job"] == "Generalist"
    assert agents["ledger"]["display_name"] == "Ledger"
    assert agents["coder"]["display_name"] == "Coder"
    assert agents["swarm"]["status"] == "idle"
    assert agents["swarm"]["dm_channel_id"] == "dm-swarm"
    assert agents["ledger"]["job"] == "Decision log"
    assert agents["coder"]["job"] == "Code"
    assert agents["coder"]["dm_channel_id"] == "dm-coder"
    assert "fenced markdown" in agents["coder"]["system_prompt"]
    assert "\\subsection*{Code}" in agents["coder"]["system_prompt"]
    # Default tool splits and windows.
    assert "read_only_shell" in agents["swarm"]["tools"]
    assert "remember" in agents["swarm"]["tools"]
    assert "computer_run" in agents["swarm"]["tools"]
    assert "read_only_shell" not in agents["ledger"]["tools"]
    assert "computer_run" not in agents["ledger"]["tools"]
    assert agents["swarm"]["history_window"] == 12
    assert agents["ledger"]["max_tool_calls"] == 6
    assert client.get("/api/agents/swarm", headers=auth).json()["memories"] == []
    assert client.get("/api/agents/nope", headers=auth).status_code == 404
    channels = {c["id"]: c for c in client.get("/api/channels", headers=auth).json()}
    assert channels["dm-swarm"]["kind"] == "dm"
    assert channels["dm-swarm"]["owner_agent"] == "swarm"
    assert channels["dm-coder"]["kind"] == "dm"
    assert channels["general"]["kind"] == "room"
    assert channels["code"]["kind"] == "room"


def test_jobs_catalog(client, auth):
    jobs = client.get("/api/jobs", headers=auth).json()
    ids = {j["id"] for j in jobs}
    assert {"sales-outbound", "chief-of-staff", "code-engineer"} <= ids
    assert all("prompt" in j and "job" in j for j in jobs)
    chief = next(j for j in jobs if j["id"] == "chief-of-staff")
    assert chief["suggested_name"] == "chief"
    assert "attention" in chief["suggested_prompt"].lower()
    assert all("suggested_name" in j and "suggested_prompt" in j for j in jobs)
    assert "Chief of staff" in chief["profile"]
    profile_ids = {(r["kind"], r["id"]) for r in client.get("/api/profiles", headers=auth).json()}
    assert ("bot", "swarm") in profile_ids
    assert ("job", "chief-of-staff") in profile_ids
    assert ("job", "sales-outbound") in profile_ids


def test_agent_lifecycle_create_patch_duplicate(client, auth):
    """One canonical agent lifecycle: create (+DM opens), patch, duplicate 409."""
    _clear_rate()
    created = client.post(
        "/api/agents",
        json={"name": "piper", "system_prompt": "Investigate latency.", "job": "Product Performance",
              "tools": ["remember", "recall"], "history_window": 8},
        headers=auth,
    )
    assert created.status_code == 200
    body = created.json()
    assert body["job"] == "Product Performance"
    assert body["tools"] == ["remember", "recall"]
    assert body["history_window"] == 8
    assert body["dm_channel_id"] == "dm-piper"
    channels = {c["id"]: c for c in client.get("/api/channels", headers=auth).json()}
    assert channels["dm-piper"]["kind"] == "dm"
    assert channels["dm-piper"]["owner_agent"] == "piper"

    _clear_rate()
    patched = client.patch(
        "/api/agents/piper",
        json={"history_window": 20, "channel_scope": "general"},
        headers=auth,
    )
    assert patched.status_code == 200
    assert patched.json()["history_window"] == 20
    assert patched.json()["channel_scope"] == "general"

    _clear_rate()
    again = client.post(
        "/api/agents",
        json={"name": "piper", "system_prompt": "dup"},
        headers=auth,
    )
    assert again.status_code == 409

    # Display names slug into stable agent names.
    _clear_rate()
    slugged = client.post(
        "/api/agents",
        json={"display_name": "Maya Chen", "system_prompt": "Help with ops.", "job": "Operations"},
        headers=auth,
    )
    assert slugged.status_code == 200
    assert slugged.json()["name"] == "maya-chen"
    _clear_rate()
    renamed = client.patch("/api/agents/maya-chen", json={"display_name": "Maya"}, headers=auth)
    assert renamed.status_code == 200
    assert renamed.json()["display_name"] == "Maya"
    assert renamed.json()["name"] == "maya-chen"

    # Unknown tools are rejected at creation time.
    _clear_rate()
    bad = client.post(
        "/api/agents",
        json={"name": "badtools", "system_prompt": "test", "tools": ["not_a_real_tool_xyz"]},
        headers=auth,
    )
    assert bad.status_code == 400


def test_dm_triggers_without_mention_room_requires_it(client, auth, monkeypatch):
    async def fake_reply(agent_row, channel_id, history, on_tools_ready=None, on_stream_start=None, on_token=None, **_kwargs):
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

    _clear_rate()
    with client.websocket_connect("/ws/general") as ws:
        ws.send_json({"token": token})
        ws.send_json({"body": "just chatting"})
        # Status frames may precede the echo; read until it lands.
        for _ in range(12):
            event = ws.receive_json()
            if event["type"] == "message" and event["message"].get("author_kind") == "human":
                break
        else:
            raise AssertionError("no echo of the human message")
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
    remaining = client.get("/api/skills", headers=auth).json()
    assert not any(s["name"] == "weekly-health" for s in remaining)
    # Bundled slash commands ship with the product.
    names = {s["name"] for s in remaining}
    assert {"standup", "digest", "decide", "research", "page",
            "repro", "draft", "review", "plan", "brief"} <= names
    standup = next(s for s in remaining if s["name"] == "standup")
    assert "blockers" in standup["body"].lower()


def test_routines_and_due(client, auth, monkeypatch):
    async def fake_reply(agent_row, channel_id, history, on_tools_ready=None, on_stream_start=None, on_token=None, **_kwargs):
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

    # A manual test-run posts without rescheduling the routine.
    current = asyncio.run(db.get_routine(row["id"]))
    asyncio.run(main._execute_routine(current, test_run=True))
    assert asyncio.run(db.get_routine(row["id"]))["next_run_at"] == current["next_run_at"]


def test_agent_failure_is_persisted_and_status_resets(client, auth, monkeypatch):
    async def broken_reply(*_args, **_kwargs):
        raise RuntimeError("provider exploded")

    monkeypatch.setattr(main.agent, "generate_reply", broken_reply)

    async def run():
        row = await db.fetch_agent("swarm")
        await db.add_message("general", "uzeb", "please help", "human")
        return await main._run_agent("general", row)

    result = asyncio.run(run())
    assert result["reply"].startswith("[agent error:")
    history = client.get("/api/channels/general/messages", headers=auth).json()
    assert any(m["author_kind"] == "agent" and "agent error" in m["body"] for m in history)
    swarm = client.get("/api/agents/swarm", headers=auth).json()
    assert swarm["status"] == "idle"


def test_approvals_roundtrip(client, auth, monkeypatch):
    async def fake_reply(agent_row, channel_id, history, on_tools_ready=None, on_stream_start=None, on_token=None, **_kwargs):
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


def test_sandbox_api_and_writes_stay_inside(client, auth, tmp_path, monkeypatch):
    sandbox = tmp_path / "box"
    sandbox.mkdir()
    (sandbox / "notes.md").write_text("hello", encoding="utf-8")
    monkeypatch.setattr(agent, "SANDBOX_DIR", str(sandbox))
    data = client.get("/api/computer", headers=auth).json()
    assert data["shared"] is True
    assert "system" in data
    assert any(f["path"] == "notes.md" for f in data["files"])
    preview = client.get("/api/computer/file", params={"path": "notes.md"}, headers=auth).json()
    assert preview["content"] == "hello"
    assert client.get("/api/computer/file", params={"path": "../secret"}, headers=auth).status_code == 404

    result = asyncio.run(agent._run_write_workspace("reports/a.md", "# hi"))
    assert "wrote reports/a.md" in result
    assert (sandbox / "reports" / "a.md").read_text(encoding="utf-8") == "# hi"
    denied = asyncio.run(agent._run_write_workspace("../escape.md", "nope"))
    assert "invalid" in denied
    assert not (tmp_path / "escape.md").exists()


def test_handoff_from_agent_mention(client, auth, monkeypatch):
    async def fake_reply(agent_row, channel_id, history, on_tools_ready=None, on_stream_start=None, on_token=None, **_kwargs):
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


def test_group_chat_triggers_members_without_mention(client, auth, monkeypatch):
    async def fake_reply(agent_row, channel_id, history, on_tools_ready=None, on_stream_start=None, on_token=None, **_kwargs):
        if on_stream_start is not None:
            await on_stream_start()
        if on_token is not None:
            await on_token("here")
        return {"reply": agent_row["name"], "tool_events": [], "usage": {}}

    monkeypatch.setattr("backend.agent.generate_reply", fake_reply)
    monkeypatch.setattr("backend.main.agent.generate_reply", fake_reply)
    _clear_rate()
    assert client.post(
        "/api/channels",
        json={"name": "empty-group", "kind": "group", "members": []},
        headers=auth,
    ).status_code == 400
    _clear_rate()
    created = client.post(
        "/api/channels",
        json={"name": "launch team", "kind": "group", "members": ["swarm", "ledger"]},
        headers=auth,
    )
    assert created.status_code == 200
    group = created.json()
    assert group["kind"] == "group"
    assert group["id"] == "launch-team"
    assert group["members"] == ["swarm", "ledger"]
    channels = {c["id"]: c for c in client.get("/api/channels", headers=auth).json()}
    assert channels["launch-team"]["members"] == ["swarm", "ledger"]

    _clear_rate()
    token = auth["Authorization"].removeprefix("Bearer ")
    with client.websocket_connect("/ws/launch-team") as ws:
        ws.send_json({"token": token})
        ws.send_json({"body": "what's the status"})
        authors = []
        for _ in range(20):
            event = ws.receive_json()
            if event["type"] == "message" and event["message"].get("author_kind") == "agent":
                authors.append(event["message"]["author"])
                if "swarm" in authors and "ledger" in authors:
                    break
        assert authors[:2] == ["swarm", "ledger"]
