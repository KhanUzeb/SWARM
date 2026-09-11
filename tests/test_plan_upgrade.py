"""Plan upgrade coverage: state machine, policy engine, result contract,
evaluation harness, memory graph, and the new v2 progress/verify APIs."""
from __future__ import annotations


def test_state_machine_transitions():
    from backend import work_state

    assert work_state.can_transition("queued", "planning")
    assert work_state.can_transition("executing", "waiting_for_approval")
    assert work_state.can_transition("verifying", "completed")
    assert not work_state.can_transition("completed", "executing")
    assert not work_state.can_transition("queued", "completed")


def test_derive_state_folds_events():
    from backend import work_state

    events = [
        {"event_type": "run_queued", "payload": {}},
        {"event_type": "step_started", "step_id": "s1", "payload": {"step": "Inspect metrics"}},
        {"event_type": "approval_requested", "payload": {}},
    ]
    assert work_state.derive_state(events, "running") == "waiting_for_approval"
    progress = work_state.summarize_progress(events)
    assert progress["plan"] == ["Inspect metrics"]
    assert progress["current_step"] == "Inspect metrics"


def test_result_contract_shape():
    from backend import work_state

    contract = work_state.build_result_contract(
        summary="done", evidence=[{"a": 1}], confidence=0.9)
    assert contract["status"] == "completed"
    assert contract["evidence"] == [{"a": 1}]
    assert contract["verification"] == []
    assert contract["confidence"] == 0.9
    assert contract["needs_human_review"] is False


def test_policy_engine_defaults_and_overrides():
    from backend import policy

    assert policy.evaluate(tool="search_history")["decision"] == "allow"
    assert policy.evaluate(tool="edit_branch")["decision"] == "review"
    assert policy.evaluate(tool="push_to_main", target="main")["decision"] == "deny"
    assert policy.evaluate(tool="deploy_production")["decision"] == "deny"
    res = policy.evaluate(agent="coder", tool="edit_branch",
                          overrides={"coder": {"edit_branch": "allow"}})
    assert res["decision"] == "allow"
    card = policy.approval_card({"title": "Push", "impact": ["7 files"]}, res)
    assert card["title"] == "Push"
    assert "decision" in card


def test_eval_harness_scoring_and_compare():
    from backend import evals

    evals.register_task("t1", "coding", "Fix the bug", ["tests pass"])
    assert any(t["id"] == "t1" for t in evals.list_tasks("coding"))
    events = [
        {"event_type": "run_queued", "created_at": 100.0},
        {"event_type": "tool_started", "created_at": 101.0},
        {"event_type": "tool_finished", "created_at": 102.0},
        {"event_type": "verification_passed", "created_at": 103.0},
        {"event_type": "run_completed", "created_at": 104.0},
    ]
    score = evals.score_run(events, human_effort=1)
    assert score["task_success"] is True
    assert score["verified_success"] is True
    assert score["tool_calls"] == 2
    assert score["human_effort_label"] == "one-click approval"
    table = evals.compare_architectures({
        "multi_agent": [score],
        "single_chat": [evals.score_run([{"event_type": "run_failed"}], 4)],
    })
    assert table["multi_agent"]["success_rate"] == 1.0
    assert table["single_chat"]["success_rate"] == 0.0


def test_event_lock_survives_loop_turnover(client):
    """A lock contended in one loop must not poison the next loop.

    Regression: the old module-global lock bound to the first contending
    loop, so any later loop (uvicorn --reload, TestClient portals) failed
    event appends with 'bound to a different event loop'.
    """
    import asyncio

    from backend import v2 as v2_mod
    from backend import work_state

    async def bind_loop():
        lock = work_state.event_lock()
        started = asyncio.Event()

        async def waiter():
            started.set()
            await lock.acquire()
            lock.release()

        await lock.acquire()
        task = asyncio.create_task(waiter())
        await started.wait()
        await asyncio.sleep(0.01)  # let the waiter block on the lock
        lock.release()
        await task
        return lock

    assert isinstance(asyncio.run(bind_loop()), asyncio.Lock)

    async def fresh_loop_appends():
        run = await v2_mod.create_run("uzeb", "loop turnover", None)
        await v2_mod.append_event(run["id"], "step_started", {}, step_id="s1")
        await v2_mod.append_event(run["id"], "step_completed", {"ok": True}, step_id="s1")
        return await v2_mod.list_events(run["id"], "uzeb")

    seqs = [e["seq"] for e in asyncio.run(fresh_loop_appends())]
    assert seqs == sorted(seqs) and len(set(seqs)) == len(seqs)


