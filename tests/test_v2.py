import asyncio
import time

import pytest

from backend import v2
from backend.v2 import order_nodes


def test_v2_workflow_and_run_contract(client, auth):
    created = client.post(
        "/api/v2/workflows",
        headers=auth,
        json={
            "name": "Release review",
            "description": "Review a release brief",
            "graph": {"nodes": [{"id": "lead", "type": "agent"}], "edges": []},
        },
    )
    assert created.status_code == 200
    workflow = created.json()
    assert workflow["id"].startswith("wf_")
    assert workflow["graph"]["nodes"][0]["id"] == "lead"

    listed = client.get("/api/v2/workflows", headers=auth)
    assert listed.status_code == 200
    assert listed.json()[0]["id"] == workflow["id"]

    run = client.post(
        "/api/v2/runs",
        headers=auth,
        json={"objective": "Check the release notes", "workflow_id": workflow["id"],
              "model": "openai/gpt-oss-20b"},
    )
    assert run.status_code == 200
    run_body = run.json()
    assert run_body["status"] == "queued"
    assert run_body["model"] == "openai/gpt-oss-20b"

    events = client.get(f"/api/v2/runs/{run_body['id']}/events", headers=auth)
    assert events.status_code == 200
    assert events.json()[0]["event_type"] == "run_queued"


def test_v2_resources_are_private(client, auth):
    run = client.post("/api/v2/runs", headers=auth, json={"objective": "private brief"}).json()
    other = client.post("/api/register", json={"handle": "other"}).json()["token"]
    assert client.get(f"/api/v2/runs/{run['id']}", headers={"Authorization": f"Bearer {other}"}).status_code == 404
    assert client.get("/api/v2/workflows").status_code == 401


def test_provider_catalog_routing_and_saved_model(client, auth):
    response = client.get("/api/ai-support/providers", headers=auth)
    assert response.status_code == 200
    providers = {row["id"]: row for row in response.json()}
    for provider_id in ("openai", "anthropic", "groq", "google", "mistral", "deepseek", "xai", "fireworks", "perplexity"):
        assert provider_id in providers
        assert "api_key" in (providers[provider_id].get("auth_methods") or ["api_key"])
    assert "oauth" in providers["google"]["auth_methods"]

    connect = client.post(
        "/api/ai-support/connect/groq",
        headers=auth,
        json={"api_key": "gsk_test_key_12345678", "model": "openai/gpt-oss-20b"},
    )
    assert connect.status_code == 200
    saved = {row["id"]: row for row in client.get("/api/v2/providers", headers=auth).json()}
    assert saved["groq"]["model"] == "openai/gpt-oss-20b"
    assert saved["groq"]["connected"] is True
    assert any(p["id"] == "deepseek" for p in client.get("/api/v2/providers", headers=auth).json())

    check = client.get("/api/v2/model-routing/validate", headers=auth, params={"provider_id": "groq", "model": "openai/gpt-oss-20b", "requires_tools": "true"})
    assert check.status_code == 200
    assert check.json()["supported"] is True

    connected = client.get("/api/v2/models/connected", headers=auth)
    assert connected.status_code == 200
    assert {"models", "default_model", "live"} <= set(connected.json())


def test_v2_rejects_invalid_workflow_graph(client, auth):
    response = client.post(
        "/api/v2/workflows",
        headers=auth,
        json={"name": "Bad graph", "graph": {"nodes": [{"id": "a"}, {"id": "a"}], "edges": [["a", "missing"]]}},
    )
    assert response.status_code == 422
    assert "node ids must be unique" in str(response.json()["detail"])


def test_v2_oauth_start_requires_config_and_emits_pkce_url(client, auth, monkeypatch):
    missing = client.get("/api/v2/providers/google/oauth/start", headers=auth)
    assert missing.status_code == 400
    monkeypatch.setenv("SWARM_GOOGLE_OAUTH_CLIENT_ID", "client-id")
    started = client.get("/api/v2/providers/google/oauth/start", headers=auth)
    assert started.status_code == 200
    url = started.json()["authorization_url"]
    assert "code_challenge=" in url
    assert "state=" in url


