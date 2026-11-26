"""Work sessions: unified interface over chat replies, v2 runs and routines."""
import asyncio
import time

import pytest

import backend.db as db
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


def _gate_binding(client, auth, run_id):
    """The reviewed binding the UI echoes back on a v2 gate decision."""
    state = client.get(f"/api/v2/runs/{run_id}/approval", headers=auth).json()
    return {"grant_id": state["grant_id"], "binding_hash": state["binding_hash"]}


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


def test_agent_error_fails_work_session(client, auth, monkeypatch):
    _mock_agent(monkeypatch, reply="[agent error: no API key — connect a provider]")
    owners = ("uzeb", "system")
    before = {s["id"] for owner in owners
              for s in asyncio.run(work.list_sessions(owner))}
    agent_row = asyncio.run(main.db.fetch_agent("swarm"))

    result = asyncio.run(main._run_agent("dm-swarm", agent_row))

    assert result["reply"].startswith("[agent error:")
    sessions = [s for owner in owners for s in asyncio.run(work.list_sessions(owner))
                if s["id"] not in before]
    assert len(sessions) == 1
    session = asyncio.run(work.get_session_any_owner(sessions[0]["id"]))
    events = asyncio.run(work.list_events(session["id"], session["owner"]))
    assert session["status"] == "failed"
    assert "work_failed" in [e["type"] for e in events]
    assert "work_completed" not in [e["type"] for e in events]


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


def _run_backed_cursor_setup(client, auth, objective="cursor stability"):
    async def setup():
        await work.init_db()
        run = await v2.create_run("uzeb", objective, None)
        session = await work.create_session(
            "uzeb", objective, source="run", run_id=run["id"])
        await work.append_event(session["id"], "work_started", {})
        await v2.append_event(run["id"], "run_started", {})
        await v2.append_event(run["id"], "step_started", {}, step_id="s1")
        await v2.append_event(run["id"], "step_completed", {"ok": True}, step_id="s1")
        return session["id"], run["id"]

    work_id, run_id = asyncio.run(setup())
    first = client.get(f"/api/work/{work_id}/events", headers=auth).json()
    return work_id, run_id, first


def _run_fingerprints(events):
    return [
        (e["seq"], e["type"], e["step_id"], e["payload"], e["created_at"])
        for e in events if e.get("via_run")
    ]


def test_cursor_stable_when_work_events_are_appended_later(client, auth):
    work_id, _run_id, first = _run_backed_cursor_setup(client, auth)
    assert [e["seq"] for e in first] == sorted(e["seq"] for e in first)
    cursor = max(e["seq"] for e in first)

    asyncio.run(work.append_event(work_id, "tool_started", {"tool": "later"}))
    second = client.get(f"/api/work/{work_id}/events", headers=auth).json()

    # Previously delivered run events keep their exact seq and content.
    assert _run_fingerprints(second) == _run_fingerprints(first)
    tail = client.get(
        f"/api/work/{work_id}/events?after={cursor}", headers=auth).json()
    assert all(e["seq"] > cursor for e in tail)
    assert not [e for e in tail if e.get("via_run")]


def test_new_run_event_continues_the_global_order(client, auth):
    work_id, run_id, first = _run_backed_cursor_setup(client, auth, "later run event")
    cursor = max(e["seq"] for e in first)
    asyncio.run(work.append_event(work_id, "tool_started", {"tool": "native"}))
    native = client.get(f"/api/work/{work_id}/events", headers=auth).json()
    native_seq = max(e["seq"] for e in native)

    asyncio.run(v2.append_event(
        run_id, "step_completed", {"done": True}, step_id="s2"))
    third = client.get(f"/api/work/{work_id}/events", headers=auth).json()

    assert _run_fingerprints(third)[:len(_run_fingerprints(first))] == _run_fingerprints(first)
    added = [e for e in third if e.get("via_run") and e["seq"] > cursor]
    assert len(added) == 1
    assert added[0]["seq"] > native_seq
    assert added[0]["step_id"] == "s2"


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
                           headers=auth, json={"status": "approved", **_gate_binding(client, auth, run['id'])})
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
                         headers=auth, json={"status": "denied", **_gate_binding(client, auth, denied_run['id'])})
    assert denied.status_code == 200
    assert denied.json()["status"] == "cancelled"
    denied_session = next(s for s in client.get("/api/work", headers=auth).json()
                          if s.get("run_id") == denied_run["id"])
    detail = client.get(f"/api/work/{denied_session['id']}", headers=auth).json()
    assert detail["status"] == "cancelled"



