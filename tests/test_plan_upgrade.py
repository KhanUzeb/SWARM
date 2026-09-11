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


def test_chat_model_override_reaches_agent_and_persists(client, auth, monkeypatch):
    import time as time_mod

    seen = {}

    async def fake_reply(agent_row, channel_id, history, on_tools_ready=None,
                         on_stream_start=None, on_token=None, model_override=None, **_kwargs):
        seen["override"] = model_override
        if on_stream_start is not None:
            await on_stream_start()
        if on_token is not None:
            await on_token("live ")
            await on_token("reply")
        return {"reply": "live reply", "tool_events": [], "usage": {},
                "model": model_override or "agent-default"}

    monkeypatch.setattr("backend.agent.generate_reply", fake_reply)
    monkeypatch.setattr("backend.main.agent.generate_reply", fake_reply)

    posted = client.post(
        "/api/channels/dm-swarm/messages",
        json={"author": "uzeb", "body": "answer with override", "model": "openai/gpt-oss-20b"},
        headers=auth,
    )
    assert posted.status_code == 200, posted.text

    deadline = time_mod.time() + 10.0
    agent_msgs = []
    while time_mod.time() < deadline:
        history = client.get("/api/channels/dm-swarm/messages", headers=auth).json()
        agent_msgs = [m for m in history if m.get("author_kind") == "agent"]
        if agent_msgs:
            break
        time_mod.sleep(0.1)
    assert agent_msgs, "no agent reply arrived"
    assert seen["override"] == "openai/gpt-oss-20b"
    assert agent_msgs[-1]["model"] == "openai/gpt-oss-20b"
    assert agent_msgs[-1]["body"] == "live reply"


def test_messages_model_column_migrates(tmp_path, monkeypatch):
    import asyncio

    import backend.db as db_mod

    db_path = tmp_path / "swarm.db"
    monkeypatch.setenv("SWARM_DB_PATH", str(db_path))
    db_mod.DB_PATH = db_path

    async def setup():
        import aiosqlite

        async with aiosqlite.connect(db_path) as conn:
            await conn.execute(
                "CREATE TABLE messages (id INTEGER PRIMARY KEY AUTOINCREMENT,"
                " channel_id TEXT NOT NULL, parent_id INTEGER, author TEXT NOT NULL,"
                " author_kind TEXT NOT NULL DEFAULT 'human', body TEXT NOT NULL,"
                " created_at REAL NOT NULL)")
            await conn.commit()
        await db_mod.init_db()

    asyncio.run(setup())
    msg = asyncio.run(db_mod.add_message("general", "swarm", "hi", "agent", model="x-model"))
    assert msg["model"] == "x-model"
    history = asyncio.run(db_mod.get_history("general"))
    assert history[0]["model"] == "x-model"


def test_stop_endpoint_cancels_nothing_by_default(client, auth):
    res = client.post("/api/channels/general/stop", headers=auth)
    assert res.status_code == 200
    assert res.json() == {"ok": True, "stopped": 0}
    assert client.post("/api/channels/nope/stop", headers=auth).status_code in (403, 404)


def test_retry_accepts_stopped_cutoff(client, auth, monkeypatch):
    import asyncio

    import backend.db as db_mod

    async def seed():
        await db_mod.add_message("general", "uzeb", "please help", "human")
        return await db_mod.add_message(
            "general", "swarm", "partial thought\n[reply cut off — stopped]", "agent")

    error_msg = asyncio.run(seed())

    async def fake_reply(*_args, **_kwargs):
        return {"reply": "resumed", "tool_events": [], "usage": {}, "model": "m"}

    monkeypatch.setattr("backend.main.agent.generate_reply", fake_reply)
    res = client.post(f"/api/messages/{error_msg['id']}/retry", headers=auth)
    assert res.status_code == 200
    assert res.json()["ok"] is True


def test_model_override_falls_back_when_gone(client, monkeypatch):
    import asyncio

    import backend.agent as agent_mod
    import backend.ai_support.resolver as resolver_mod

    async def yes_ready():
        return True

    async def fake_attempts(model):
        return [(object(), model, "groq")]

    calls = []

    async def fake_complete(_client, model, *_args, **_kwargs):
        calls.append(model)
        if model == "bad-model-xyz":
            err = RuntimeError("model bad-model-xyz does not exist")
            err.status_code = 404
            raise err
        return "fallback answer", [], None

    monkeypatch.setattr(resolver_mod, "any_provider_ready", yes_ready)
    monkeypatch.setattr(resolver_mod, "iter_provider_attempts", fake_attempts)
    monkeypatch.setattr(resolver_mod, "iter_openai_compatible_attempts", fake_attempts)
    monkeypatch.setattr(agent_mod, "_complete_stream", fake_complete)
    monkeypatch.setattr(agent_mod, "RETRY_DELAY_SECONDS", 0)

    row = {"name": "swarm", "system_prompt": "You are swarm.", "model": "row-model",
           "history_window": 12, "max_tool_calls": 1, "tools": []}
    history = [{"author_kind": "human", "author": "uzeb", "body": "hi"}]
    result = asyncio.run(agent_mod.generate_reply(row, "general", history, model_override="bad-model-xyz"))
    assert result["reply"] == "fallback answer"
    assert result.get("model_fallback") is True
    assert result["model"] != "bad-model-xyz"


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