def test_v2_run_report_creates_downloadable_artifact(client, auth, monkeypatch):
    monkeypatch.setenv("SWARM_DEMO", "1")
    run = client.post("/api/v2/runs", headers=auth, json={"objective": "create a report"}).json()
    for _ in range(20):
        current = client.get(f"/api/v2/runs/{run['id']}", headers=auth).json()
        if current["status"] == "completed":
            break
        time.sleep(0.05)
    assert current["status"] == "completed"
    artifacts = client.get(f"/api/v2/runs/{run['id']}/artifacts", headers=auth)
    assert artifacts.status_code == 200
    assert artifacts.json()[0]["name"] == "run-report.json"
    download = client.get(f"/api/v2/artifacts/{artifacts.json()[0]['id']}", headers=auth)
    assert download.status_code == 200
    assert download.headers["content-type"].startswith("application/json")


def test_v2_parallel_and_conditional_nodes_execute(client, auth, monkeypatch):
    monkeypatch.setenv("SWARM_DEMO", "1")
    workflow = client.post("/api/v2/workflows", headers=auth, json={
        "name": "Parallel review",
        "graph": {"nodes": [
            {"id": "parallel", "type": "parallel", "children": [
                {"id": "a", "type": "agent", "agent": "swarm", "label": "Research"},
                {"id": "b", "type": "agent", "agent": "ledger", "label": "Decisions"},
            ]},
            {"id": "skip", "type": "conditional", "when": "never", "label": "Skipped"},
        ], "edges": []},
    }).json()
    run = client.post("/api/v2/runs", headers=auth, json={"objective": "review", "workflow_id": workflow["id"]}).json()
    for _ in range(30):
        current = client.get(f"/api/v2/runs/{run['id']}", headers=auth).json()
        if current["status"] == "completed":
            break
        time.sleep(0.05)
    assert current["status"] == "completed"
    events = client.get(f"/api/v2/runs/{run['id']}/events", headers=auth).json()
    assert sum(e["event_type"] == "step_completed" for e in events) == 2
    assert any(e["event_type"] == "step_skipped" for e in events)


def test_v2_run_websocket_replays_events(client, token, monkeypatch):
    monkeypatch.setenv("SWARM_DEMO", "1")
    run = client.post("/api/v2/runs", headers={"Authorization": f"Bearer {token}"}, json={"objective": "stream events"}).json()
    with client.websocket_connect(f"/api/v2/ws/runs/{run['id']}") as socket:
        socket.send_json({"token": token, "after": 0})
        first = socket.receive_json()
        assert first["run_id"] == run["id"]
        assert first["event_type"] == "run_queued"


def test_v2_graph_order_follows_edges():
    nodes = [{"id": "finish"}, {"id": "start"}, {"id": "middle"}]
    ordered = order_nodes(nodes, [["start", "middle"], ["middle", "finish"]])
    assert [node["id"] for node in ordered] == ["start", "middle", "finish"]


def test_v2_resume_skips_completed_parallel_children(client, auth, monkeypatch):
    async def _run():
        workflow = await v2.create_workflow("uzeb", "Parallel resume", "", {
            "nodes": [{"id": "parallel", "type": "parallel", "children": [
                {"id": "already_done", "type": "agent", "agent": "swarm"},
                {"id": "still_pending", "type": "agent", "agent": "ledger"},
            ]}],
            "edges": [],
        })
        run = await v2.create_run("uzeb", "resume safely", workflow["id"])
        await v2.append_event(run["id"], "step_completed", {
            "label": "Completed already", "output": "saved result", "parallel": True,
        }, "already_done")
        requested_agents = []

        async def fetch_agent(name):
            requested_agents.append(name)
            return None

        monkeypatch.setattr(v2.db, "fetch_agent", fetch_agent)
        await v2.execute_run(run["id"], "uzeb")
        finished = await v2.get_run(run["id"], "uzeb")
        return run["id"], requested_agents, finished["report"]

    run_id, requested_agents, report = asyncio.run(_run())
    assert requested_agents == ["ledger"]
    steps = report["steps"]
    assert [step["id"] for step in steps] == ["already_done", "still_pending"]
    assert steps[0]["output"] == "saved result"
    events = asyncio.run(v2.list_events(run_id, "uzeb"))
    assert [e["step_id"] for e in events if e["event_type"] == "step_completed"] == [
        "already_done", "still_pending",
    ]


# ------------------------------------------------- durable claim / lease / recovery


async def _expire_lease(run_id: str) -> None:
    """Force a lease expiry without sleeping (test-only shortcut)."""
    async with v2._open_db() as conn:
        await conn.execute(
            "UPDATE runs SET lease_expires_at=? WHERE id=?", (time.time() - 1, run_id))
        await conn.commit()