def test_approval_resume_preserves_completed_step_outputs(client, auth, monkeypatch):
    """Resuming after a gate must keep prior step results in the final report."""
    _mock_agent(monkeypatch, reply="step output")
    workflow = client.post(
        "/api/v2/workflows",
        headers=auth,
        json={"name": "Resume report", "description": "", "graph": {
            "nodes": [
                {"id": "before", "type": "agent", "label": "Before"},
                {"id": "gate", "type": "approval", "label": "Review"},
                {"id": "after", "type": "agent", "label": "After"},
            ],
            "edges": [["before", "gate"], ["gate", "after"]],
        }},
    ).json()
    edited = client.patch(
        f"/api/v2/workflows/{workflow['id']}",
        headers=auth,
        json={"name": "Resume report edited"},
    )
    assert edited.status_code == 200
    assert edited.json()["name"] == "Resume report edited"
    run = client.post(
        "/api/v2/runs", headers=auth,
        json={"objective": "Resume this workflow", "workflow_id": workflow["id"]},
    ).json()
    _wait_for(lambda: client.get(
        f"/api/v2/runs/{run['id']}", headers=auth
    ).json()["status"] == "waiting_for_approval" or None)

    resolved = client.post(
        f"/api/v2/runs/{run['id']}/approvals/gate",
        headers=auth,
        json={"status": "approved", **_gate_binding(client, auth, run['id'])},
    )
    assert resolved.status_code == 200
    terminal = _wait_for(lambda: (
        current if (current := client.get(
            f"/api/v2/runs/{run['id']}", headers=auth
        ).json())["status"] == "completed" else None
    ))
    assert [step["id"] for step in terminal["report"]["steps"]] == ["before", "after"]

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


def _launch_parked_run(client, auth, objective):
    workflow_id = _approval_workflow(client, auth)
    run = client.post("/api/v2/runs", headers=auth,
                      json={"objective": objective, "workflow_id": workflow_id}).json()
    _wait_for(lambda: client.get(f"/api/v2/runs/{run['id']}", headers=auth).json()["status"]
              == "waiting_for_approval" or None, "park at gate")
    session = _wait_for(lambda: next(iter([s for s in client.get("/api/work", headers=auth).json()
                                           if s.get("run_id") == run["id"]]), None), "work session")
    client.get(f"/api/work/{session['id']}/events", headers=auth).json()
    return run["id"], session["id"], _gate_binding(client, auth, run["id"])


def test_work_cancel_at_parked_gate_denies_late_approval(client, auth):
    rid, wid, binding = _launch_parked_run(client, auth, "cancel at gate")
    assert client.get(f"/api/work/{wid}", headers=auth).json()["status"] == "waiting_for_approval"

    resp = client.post(f"/api/work/{wid}/cancel", headers=auth)
    assert resp.status_code == 200
    assert resp.json()["status"] == "cancelled"
    assert asyncio.run(v2.get_run(rid, "uzeb"))["status"] == "cancelled"
    grant = asyncio.run(v2.get_approval_grant(rid, "uzeb", "gate"))
    assert grant["status"] == "cancelled"  # owner cancelled; nobody denied the gate
    assert client.post(f"/api/v2/runs/{rid}/approvals/gate", headers=auth,
                       json={**binding, "status": "approved"}).status_code == 409
    assert asyncio.run(v2.get_run(rid, "uzeb"))["status"] == "cancelled"
    assert rid not in [r["id"] for r in asyncio.run(v2.recoverable_runs())]
    events = asyncio.run(work.list_events(wid, "uzeb"))
    terminals = [e for e in events
                 if e["type"] in {"work_completed", "work_failed", "work_cancelled"}]
    assert len(terminals) == 1  # native settlement is adopted by the mirror
    assert terminals[0].get("via_run")