async def test_memory_graph_relations(tmp_path, monkeypatch):
    import backend.db as db_mod
    from backend import memory_graph

    db_path = tmp_path / "swarm.db"
    monkeypatch.setenv("SWARM_DB_PATH", str(db_path))
    db_mod.DB_PATH = db_path

    rec = memory_graph.memory_record(1, source="run", confidence=0.9, owner="uzeb")
    assert rec["confidence"] == 0.9
    saved = await memory_graph.save_memory_meta(rec)
    assert saved["memory_id"] == 1
    rel = await memory_graph.add_relation("Project", "contains", "Repo")
    assert rel["src"] == "Project"
    found = await memory_graph.related("Project")
    assert any(r["dst"] == "Repo" for r in found)

    ranked = memory_graph.rank_candidates("repo", [
        {"id": 1, "text": "unrelated", "created_at": 1.0, "confidence": 0.1},
        {"id": 2, "text": "repo deploy", "created_at": 9999999999.0, "confidence": 0.9},
    ])
    assert ranked[0]["id"] == 2


def test_progress_and_verify_endpoints(client, auth):
    run = client.post("/api/v2/runs", json={"objective": "Probe run"}, headers=auth)
    assert run.status_code == 200, run.text
    run_id = run.json()["id"]

    states = client.get("/api/v2/work-states", headers=auth)
    assert states.status_code == 200
    assert "executing" in states.json()["states"]

    progress = client.get(f"/api/v2/runs/{run_id}/progress", headers=auth)
    assert progress.status_code == 200
    body = progress.json()
    assert body["run_id"] == run_id
    assert "state" in body and "plan" in body

    verify = client.post(f"/api/v2/runs/{run_id}/verify", json={
        "passed": True, "checks": ["result-review"], "summary": "Looks good",
        "evidence": [{"source": "test"}], "confidence": 0.9,
    }, headers=auth)
    assert verify.status_code == 200, verify.text
    result = verify.json()["result"]
    assert result["status"] == "completed"
    assert result["confidence"] == 0.9
    assert result["evidence"] == [{"source": "test"}]


def test_task_routing_classifies_and_routes():
    from backend import routing

    coding = routing.classify("Fix the checkout latency bug and add a benchmark")
    assert coding["task_type"] == "coding"
    research = routing.classify("Research the competitor landscape with sources")
    assert research["task_type"] == "research"
    chat = routing.classify("hello there")
    assert chat["task_type"] == "chat"
    assert routing.needs_tools("coding") is True
    assert routing.needs_tools("chat") is False


def test_route_endpoint_fallback_without_providers(client, auth):
    res = client.post("/api/v2/model-routing/route", json={"objective": "Fix the bug"}, headers=auth)
    assert res.status_code == 200
    body = res.json()
    assert body["task_type"] == "coding"
    assert body["requires_tools"] is True
    assert "reasons" in body

    empty = client.post("/api/v2/model-routing/route", json={"objective": "  "}, headers=auth)
    assert empty.status_code == 422


def test_route_endpoint_picks_connected_provider(client, auth, monkeypatch):
    from backend.ai_support import resolver as resolver_mod

    async def fake_auth(provider_id):
        if provider_id == "groq":
            return type("Auth", (), {"default_model": "openai/gpt-oss-120b"})()
        return None

    monkeypatch.setattr(resolver_mod, "resolve_runtime_auth", fake_auth)
    res = client.post("/api/v2/model-routing/route", json={"objective": "Research launch risks"}, headers=auth)
    assert res.status_code == 200
    body = res.json()
    assert body["task_type"] == "research"
    assert body["provider_id"] == "groq"
    assert body["model"] == "openai/gpt-oss-120b"


def test_policy_endpoint(client, auth):
    res = client.post("/api/v2/policy/evaluate", json={
        "agent": "coder", "tool": "push_to_main", "target": "main",
    }, headers=auth)
    assert res.status_code == 200
    assert res.json()["evaluation"]["decision"] == "deny"
    assert "approval_card" in res.json()


def test_eval_and_relation_endpoints(client, auth):
    res = client.post("/api/v2/evals/tasks", json={
        "id": "e2e-demo", "category": "planning", "prompt": "Plan release",
    }, headers=auth)
    assert res.status_code == 200
    listed = client.get("/api/v2/evals/tasks", headers=auth)
    assert listed.status_code == 200

    scored = client.post("/api/v2/evals/score", json={
        "events": [{"event_type": "run_completed"}], "human_effort": 0,
    }, headers=auth)
    assert scored.status_code == 200
    assert scored.json()["task_success"] is True

    rel = client.post("/api/knowledge/relations", json={
        "src": "Release", "rel": "decided-by", "dst": "Owner",
    }, headers=auth)
    assert rel.status_code == 200, rel.text
    found = client.get("/api/knowledge/relations", params={"entity": "Release"}, headers=auth)
    assert found.status_code == 200