def test_lease_columns_are_additive_for_existing_runs():
    """An older runs table is upgraded in place without losing rows."""
    async def _run():
        await v2.init_db()
        async with v2._open_db() as conn:
            await conn.execute("DROP TABLE runs")
            await conn.execute(
                "CREATE TABLE runs (id TEXT PRIMARY KEY, workflow_id TEXT,"
                " owner TEXT NOT NULL, objective TEXT NOT NULL,"
                " status TEXT NOT NULL DEFAULT 'draft',"
                " policy TEXT NOT NULL DEFAULT 'supervised', report TEXT,"
                " created_at REAL NOT NULL, started_at REAL, finished_at REAL,"
                " model TEXT)")
            await conn.execute(
                "INSERT INTO runs(id, owner, objective, status, created_at)"
                " VALUES('legacy', 'uzeb', 'old work', 'queued', 1)")
            await conn.commit()

        await v2.init_db()
        row = await v2.get_run("legacy", "uzeb")
        assert row["lease_generation"] == 0
        assert row["lease_owner"] is None
        assert row["lease_expires_at"] is None
        claim = await v2.claim_run("legacy", "uzeb", worker_id="new", lease_seconds=30)
        assert claim is not None and claim.generation == 1

    asyncio.run(_run())


def test_claim_run_is_mutually_exclusive():
    """Only one executor can own a run; a live lease cannot be double-claimed."""
    async def _run():
        await v2.init_db()
        run = await v2.create_run("uzeb", "exclusive", None)
        rid = run["id"]

        first = await v2.claim_run(rid, "uzeb", worker_id="a", lease_seconds=30)
        assert first is not None and first.generation == 1
        assert await v2.claim_run(rid, "uzeb", worker_id="b", lease_seconds=30) is None
        assert await v2.claim_run(rid, "uzeb", worker_id="c", lease_seconds=30) is None

        row = await v2.get_run(rid, "uzeb")
        assert row["status"] == "running"
        assert row["lease_owner"] == "a"
        assert row["lease_generation"] == 1

    asyncio.run(_run())


def test_running_lease_takeover_after_expiry_bumps_generation():
    """An expired lease is reclaimable exactly once, with a new fencing token."""
    async def _run():
        await v2.init_db()
        run = await v2.create_run("uzeb", "takeover", None)
        rid = run["id"]
        first = await v2.claim_run(rid, "uzeb", worker_id="a", lease_seconds=30)
        assert first is not None
        assert await v2.claim_run(rid, "uzeb", worker_id="b", lease_seconds=30) is None

        await _expire_lease(rid)
        second = await v2.claim_run(rid, "uzeb", worker_id="b", lease_seconds=30)
        assert second is not None
        assert second.generation == first.generation + 1
        assert await v2.claim_run(rid, "uzeb", worker_id="c", lease_seconds=30) is None
        assert (await v2.get_run(rid, "uzeb"))["lease_owner"] == "b"

    asyncio.run(_run())


def test_fenced_writes_from_stale_executor_are_rejected():
    """A taken-over executor cannot persist status or events."""
    async def _run():
        await v2.init_db()
        run = await v2.create_run("uzeb", "fencing", None)
        rid = run["id"]
        stale = await v2.claim_run(rid, "uzeb", worker_id="stale", lease_seconds=30)
        assert stale is not None

        await _expire_lease(rid)
        current = await v2.claim_run(rid, "uzeb", worker_id="current", lease_seconds=30)
        assert current is not None and current.generation == stale.generation + 1

        assert await v2.update_run(rid, "uzeb", "failed", claim=stale) is None
        try:
            await v2.append_event(rid, "step_completed", {"output": "late"}, "s", claim=stale)
        except v2.StaleRunClaim:
            pass
        else:
            raise AssertionError("stale executor appended an event")

        assert (await v2.get_run(rid, "uzeb"))["status"] == "running"
        assert not [e for e in await v2.list_events(rid, "uzeb")
                    if e["event_type"] == "step_completed"]

        assert await v2.update_run(rid, "uzeb", "completed", claim=current) is not None
        fresh = await v2.append_event(rid, "run_completed", {}, claim=current)
        assert fresh["event_type"] == "run_completed"

    asyncio.run(_run())