def test_work_cancel_fails_closed_when_the_run_cannot_be_cancelled(client, auth, monkeypatch):
    rid, wid, binding = _launch_parked_run(client, auth, "cancel failure")

    async def failing_cancel(*_args, **_kwargs):
        raise RuntimeError("simulated database failure")

    monkeypatch.setattr(v2, "cancel_run", failing_cancel)
    resp = client.post(f"/api/work/{wid}/cancel", headers=auth)
    assert resp.status_code == 409
    session = client.get(f"/api/work/{wid}", headers=auth).json()
    assert session["status"] == "waiting_for_approval"
    assert "work_cancelled" not in [e["type"] for e in client.get(
        f"/api/work/{wid}/events", headers=auth).json()]
    # The grant was never revoked: the honest outcome is that the cancel did
    # not happen, so a real human decision can still resume the run.
    assert client.post(f"/api/v2/runs/{rid}/approvals/gate", headers=auth,
                       json={**binding, "status": "approved"}).status_code == 200


def test_work_event_mirror_retries_a_crash_reservation(client, auth):
    async def setup():
        run = await v2.create_run("uzeb", "crash reservation", None)
        session = await work.create_session(
            "uzeb", "crash reservation", source="run", run_id=run["id"])
        async with db.open_db() as conn:
            await conn.execute(
                "INSERT INTO work_run_event_mirror(work_id, run_seq, work_seq, via_run)"
                " VALUES(?,?,0,?)", (session["id"], 1, run["id"]))
            await conn.commit()
        return session["id"]

    wid = asyncio.run(setup())
    events = asyncio.run(work.list_events(wid, "uzeb"))
    assert [e["type"] for e in events if e.get("via_run")] == ["work_queued"]
    rows = asyncio.run(_mirror_rows(wid))
    assert rows == [(1, 2)]


def _mirror_rows(work_id):
    async def read():
        async with db.open_db() as conn:
            cur = await conn.execute(
                "SELECT run_seq, work_seq FROM work_run_event_mirror WHERE work_id=?",
                (work_id,))
            return [tuple(r) for r in await cur.fetchall()]
    return read()


def test_work_event_mirror_pages_past_one_batch(client, auth, monkeypatch):
    async def setup():
        run = await v2.create_run("uzeb", "mirror paging", None)
        session = await work.create_session(
            "uzeb", "mirror paging", source="run", run_id=run["id"])
        async with db.open_db() as conn:
            await conn.executemany(
                "INSERT INTO run_events(run_id, seq, event_type, step_id, payload, created_at)"
                " VALUES(?,?,?,?,?,?)",
                [(run["id"], seq, "step_completed", f"s{seq}", "{}", 1.0)
                 for seq in range(2, 8)])
            await conn.commit()
        return session["id"]

    monkeypatch.setattr(work, "MIRROR_PAGE_SIZE", 2)
    wid = asyncio.run(setup())
    events = asyncio.run(work.list_events(wid, "uzeb"))
    assert len([e for e in events if e.get("via_run")]) == 7


