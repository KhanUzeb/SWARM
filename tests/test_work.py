"""Work sessions: unified interface over chat replies, v2 runs and routines."""
import asyncio
import time

import backend.main as main
import backend.v2 as v2
import backend.work as work


def _clear_rate():
    main._last_write.clear()


def _mock_agent(monkeypatch, reply="done", tool_events=None):
    async def fake_reply(*_args, **_kwargs):
        return {"reply": reply, "tool_events": tool_events or [], "usage": {}}

    monkeypatch.setattr(main.agent, "generate_reply", fake_reply)


def _wait_for(predicate, timeout=10.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        result = predicate()
        if result:
            return result
        time.sleep(0.1)
    raise AssertionError("timed out waiting for background work")


def test_workflow_run_maps_to_same_session_interface(client, auth, monkeypatch):
    _mock_agent(monkeypatch, reply="run output")
    run = client.post("/api/v2/runs", headers=auth, json={"objective": "Check the brief"}).json()

    sessions = _wait_for(
        lambda: [s for s in client.get("/api/work", headers=auth).json()
                 if s.get("run_id") == run["id"]] or None)
    session = sessions[0]
    assert session["source"] == "run"

    detail = client.get(f"/api/work/{session['id']}", headers=auth)
    assert detail.status_code == 200
    assert detail.json()["run_id"] == run["id"]

    events = _wait_for(
        lambda: client.get(f"/api/work/{session['id']}/events", headers=auth).json() or None)
    assert events[0]["work_id"] == session["id"]
    assert events[0]["seq"] >= 1

    # A chat-triggered reply on the same interface links back full messages…
    _clear_rate()
    client.post(
        "/api/channels/dm-swarm/messages",
        json={"author": "uzeb", "body": "link me"},
        headers=auth,
    )
    chat = _wait_for(
        lambda: [s for s in client.get("/api/work", headers=auth).json()
                 if s.get("channel_id") == "dm-swarm" and s["status"] == "completed"] or None)
    msgs = client.get(f"/api/work/{chat[0]['id']}/messages", headers=auth)
    assert msgs.status_code == 200
    assert any(m.get("author") == "swarm" and m.get("body") == "run output"
               and "reactions" in m for m in msgs.json())
    assert client.get("/api/work/work_nope/messages", headers=auth).status_code == 404
    # …and message linking stays idempotent.
    assert asyncio.run(work.link_message(session["id"], 4242)) is True
    assert asyncio.run(work.link_message(session["id"], 4242)) is False
    assert asyncio.run(work.linked_messages(session["id"], "uzeb")) == [4242]


def test_event_cursor_replay_is_stable(client, auth):
    """Cursor replay over run-backed sessions is monotonic and never
    renumbers or drops the run-derived tail (regression: pre-fix behavior
    renumbered from 1). Fully deterministic — no background polling."""
    async def setup():
        run = await v2.create_run("uzeb", "cursor determinism", None)
        session = await work.create_session(
            "uzeb", "cursor determinism", source="run", run_id=run["id"])
        await work.append_event(session["id"], "work_started", {})
        await v2.append_event(run["id"], "run_started", {})
        await v2.append_event(run["id"], "step_started", {}, step_id="s1")
        await v2.append_event(run["id"], "step_completed", {"ok": True}, step_id="s1")
        return session["id"]

    work_id = asyncio.run(setup())
    full = client.get(f"/api/work/{work_id}/events", headers=auth).json()
    seqs = [e["seq"] for e in full]
    assert seqs == sorted(seqs) and len(set(seqs)) == len(seqs)
    via_run = [e for e in full if e.get("via_run")]
    assert len(via_run) >= 3  # run_queued + run_started + steps, after work evts

    # Replay from a mid cursor returns only later events, monotonically.
    mid = seqs[len(seqs) // 2]
    tail = client.get(f"/api/work/{work_id}/events?after={mid}", headers=auth).json()
    assert all(e["seq"] > mid for e in tail)
    # Reconnect replay is stable: same cursor, same events.
    again = client.get(f"/api/work/{work_id}/events?after={mid}", headers=auth).json()
    assert [(e["seq"], e["type"]) for e in again] == [(e["seq"], e["type"]) for e in tail]
    # Cursor at the tip returns nothing.
    assert client.get(f"/api/work/{work_id}/events?after={seqs[-1]}", headers=auth).json() == []


def _approval_workflow(client, auth):
    created = client.post(
        "/api/v2/workflows",
        headers=auth,
        json={"name": "Gate", "description": "",
              "graph": {"nodes": [{"id": "gate", "type": "approval", "label": "Gate"}], "edges": []}},
    )
    assert created.status_code == 200
    return created.json()["id"]


def test_approval_gate_approve_and_deny(client, auth, monkeypatch):
    """Approval checkpoint: approve resumes to terminal, deny cancels."""
    _mock_agent(monkeypatch, reply="post approval work")
    workflow_id = _approval_workflow(client, auth)

    def launch(objective):
        run = client.post(
            "/api/v2/runs", headers=auth,
            json={"objective": objective, "workflow_id": workflow_id}).json()
        _wait_for(lambda: client.get(f"/api/v2/runs/{run['id']}", headers=auth).json()["status"]
                  == "waiting_for_approval" or None)
        return run

    # Approve path: gate surfaces in the normalized interface, then resumes.
    run = launch("Needs a human")
    session = next(s for s in client.get("/api/work", headers=auth).json()
                   if s.get("run_id") == run["id"])
    assert session["status"] == "waiting_for_approval"
    assert session["requires_action"] is True
    types = [e["type"] for e in client.get(f"/api/work/{session['id']}/events", headers=auth).json()]
    assert "approval_requested" in types

    resolved = client.post(f"/api/v2/runs/{run['id']}/approvals/gate",
                           headers=auth, json={"status": "approved"})
    assert resolved.status_code == 200
    events = _wait_for(
        lambda: client.get(f"/api/work/{session['id']}/events", headers=auth).json() or None)
    assert "approval_resolved" in [e["type"] for e in events]
    terminal = _wait_for(
        lambda: client.get(f"/api/work/{session['id']}", headers=auth).json()["status"]
        in {"completed", "running"} or None)
    assert terminal

    # Deny path: run and session both cancel.
    denied_run = launch("Please deny")
    denied = client.post(f"/api/v2/runs/{denied_run['id']}/approvals/gate",
                         headers=auth, json={"status": "denied"})
    assert denied.status_code == 200
    assert denied.json()["status"] == "cancelled"
    denied_session = next(s for s in client.get("/api/work", headers=auth).json()
                          if s.get("run_id") == denied_run["id"])
    detail = client.get(f"/api/work/{denied_session['id']}", headers=auth).json()
    assert detail["status"] == "cancelled"


def test_work_cancel_and_idempotent_recancel(client, auth, monkeypatch):
    _mock_agent(monkeypatch, reply="slow work")
    run = client.post("/api/v2/runs", headers=auth, json={"objective": "Cancel me"}).json()
    sessions = _wait_for(
        lambda: [s for s in client.get("/api/work", headers=auth).json()
                 if s.get("run_id") == run["id"]] or None)
    work_id = sessions[0]["id"]

    cancelled = client.post(f"/api/work/{work_id}/cancel", headers=auth)
    assert cancelled.status_code == 200
    assert cancelled.json()["status"] == "cancelled"
    types = [e["type"] for e in client.get(f"/api/work/{work_id}/events", headers=auth).json()]
    assert "work_cancelled" in types

    # Cancelling a terminal session is idempotent, not an error.
    again = client.post(f"/api/work/{work_id}/cancel", headers=auth)
    assert again.status_code == 200
    assert again.json()["status"] == "cancelled"


def test_owner_isolation_and_auth(client, auth):
    run = client.post("/api/v2/runs", headers=auth, json={"objective": "Mine"}).json()
    session = _wait_for(
        lambda: next(iter([s for s in client.get("/api/work", headers=auth).json()
                           if s.get("run_id") == run["id"]]), None))
    other = client.post("/api/register", json={"handle": "intruder"}).json()["token"]
    foreign = {"Authorization": f"Bearer {other}"}
    assert client.get(f"/api/work/{session['id']}", headers=foreign).status_code == 404
    assert client.get(f"/api/work/{session['id']}/events", headers=foreign).status_code == 404
    assert client.post(f"/api/work/{session['id']}/cancel", headers=foreign).status_code == 404
    assert client.get("/api/work").status_code == 401
    assert client.get(f"/api/work/{session['id']}/events").status_code == 401


def test_no_secrets_leak_through_events(client, auth):
    session = asyncio.run(work.create_session("uzeb", "secret check", source="chat"))
    event = asyncio.run(work.append_event(session["id"], "tool_started", {
        "tool": "fetch_url", "secret": "shh", "api_key": "key",
        "access_token": "tok", "refresh_token": "ref", "hidden_prompt": "sys",
    }, "fetch_url"))
    assert "secret" not in event["payload"]
    listed = client.get(f"/api/work/{session['id']}/events", headers=auth).json()
    blob = str(listed)
    for leaked in ("shh", "tok", "ref", "sys"):
        assert leaked not in blob


def test_existing_databases_upgrade_without_data_loss(client, auth):
    _clear_rate()
    workflow = client.post(
        "/api/v2/workflows", headers=auth,
        json={"name": "Legacy", "description": "pre-existing",
              "graph": {"nodes": [{"id": "lead", "type": "agent"}], "edges": []}}).json()
    posted = client.post(
        "/api/channels/general/messages",
        json={"author": "uzeb", "body": "legacy data"},
        headers=auth,
    ).json()
    # Re-running the additive migration must be idempotent and lossless.
    asyncio.run(work.init_db())
    asyncio.run(v2.init_db())
    assert client.get(f"/api/v2/workflows/{workflow['id']}", headers=auth).status_code == 200
    history = client.get("/api/channels/general/messages", headers=auth).json()
    assert any(m["id"] == posted["id"] for m in history)
    assert client.get("/api/work", headers=auth).status_code == 200


def test_restart_recovery_settles_interrupted_sessions(client, auth, monkeypatch):
    """Deterministic recovery: the parked run sits in waiting_for_approval
    (a stable, recoverable state), so recovery can neither miss the orphan
    nor race the run to completion."""
    _mock_agent(monkeypatch, reply="recovered run")
    # A chat session stuck mid-flight gets cancelled with a reason.
    orphan = asyncio.run(work.create_session("uzeb", "orphaned chat work", source="chat"))
    asyncio.run(work.append_event(orphan["id"], "work_started", {"objective": "x"}))
    # A run-backed session parked at an approval gate is recoverable.
    workflow_id = _approval_workflow(client, auth)
    run = client.post(
        "/api/v2/runs", headers=auth,
        json={"objective": "Resumable", "workflow_id": workflow_id}).json()
    _wait_for(lambda: client.get(f"/api/v2/runs/{run['id']}", headers=auth).json()["status"]
              == "waiting_for_approval" or None)

    settled = asyncio.run(work.recover_interrupted())
    assert settled >= 1
    detail = client.get(f"/api/work/{orphan['id']}", headers=auth).json()
    assert detail["status"] == "cancelled"
    types = [e["type"] for e in client.get(f"/api/work/{orphan['id']}/events", headers=auth).json()]
    assert "work_cancelled" in types
    # The parked run session is left alone — still waiting, still recoverable.
    session = next(s for s in client.get("/api/work", headers=auth).json()
                   if s.get("run_id") == run["id"])
    assert session["status"] == "waiting_for_approval"
    assert asyncio.run(work.recover_interrupted()) == 0  # idempotent while parked
    # Approving lets the run finish; the list view syncs its session to terminal.
    client.post(f"/api/v2/runs/{run['id']}/approvals/gate",
                headers=auth, json={"status": "approved"})
    _wait_for(
        lambda: client.get(f"/api/v2/runs/{run['id']}", headers=auth).json()["status"]
        == "completed" or None)
    synced = [s for s in client.get("/api/work", headers=auth).json()
              if s.get("run_id") == run["id"]][0]
    assert synced["status"] == "completed"


def test_ws_receives_work_lifecycle_events(client, auth, monkeypatch):
    """A chat-triggered reply fans work events out on the channel socket."""
    _mock_agent(monkeypatch, reply="rail reply")
    _clear_rate()
    with client.websocket_connect("/ws/dm-swarm") as ws:
        ws.send_json({"token": auth["Authorization"].removeprefix("Bearer ")})
        ws.send_json({"body": "hey swarm, status update"})
        seen: dict[str, dict] = {}
        for _ in range(60):
            event = ws.receive_json()
            if event.get("type") == "work" and isinstance(event.get("event"), dict):
                seen[event["event"]["type"]] = event["event"]
                if "work_completed" in seen:
                    break
        assert {"work_started", "agent_started", "message_linked", "work_completed"} <= set(seen)
        assert seen["work_completed"]["work_id"].startswith("work_")
        work_id = seen["work_completed"]["work_id"]
    # The same session is listed with its message link.
    sessions = [s for s in client.get("/api/work", headers=auth).json() if s["id"] == work_id]
    assert sessions and sessions[0]["status"] == "completed"