def test_recovery_of_parked_approval_fails_closed():
    """A parked gate stays listed for recovery but cannot be claimed or resumed.

    Re-launching it emits no duplicate events and leaves it parked. Until the
    exact-bound approval-consume slice lands, this is an intentional temporary
    inability to resume a gated run. It is fail-closed on purpose.
    """
    async def _run():
        await v2.init_db()
        run = await v2.create_run("uzeb", "gated", None)
        rid = run["id"]
        claim = await v2.claim_run(rid, "uzeb", worker_id="a", lease_seconds=30)
        assert claim is not None
        await v2.update_run(rid, "uzeb", "waiting_for_approval", claim=claim)
        await v2.append_event(
            rid, "approval_requested", {"label": "gate"}, "gate", claim=claim)
        await v2.release_run_lease(claim)

        # The waiter stays visible to recovery so a restart cannot lose it.
        assert rid in {r["id"] for r in await v2.recoverable_runs()}

        # Even an approval_resolved event plus a fresh execution attempt must
        # not resume the gate: no exact-bound grant exists to consume yet.
        await v2.append_event(
            rid, "approval_resolved", {"decision": "approved"}, "gate")
        await v2.execute_run(rid, "uzeb")

        row = await v2.get_run(rid, "uzeb")
        assert row["status"] == "waiting_for_approval"
        events = await v2.list_events(rid, "uzeb")
        assert [e["event_type"] for e in events].count("approval_requested") == 1

    asyncio.run(_run())


def test_recovery_reclaims_running_run_with_expired_lease():
    """A dead executor's running work is recoverable once its lease expires."""
    async def _run():
        await v2.init_db()
        run = await v2.create_run("uzeb", "orphan", None)
        rid = run["id"]
        await v2.claim_run(rid, "uzeb", worker_id="dead", lease_seconds=30)
        assert rid not in {r["id"] for r in await v2.recoverable_runs()}

        await _expire_lease(rid)
        assert rid in {r["id"] for r in await v2.recoverable_runs()}
        takeover = await v2.claim_run(rid, "uzeb", worker_id="live", lease_seconds=30)
        assert takeover is not None and takeover.generation == 2

    asyncio.run(_run())


def test_lease_heartbeat_prevents_takeover(monkeypatch):
    """A live executor keeps renewing its lease during a long step."""
    monkeypatch.setattr(v2, "RUN_LEASE_SECONDS", 0.3)

    async def _run():
        await v2.init_db()
        run = await v2.create_run("uzeb", "heartbeat", None)
        rid = run["id"]

        async def fake_fetch_agent(name):
            return {"name": name}

        async def slow_reply(*args, **kwargs):
            await asyncio.sleep(0.7)
            return {"reply": "done"}

        import backend.agent as agent_mod
        monkeypatch.setattr(v2.db, "fetch_agent", fake_fetch_agent)
        monkeypatch.setattr(agent_mod, "generate_reply", slow_reply)

        task = asyncio.create_task(v2.execute_run(rid, "uzeb"))
        await asyncio.sleep(0.5)
        assert await v2.claim_run(rid, "uzeb", worker_id="intruder", lease_seconds=0.3) is None
        await task
        assert (await v2.get_run(rid, "uzeb"))["status"] == "completed"

    asyncio.run(_run())


# --------------------------------------------- exact-bound approval grants ---

_GATE_GRAPH = {
    "nodes": [
        {"id": "gate", "type": "approval", "label": "Review",
         "agent": "swarm", "scope": "run"},
    ],
    "edges": [],
}


async def _parked_gate():
    await v2.init_db()
    workflow = await v2.create_workflow("uzeb", "Gate", "", _GATE_GRAPH)
    run = await v2.create_run("uzeb", "gated work", workflow["id"])
    rid = run["id"]
    claim = await v2.claim_run(rid, "uzeb", worker_id="executor", lease_seconds=30)
    assert claim is not None
    report = run.get("report") or {
        "objective": run["objective"], "summary": "", "steps": [],
        "artifacts": [], "decisions": [],
    }
    parked = await v2.park_at_approval(
        rid, "uzeb", "gate", claim=claim, report=report, workflow=workflow)
    await v2.release_run_lease(claim)
    return rid, workflow, parked