def test_recovery_does_not_duplicate_a_mirrored_terminal_event(client, auth):
    async def setup():
        run = await v2.create_run("uzeb", "cancel then crash", None)
        session = await work.create_session(
            "uzeb", "cancel then crash", source="run", run_id=run["id"])
        await v2.cancel_run(run["id"], "uzeb")
        async with db.open_db() as conn:
            await conn.execute(
                "UPDATE work_sessions SET status='running' WHERE id=?", (session["id"],))
            await conn.commit()
        return session["id"]

    wid = asyncio.run(setup())
    assert asyncio.run(work.recover_interrupted()) == 1
    events = asyncio.run(work.list_events(wid, "uzeb"))
    terminals = [e for e in events if e["type"] in {"work_completed", "work_failed", "work_cancelled"}]
    assert len(terminals) == 1
    assert terminals[0].get("via_run")
    assert asyncio.run(work.get_session(wid, "uzeb"))["status"] == "cancelled"


def test_denial_is_recorded_once_after_the_mirror_runs(client, auth):
    rid, wid, binding = _launch_parked_run(client, auth, "deny once")
    denied = client.post(f"/api/v2/runs/{rid}/approvals/gate", headers=auth,
                         json={**binding, "status": "denied"})
    assert denied.status_code == 200
    events = asyncio.run(work.list_events(wid, "uzeb"))
    terminals = [e for e in events if e["type"] in {"work_completed", "work_failed", "work_cancelled"}]
    assert len(terminals) == 1
    assert asyncio.run(work.get_session(wid, "uzeb"))["status"] == "cancelled"


def test_legacy_cursor_replays_once_then_stays_stable(client, auth):
    """An upgraded database has no mirror rows yet; old cursors catch up once."""
    async def setup():
        run = await v2.create_run("uzeb", "legacy cursor", None)
        session = await work.create_session(
            "uzeb", "legacy cursor", source="run", run_id=run["id"])
        now = time.time()
        # Pre-mirror shape: native projection only, no run_event_mirror rows.
        async with db.open_db() as conn:
            await conn.execute(
                "DELETE FROM work_events WHERE work_id=? AND seq=1", (session["id"],))
            await conn.executemany(
                "INSERT INTO work_events(work_id, seq, type, step_id, payload, created_at)"
                " VALUES(?,?,?,?,?,?)",
                [(session["id"], 2, "approval_requested", "gate", '{"label":"gate"}', now),
                 (session["id"], 3, "work_completed", None, '{"summary":"old result"}', now)])
            await conn.execute(
                "UPDATE work_sessions SET status='completed', finished_at=? WHERE id=?",
                (now, session["id"]))
            await conn.commit()
        for etype in ("run_queued", "step_completed", "run_completed"):
            await v2.append_event(run["id"], etype, {})
        return session["id"]

    wid = asyncio.run(setup())
    first = asyncio.run(work.list_events(wid, "uzeb", 3))
    again = asyncio.run(work.list_events(wid, "uzeb", 3))
    assert first and [(e["seq"], e["type"]) for e in first] == [
        (e["seq"], e["type"]) for e in again]
    assert asyncio.run(work.get_session(wid, "uzeb"))["status"] == "completed"
    assert asyncio.run(work.get_session(wid, "uzeb"))["requires_action"] is False


@pytest.mark.parametrize("kind", ["completed", "failed"])
def test_terminal_work_event_is_recorded_once(client, auth, kind):
    async def scenario():
        if kind == "completed":
            run = await v2.create_run("uzeb", "terminal dedupe", None)
        else:
            wf = await v2.create_workflow("uzeb", "Bad", "", {
                "nodes": [{"id": "bad", "type": "unsupported"}], "edges": []})
            run = await v2.create_run("uzeb", "terminal dedupe", wf["id"])
        session = await work.create_session(
            "uzeb", "terminal dedupe", source="run", run_id=run["id"])
        await work.append_event(session["id"], "work_started", {})
        await v2.execute_run(run["id"], "uzeb")
        snapshot = await work.get_session(session["id"], "uzeb")
        # All polls read the same stale snapshot before any one commits.
        await asyncio.gather(*(
            main._sync_work_with_run(dict(snapshot), "uzeb") for _ in range(4)))
        events = await work.list_events(session["id"], "uzeb")
        target = "work_completed" if kind == "completed" else "work_failed"
        return [e for e in events if e["type"] == target], await work.get_session(
            session["id"], "uzeb")

    events, session = asyncio.run(scenario())
    assert len(events) == 1
    assert events[0].get("via_run")
    assert session["status"] == kind


