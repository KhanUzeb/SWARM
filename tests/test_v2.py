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
        json={"objective": "Check the release notes", "workflow_id": workflow["id"]},
    )
    assert run.status_code == 200
    run_body = run.json()
    assert run_body["status"] == "queued"

    events = client.get(f"/api/v2/runs/{run_body['id']}/events", headers=auth)
    assert events.status_code == 200
    assert events.json()[0]["event_type"] == "run_queued"


def test_v2_resources_are_private(client, auth):
    run = client.post("/api/v2/runs", headers=auth, json={"objective": "private brief"}).json()
    other = client.post("/api/register", json={"handle": "other"}).json()["token"]
    assert client.get(f"/api/v2/runs/{run['id']}", headers={"Authorization": f"Bearer {other}"}).status_code == 404
    assert client.get("/api/v2/workflows").status_code == 401


def test_provider_catalog_has_broad_api_key_and_oauth_metadata(client, auth):
    response = client.get("/api/ai-support/providers", headers=auth)
    assert response.status_code == 200
    providers = {row["id"]: row for row in response.json()}
    for provider_id in ("openai", "anthropic", "groq", "google", "mistral", "deepseek", "xai", "fireworks", "perplexity"):
        assert provider_id in providers
        assert "api_key" in (providers[provider_id].get("auth_methods") or ["api_key"])
    assert "oauth" in providers["google"]["auth_methods"]


def test_v2_rejects_invalid_workflow_graph(client, auth):
    response = client.post(
        "/api/v2/workflows",
        headers=auth,
        json={"name": "Bad graph", "graph": {"nodes": [{"id": "a"}, {"id": "a"}], "edges": [["a", "missing"]]}},
    )
    assert response.status_code == 422
    assert "node ids must be unique" in str(response.json()["detail"])


def test_v2_provider_catalog_and_routing_preflight(client, auth):
    providers = client.get("/api/v2/providers", headers=auth)
    assert providers.status_code == 200
    assert any(p["id"] == "deepseek" for p in providers.json())
    check = client.get("/api/v2/model-routing/validate", headers=auth, params={"provider_id": "groq", "model": "openai/gpt-oss-20b", "requires_tools": "true"})
    assert check.status_code == 200
    assert check.json()["supported"] is True


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
import time

from backend.v2 import order_nodes