def test_exact_bound_approval_resume_is_single_use():
    """A granted, exact-bound approval unparks the gate exactly once."""
    async def _run():
        rid, _workflow, parked = await _parked_gate()
        assert (await v2.get_run(rid, "uzeb"))["status"] == "waiting_for_approval"
        # No grant yet: an event or a restart cannot stand in for a decision.
        assert await v2.claim_run(rid, "uzeb", worker_id="intruder") is None

        outcome = await v2.resolve_approval_grant(
            rid, "uzeb", "gate", "approved", resolved_by="uzeb")
        assert outcome["state"] == "resolved"
        claim = await v2.claim_run(rid, "uzeb", worker_id="executor-2", lease_seconds=30)
        assert claim is not None
        grant = await v2.get_approval_grant(rid, "uzeb", "gate")
        assert grant["status"] == "consumed"
        assert grant["binding_hash"] == parked["binding_hash"]
        assert grant["canonical_args"]["step_id"] == "gate"
        assert grant["canonical_args"]["agent"] == "swarm"

        # The same grant cannot authorise a second resume.
        again = await v2.resolve_approval_grant(
            rid, "uzeb", "gate", "approved", resolved_by="uzeb")
        assert again["state"] == "already"
        events = await v2.list_events(rid, "uzeb")
        assert sum(e["event_type"] == "approval_requested" for e in events) == 1

    asyncio.run(_run())


def test_denied_and_expired_grants_never_resume():
    async def _run():
        rid, _workflow, parked = await _parked_gate()
        denied = await v2.resolve_approval_grant(
            rid, "uzeb", "gate", "denied", resolved_by="uzeb")
        assert denied["state"] == "resolved"
        assert await v2.claim_run(rid, "uzeb", worker_id="intruder") is None
        assert (await v2.get_run(rid, "uzeb"))["status"] == "waiting_for_approval"

        rid2, _workflow2, parked2 = await _parked_gate()
        async with v2.db.open_db() as conn:
            await conn.execute(
                "UPDATE approval_grants SET expires_at=? WHERE id=?",
                (time.time() - 1, parked2["grant_id"]))
            await conn.commit()
        expired = await v2.resolve_approval_grant(
            rid2, "uzeb", "gate", "approved", resolved_by="uzeb")
        assert expired["state"] == "expired"
        assert await v2.claim_run(rid2, "uzeb", worker_id="intruder") is None

    asyncio.run(_run())


def test_stale_binding_is_rejected_not_silently_applied():
    async def _run():
        rid, _workflow, parked = await _parked_gate()
        # A client showing old arguments cannot approve the current gate.
        with pytest.raises(v2.ApprovalBindingError):
            await v2.resolve_approval_grant(
                rid, "uzeb", "gate", "approved", resolved_by="uzeb",
                binding_hash="not-the-shown-binding")
        grant = await v2.get_approval_grant(rid, "uzeb", "gate")
        assert grant["status"] == "pending"

        # A grant whose binding no longer matches the stored gate cannot claim.
        await v2.resolve_approval_grant(
            rid, "uzeb", "gate", "approved", resolved_by="uzeb")
        async with v2.db.open_db() as conn:
            await conn.execute(
                "UPDATE approval_grants SET binding_hash=? WHERE id=?",
                ("tampered", parked["grant_id"]))
            await conn.commit()
        with pytest.raises(v2.ApprovalBindingError):
            await v2.claim_run(rid, "uzeb", worker_id="intruder")

    asyncio.run(_run())


def test_crash_ambiguity_is_reported_instead_of_replayed():
    async def _run():
        rid, _workflow, parked = await _parked_gate()
        await v2.resolve_approval_grant(
            rid, "uzeb", "gate", "approved", resolved_by="uzeb")
        # Simulate the ambiguous window: grant consumed, run still parked.
        async with v2.db.open_db() as conn:
            await conn.execute(
                "UPDATE approval_grants SET status='consumed', consumed_at=? WHERE id=?",
                (time.time(), parked["grant_id"]))
            await conn.commit()
        with pytest.raises(v2.ApprovalAmbiguous):
            await v2.claim_run(rid, "uzeb", worker_id="intruder")

    asyncio.run(_run())


def test_v2_approval_route_binds_and_rejects_replay(client, auth):
    rid, _workflow, parked = asyncio.run(_parked_gate())
    binding = {"grant_id": parked["grant_id"], "binding_hash": parked["binding_hash"]}
    # A client that omits the reviewed binding is never silently authorized.
    bare = client.post(
        f"/api/v2/runs/{rid}/approvals/gate",
        headers=auth, json={"status": "approved"})
    assert bare.status_code == 422
    assert asyncio.run(v2.get_approval_grant(rid, "uzeb", "gate"))["status"] == "pending"
    wrong = client.post(
        f"/api/v2/runs/{rid}/approvals/gate",
        headers=auth, json={**binding, "binding_hash": "stale", "status": "approved"})
    assert wrong.status_code == 409
    assert asyncio.run(v2.get_approval_grant(rid, "uzeb", "gate"))["status"] == "pending"
    denied = client.post(
        f"/api/v2/runs/{rid}/approvals/gate",
        headers=auth, json={**binding, "status": "denied"})
    assert denied.status_code == 200
    assert denied.json()["status"] == "cancelled"
    grant = asyncio.run(v2.get_approval_grant(rid, "uzeb", "gate"))
    assert grant["status"] == "denied"
    replay = client.post(
        f"/api/v2/runs/{rid}/approvals/gate",
        headers=auth, json={**binding, "status": "approved"})
    assert replay.status_code == 409