def test_unknown_gate_does_not_keep_a_stale_active_step(client, auth):
    async def scenario():
        run = await v2.create_run("uzeb", "no grant", None)
        session = await work.create_session(
            "uzeb", "no grant", source="run", run_id=run["id"])
        await work.append_event(session["id"], "work_started", {})
        async with db.open_db() as conn:
            await conn.execute(
                "UPDATE work_sessions SET active_step='old_gate' WHERE id=?",
                (session["id"],))
            await conn.execute(
                "UPDATE runs SET status='waiting_for_approval' WHERE id=?", (run["id"],))
            await conn.commit()
        await main._sync_work_with_run(
            await work.get_session(session["id"], "uzeb"), "uzeb")
        return await v2.approval_state(run["id"], "uzeb"), await work.get_session(
            session["id"], "uzeb")

    state, session = asyncio.run(scenario())
    assert state["state"] == "none"
    assert session["status"] == "waiting_for_approval"
    assert session["active_step"] is None


def test_second_approval_gate_replaces_the_active_step(client, auth):
    async def scenario():
        wf = await v2.create_workflow("uzeb", "Two gates", "", {
            "nodes": [{"id": "g1", "type": "approval", "label": "First"},
                      {"id": "g2", "type": "approval", "label": "Second"}],
            "edges": [["g1", "g2"]]})
        run = await v2.create_run("uzeb", "two gates", wf["id"])
        session = await work.create_session(
            "uzeb", "two gates", source="run", run_id=run["id"])
        await work.append_event(session["id"], "work_started", {})
        await v2.execute_run(run["id"], "uzeb")
        await main._sync_work_with_run(
            await work.get_session(session["id"], "uzeb"), "uzeb")
        first = await work.get_session(session["id"], "uzeb")
        grant = await v2.get_approval_grant(run["id"], "uzeb", "g1")
        await v2.resolve_approval_grant(
            run["id"], "uzeb", "g1", "approved", resolved_by="uzeb",
            grant_id=grant["id"], binding_hash=grant["binding_hash"])
        await work.append_event(session["id"], "approval_resolved", {"step_id": "g1"}, "g1")
        await v2.execute_run(run["id"], "uzeb")
        await main._sync_work_with_run(
            await work.get_session(session["id"], "uzeb"), "uzeb")
        second = await work.get_session(session["id"], "uzeb")
        actual = (await v2.approval_state(run["id"], "uzeb"))["step_id"]
        return first, second, actual

    first, second, actual = asyncio.run(scenario())
    assert first["active_step"] == "g1"
    assert actual == "g2"
    assert second["active_step"] == "g2"
    assert second["status"] == "waiting_for_approval"


def test_concurrent_work_polls_record_one_approval_request(client, auth):
    async def setup_and_poll():
        wf = await v2.create_workflow("uzeb", "Gate", "", {
            "nodes": [{"id": "review_gate", "type": "approval", "label": "Review"}],
            "edges": []})
        run = await v2.create_run("uzeb", "approval dedupe", wf["id"])
        session = await work.create_session(
            "uzeb", "approval dedupe", source="run", run_id=run["id"])
        await work.append_event(session["id"], "work_started", {})
        await v2.execute_run(run["id"], "uzeb")
        snapshot = await work.get_session(session["id"], "uzeb")
        await asyncio.gather(*(
            main._sync_work_with_run(dict(snapshot), "uzeb") for _ in range(3)))
        events = await work.list_events(session["id"], "uzeb")
        requested = [e for e in events if e["type"] == "approval_requested"]
        settled = await work.get_session(session["id"], "uzeb")
        return requested, settled

    requested, settled = asyncio.run(setup_and_poll())
    assert [(e["step_id"], bool(e.get("via_run"))) for e in requested] == [
        ("review_gate", True)]
    assert settled["status"] == "waiting_for_approval"
    assert settled["active_step"] == "review_gate"