def test_approval_state_reports_the_actual_descriptor_and_fails_closed(client, auth):
    rid, _workflow, parked = asyncio.run(_parked_gate())
    resp = client.get(f"/api/v2/runs/{rid}/approval", headers=auth)
    assert resp.status_code == 200
    state = resp.json()
    assert state["state"] == "pending"
    assert state["grant_id"] == parked["grant_id"]
    assert state["binding_hash"] == parked["binding_hash"]
    assert state["canonical_args"]["step_id"] == "gate"
    # Honest copy: a checkpoint is not authority over a named tool/command.
    assert "no specific tool or command is bound" in state["authority"]
    assert client.get("/api/v2/runs/does-not-exist/approval", headers=auth).status_code == 404


def test_expired_gate_reissue_requires_a_fresh_human_decision(client, auth):
    rid, _workflow, parked = asyncio.run(_parked_gate())
    old = {"grant_id": parked["grant_id"], "binding_hash": parked["binding_hash"]}

    async def expire():
        async with v2.db.open_db() as conn:
            await conn.execute(
                "UPDATE approval_grants SET expires_at=? WHERE id=?",
                (time.time() - 1, old["grant_id"]))
            await conn.commit()

    asyncio.run(expire())
    expired = client.post(
        f"/api/v2/runs/{rid}/approvals/gate",
        headers=auth, json={**old, "status": "approved"})
    assert expired.status_code == 410
    assert client.get(f"/api/v2/runs/{rid}/approval", headers=auth).json()["state"] == "expired"

    fresh = client.post(f"/api/v2/runs/{rid}/approval/reissue", headers=auth)
    assert fresh.status_code == 200
    new_state = fresh.json()
    assert new_state["state"] == "pending"
    assert new_state["grant_id"] != old["grant_id"]
    assert new_state["binding_hash"] != old["binding_hash"]
    # The old decision cannot authorise the freshly re-requested gate.
    stale = client.post(
        f"/api/v2/runs/{rid}/approvals/gate",
        headers=auth, json={**old, "status": "approved"})
    assert stale.status_code == 409
    assert client.get(f"/api/v2/runs/{rid}/approval", headers=auth).json()["state"] == "pending"


def test_ambiguous_gate_fails_closed_and_rearm_uses_a_fresh_binding(client, auth):
    rid, _workflow, parked = asyncio.run(_parked_gate())

    async def make_ambiguous():
        await v2.resolve_approval_grant(
            rid, "uzeb", "gate", "approved", resolved_by="uzeb",
            grant_id=parked["grant_id"], binding_hash=parked["binding_hash"])
        async with v2.db.open_db() as conn:
            await conn.execute(
                "UPDATE approval_grants SET status='consumed', consumed_at=? WHERE id=?",
                (time.time(), parked["grant_id"]))
            await conn.commit()

    asyncio.run(make_ambiguous())
    state = client.get(f"/api/v2/runs/{rid}/approval", headers=auth)
    assert state.status_code == 200
    assert state.json()["state"] == "ambiguous"
    with pytest.raises(v2.ApprovalAmbiguous):
        asyncio.run(v2.claim_run(rid, "uzeb", worker_id="intruder"))

    rearmed = client.post(
        f"/api/v2/runs/{rid}/approval/reconcile",
        headers=auth, json={"action": "rearm"})
    assert rearmed.status_code == 200
    out = rearmed.json()
    assert out["state"] == "pending"
    assert out["grant_id"] != parked["grant_id"]
    assert out["binding_hash"] != parked["binding_hash"]
    # The superseded consumed grant no longer marks the gate as done.
    assert asyncio.run(v2.consumed_gate_steps(rid, "uzeb")) == set()


def _force_ambiguous(run_id: str, owner: str) -> None:
    async def run():
        now = time.time()
        async with v2.db.open_db() as conn:
            await conn.execute(
                "INSERT INTO approval_grants(id, run_id, owner, step_id, agent, scope,"
                " canonical_args, binding_hash, status, created_at, expires_at,"
                " consumed_at) VALUES(?,?,?,?,?,?,?,?,'consumed',?,?,?)",
                (f"agr_{run_id[-6:]}", run_id, owner, "gate", "swarm", "run",
                 "{}", "stub", now, now + 100, now))
            await conn.execute(
                "UPDATE runs SET status='waiting_for_approval' WHERE id=?", (run_id,))
            await conn.commit()

    asyncio.run(run())


def test_admin_can_reconcile_a_run_owned_by_someone_else(client, auth):
    async def setup():
        await v2.init_db()
        created = await v2.create_run("alice", "other owner gate", None)
        return created["id"]

    rid = asyncio.run(setup())
    _force_ambiguous(rid, "alice")
    resp = client.post(
        f"/api/v2/runs/{rid}/approval/reconcile",
        headers=auth, json={"action": "cancel"})
    assert resp.status_code == 200
    assert resp.json()["run_status"] == "cancelled"
    assert asyncio.run(v2.get_run(rid, "alice"))["status"] == "cancelled"


def test_non_admin_is_rejected_by_reconcile_route(client, auth):
    registered = client.post("/api/register", json={
        "handle": "member-reconcile", "password": "pw12345678"})
    assert registered.status_code == 200
    member_auth = {"Authorization": f"Bearer {registered.json()['token']}"}
    rid, _workflow, _parked = asyncio.run(_parked_gate())
    _force_ambiguous(rid, "uzeb")
    resp = client.post(
        f"/api/v2/runs/{rid}/approval/reconcile",
        headers=member_auth, json={"action": "cancel"})
    assert resp.status_code == 403
    assert asyncio.run(v2.get_run(rid, "uzeb"))["status"] == "waiting_for_approval"


def test_two_claimants_at_a_granted_gate_do_not_emit_false_ambiguity(client):
    rid, _workflow, parked = asyncio.run(_parked_gate())
    asyncio.run(v2.resolve_approval_grant(
        rid, "uzeb", "gate", "approved", resolved_by="uzeb",
        grant_id=parked["grant_id"], binding_hash=parked["binding_hash"]))

    async def race():
        return await asyncio.gather(
            v2.claim_run(rid, "uzeb", worker_id="first"),
            v2.claim_run(rid, "uzeb", worker_id="second"),
        )

    claims = asyncio.run(race())
    assert sum(c is not None for c in claims) == 1
    events = asyncio.run(v2.list_events(rid, "uzeb"))
    assert not [e for e in events if e["event_type"] == "approval_ambiguous"]
    assert not [e for e in events if e["event_type"] == "execution_ambiguous"]
    assert asyncio.run(v2.get_run(rid, "uzeb"))["status"] == "running"


def test_concurrent_decisions_consume_exactly_one_grant(client):
    rid, _workflow, parked = asyncio.run(_parked_gate())

    async def decide():
        return await v2.resolve_approval_grant(
            rid, "uzeb", "gate", "approved", resolved_by="uzeb",
            grant_id=parked["grant_id"], binding_hash=parked["binding_hash"])

    async def both():
        return await asyncio.gather(decide(), decide())

    outcomes = asyncio.run(both())
    states = [out["state"] for out in outcomes]
    assert states.count("resolved") == 1
    assert states.count("already") == 1


def test_workflow_edit_after_park_is_stale_and_reissue_recovers(client, auth):
    rid, workflow, parked = asyncio.run(_parked_gate())

    async def edit():
        graph = dict(workflow["graph"])
        graph["nodes"] = [
            {**node, "label": "Changed after review"} if node.get("id") == "gate" else node
            for node in graph["nodes"]
        ]
        assert await v2.update_workflow(workflow["id"], "uzeb", {"graph": graph})

    asyncio.run(edit())
    state = client.get(f"/api/v2/runs/{rid}/approval", headers=auth)
    assert state.status_code == 200
    out = state.json()
    assert out["state"] == "stale"
    assert out["can_reissue"] is True
    assert out["grant_id"] == parked["grant_id"]

    fresh = client.post(f"/api/v2/runs/{rid}/approval/reissue", headers=auth)
    assert fresh.status_code == 200
    new_state = fresh.json()
    assert new_state["state"] == "pending"
    assert new_state["grant_id"] != parked["grant_id"]
    assert new_state["binding_hash"] != parked["binding_hash"]