def test_approval_decision_is_recorded_once(client, auth, monkeypatch):
    _mock_agent(monkeypatch, reply="after gate")
    rid, wid, binding = _launch_parked_run(client, auth, "decision once")
    decision = client.post(f"/api/v2/runs/{rid}/approvals/gate", headers=auth,
                           json={**binding, "status": "approved"})
    assert decision.status_code == 200
    _wait_for(lambda: client.get(f"/api/v2/runs/{rid}", headers=auth).json()["status"]
              == "completed" or None, "run completion")
    events = asyncio.run(work.list_events(wid, "uzeb"))
    resolved = [e for e in events if e["type"] == "approval_resolved"]
    assert len(resolved) == 1
    assert resolved[0]["payload"]["grant_id"] == binding["grant_id"]
    assert asyncio.run(work.get_session(wid, "uzeb"))["status"] == "completed"


def test_work_rail_names_the_real_approval_gate(client, auth):
    created = client.post("/api/v2/workflows", headers=auth, json={
        "name": "Named gate", "description": "", "graph": {
            "nodes": [{"id": "review_gate", "type": "approval", "label": "Review"}],
            "edges": []}}).json()
    run = client.post("/api/v2/runs", headers=auth, json={
        "objective": "needs review", "workflow_id": created["id"]}).json()
    _wait_for(lambda: client.get(f"/api/v2/runs/{run['id']}", headers=auth).json()["status"]
              == "waiting_for_approval" or None, "park at gate")
    session = next(s for s in client.get("/api/work", headers=auth).json()
                   if s.get("run_id") == run["id"])
    state = client.get(f"/api/v2/runs/{run['id']}/approval", headers=auth).json()
    assert state["step_id"] == "review_gate"
    assert session["active_step"] == "review_gate"


def test_new_run_stays_queued_until_an_executor_starts(client, auth, monkeypatch):
    """The work rail must not show a run as running before it is claimed."""
    monkeypatch.setattr(main.v2, "start_run", lambda *_args, **_kwargs: None)
    run = client.post("/api/v2/runs", headers=auth,
                      json={"objective": "queued until started"}).json()
    session = next(s for s in client.get("/api/work", headers=auth).json()
                   if s.get("run_id") == run["id"])
    types = [e["type"] for e in client.get(
        f"/api/work/{session['id']}/events", headers=auth).json()]
    assert run["status"] == "queued"
    assert session["status"] == "queued"
    assert "work_started" not in types
    assert "work_queued" in types


def test_restart_settlement_records_the_real_terminal_outcome(client, auth):
    async def setup():
        run = await v2.create_run("uzeb", "completed before restart", None)
        session = await work.create_session(
            "uzeb", "completed before restart", source="run", run_id=run["id"])
        await v2.update_run(run["id"], "uzeb", "completed",
                            report={"summary": "real result"})
        async with db.open_db() as conn:
            await conn.execute(
                "UPDATE work_sessions SET status='running' WHERE id=?", (session["id"],))
            await conn.commit()
        return session["id"]

    wid = asyncio.run(setup())
    assert asyncio.run(work.recover_interrupted()) == 1
    session = asyncio.run(work.get_session(wid, "uzeb"))
    events = asyncio.run(work.list_events(wid, "uzeb"))
    assert session["status"] == "completed"
    assert events[-1]["type"] == "work_completed"


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
                headers=auth, json={"status": "approved", **_gate_binding(client, auth, run['id'])})
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