def test_http_approval_runs_through_to_completion(client, auth):
    rid, _workflow, parked = asyncio.run(_parked_gate())
    decision = client.post(
        f"/api/v2/runs/{rid}/approvals/gate", headers=auth,
        json={"status": "approved", "grant_id": parked["grant_id"],
              "binding_hash": parked["binding_hash"]})
    assert decision.status_code == 200

    def completed():
        current = client.get(f"/api/v2/runs/{rid}", headers=auth).json()
        return current if current["status"] == "completed" else None

    done = _wait_for_http(completed)
    assert done["status"] == "completed"
    grant = asyncio.run(v2.get_approval_grant(rid, "uzeb", "gate"))
    assert grant["status"] == "consumed"


def _wait_for_http(predicate, timeout=10.0):
    import time as _t
    deadline = _t.time() + timeout
    while _t.time() < deadline:
        result = predicate()
        if result:
            return result
        _t.sleep(0.1)
    raise AssertionError("timed out waiting for run to complete")


def test_consume_is_scoped_to_the_parked_gate_step(client):
    import json

    async def run():
        await v2.init_db()
        workflow = await v2.create_workflow("uzeb", "Two gates", "", {
            "nodes": [{"id": "g1", "type": "approval"}, {"id": "g2", "type": "approval"}],
            "edges": [["g1", "g2"]],
        })
        created = await v2.create_run("uzeb", "two gates", workflow["id"])
        rid = created["id"]
        now = time.time()
        async with v2.db.open_db() as conn:
            for step in ("g1", "g2"):
                canonical, digest = v2._gate_binding(
                    created, workflow, step, nonce=f"agr_{step}")
                await conn.execute(
                    "INSERT INTO approval_grants(id, run_id, owner, step_id, agent, scope,"
                    " canonical_args, binding_hash, status, created_at, expires_at)"
                    " VALUES(?,?,?,?,?,?,?,?, 'granted', ?, ?)",
                    (f"agr_{step}", rid, "uzeb", step, canonical["agent"],
                     canonical["scope"],
                     json.dumps(canonical, sort_keys=True, separators=(",", ":")),
                     digest, now, now + 100))
            await conn.execute(
                "UPDATE runs SET status='waiting_for_approval' WHERE id=?", (rid,))
            await conn.commit()
        parked_run = await v2.get_run(rid, "uzeb")
        claim = await v2._consume_grant_for_claim(
            parked_run, "worker", 30, step_id="g1")
        assert claim is not None
        assert (await v2.get_approval_grant(rid, "uzeb", "g1"))["status"] == "consumed"
        assert (await v2.get_approval_grant(rid, "uzeb", "g2"))["status"] == "granted"

    asyncio.run(run())


def test_lost_lease_leaves_a_durable_ambiguity_event(client, monkeypatch):
    async def run():
        await v2.init_db()
        created = await v2.create_run("uzeb", "ambiguous execution", None)
        rid = created["id"]
        real_update = v2.update_run
        calls = {"n": 0}

        async def takeover(*args, **kwargs):
            calls["n"] += 1
            if calls["n"] == 1:
                raise v2.StaleRunClaim("another executor took the lease")
            return await real_update(*args, **kwargs)

        monkeypatch.setattr(v2, "update_run", takeover)
        await v2.execute_run(rid, "uzeb")
        events = await v2.list_events(rid, "uzeb")
        ambiguous = [e for e in events if e["event_type"] == "execution_ambiguous"]
        assert len(ambiguous) == 1
        assert "ambiguous" in ambiguous[0]["payload"]["detail"]

    asyncio.run(run())


def test_stale_executor_artifact_write_is_rejected(client, tmp_path):
    from backend import agent as agent_mod

    async def run():
        await v2.init_db()
        run = await v2.create_run("uzeb", "artifact fence", None)
        rid = run["id"]
        claim = await v2.claim_run(rid, "uzeb", worker_id="owner")
        assert claim is not None
        old_root = agent_mod.SANDBOX_DIR
        agent_mod.SANDBOX_DIR = str(tmp_path)
        try:
            bad = v2.RunClaim(rid, "uzeb", "intruder", claim.generation - 1, time.time(), 30)
            assert await v2.create_artifact(rid, "uzeb", "stale.md", "x", claim=bad) is None
            good = await v2.create_artifact(rid, "uzeb", "fresh.md", "x", claim=claim)
            assert good is not None
            assert [a["name"] for a in await v2.list_artifacts(rid, "uzeb")] == ["fresh.md"]
        finally:
            agent_mod.SANDBOX_DIR = old_root

    asyncio.run(run())
