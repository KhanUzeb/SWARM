"""Memory management, budget-aware context, and the knowledge base."""
import asyncio

import backend.db as db
import backend.main as main


def _clear_rate():
    main._last_write.clear()


# --------------------------------------------------------------- memory ---
def test_forget_tool_by_id_and_query(client, monkeypatch):
    from backend.tools.registry import get_registry

    async def scenario():
        row = await db.add_memory("swarm", "the deploy key is hunter2", channel_id="general")
        assert (await db.search_memory("swarm", "deploy", channel_id="general"))
        registry = get_registry()
        by_id = await registry.execute(
            "forget", {"target": str(row["id"])},
            agent_name="swarm", channel_id="general", allowed=["forget"],
            sandbox_dir="", shell_runner=lambda cmd: "", workspace_helpers={},
        )
        assert "forgot note" in by_id
        row2 = await db.add_memory("swarm", "stale fact about deploy", channel_id="general")
        assert row2["id"] != row["id"]
        by_query = await registry.execute(
            "forget", {"target": "stale fact"},
            agent_name="swarm", channel_id="general", allowed=["forget"],
            sandbox_dir="", shell_runner=lambda cmd: "", workspace_helpers={},
        )
        assert "forgot 1 note" in by_query
        assert not await db.search_memory("swarm", "stale", channel_id="general")
        missing = await registry.execute(
            "forget", {"target": "nothing matches this"},
            agent_name="swarm", channel_id="general", allowed=["forget"],
            sandbox_dir="", shell_runner=lambda cmd: "", workspace_helpers={},
        )
        assert "no matching notes" in missing

    asyncio.run(scenario())


def test_memory_endpoints_list_and_forget(client, auth):
    async def seed():
        return await db.add_memory("swarm", "endpoint note", channel_id="general")

    row = asyncio.run(seed())
    listed = client.get("/api/agents/swarm/memory", headers=auth)
    assert listed.status_code == 200
    assert any(m["id"] == row["id"] for m in listed.json())

    assert client.get("/api/agents/nobody/memory", headers=auth).status_code == 404
    _clear_rate()
    gone = client.delete(f"/api/agents/swarm/memory/{row['id']}", headers=auth)
    assert gone.status_code == 200
    _clear_rate()
    assert client.delete(f"/api/agents/swarm/memory/{row['id']}", headers=auth).status_code == 404


def test_recall_lists_note_ids(client, auth):
    async def seed():
        return await db.add_memory("swarm", "recallable fact xyz", channel_id="general")

    row = asyncio.run(seed())
    from backend.tools.registry import get_registry

    async def run():
        return await get_registry().execute(
            "recall", {"query": "recallable"},
            agent_name="swarm", channel_id="general", allowed=["recall"],
            sandbox_dir="", shell_runner=lambda cmd: "", workspace_helpers={},
        )

    assert f"(id {row['id']})" in asyncio.run(run())


# --------------------------------------------------------------- context ---
def test_context_stats_and_compact(client, auth):
    for i in range(6):
        _clear_rate()
        client.post(
            "/api/channels/general/messages",
            json={"author": "uzeb", "body": f"context filler message {i}"},
            headers=auth,
        )
    stats = client.get("/api/channels/general/context?agent=swarm", headers=auth)
    assert stats.status_code == 200
    body = stats.json()
    assert body["messages"] >= 6
    assert body["budget_chars"] == 12_000
    assert body["history_chars"] > 0

    assert client.get("/api/channels/nope/context", headers=auth).status_code == 404

    # Too little history to compact yet.
    _clear_rate()
    fresh = client.post("/api/channels", json={"name": "tiny-room"}, headers=auth).json()
    _clear_rate()
    client.post(
        f"/api/channels/{fresh['id']}/messages",
        json={"author": "uzeb", "body": "just one line"},
        headers=auth,
    )
    _clear_rate()
    assert client.post(
        f"/api/channels/{fresh['id']}/compact", headers=auth, json={"agent": "swarm"}
    ).status_code == 409

    # Enough history compacts into a summary.
    for i in range(16):
        _clear_rate()
        client.post(
            "/api/channels/general/messages",
            json={"author": "uzeb", "body": f"history line {i} for the summary"},
            headers=auth,
        )
    _clear_rate()
    compacted = client.post(
        "/api/channels/general/compact", headers=auth, json={"agent": "swarm"})
    assert compacted.status_code == 200
    assert "Earlier in this channel" in compacted.json()["body"]
    stats2 = client.get("/api/channels/general/context?agent=swarm", headers=auth).json()
    assert stats2["has_summary"] is True


def test_build_context_trims_to_budget(client):
    from backend import context as context_mod

    history = [
        {"author": "uzeb", "author_kind": "human", "body": "x" * 2000}
        for _ in range(20)
    ]

    async def run():
        await db.add_memory("swarm", "keep me", channel_id="general")
        return await context_mod.build_context(
            "swarm", "general", history, window=20, budget_chars=3000)

    package = asyncio.run(run())
    assert package["stats"]["total_chars"] <= 3000
    assert package["stats"]["dropped_messages"] > 0
    assert package["history"]  # most recent turns survive


def test_knowledge_hits_reach_agent_prompt(client):
    from backend import context as context_mod
    from backend import knowledge as kb_mod

    async def direct():
        history = [{"author": "uzeb", "author_kind": "human", "body": "how do we deploy?"}]
        await kb_mod.create_doc(
            "agent:swarm", "Deploy runbook", "deploy with ./ship.sh on Fridays",
            channel_id="general", source="agent")
        return await context_mod.build_context("swarm", "general", history, window=5)

    package = asyncio.run(direct())
    assert package["stats"]["kb_hits"] >= 1
    assert "ship.sh" in package["kb_hits"][0]["body"]


# --------------------------------------------------------------- knowledge ---
def test_knowledge_crud_and_owner_isolation(client, auth):
    _clear_rate()
    created = client.post(
        "/api/knowledge", headers=auth,
        json={"title": "Oncall", "body": "page the oncall when deploy fails", "tags": "ops"},
    )
    assert created.status_code == 200
    doc = created.json()
    assert doc["id"].startswith("kb_")

    _clear_rate()
    assert client.post(
        "/api/knowledge", headers=auth, json={"title": "Empty", "body": "  "}).status_code == 400

    listed = client.get("/api/knowledge", headers=auth).json()
    assert any(d["id"] == doc["id"] for d in listed)

    found = client.get("/api/knowledge/search?q=oncall", headers=auth)
    assert found.status_code == 200
    assert any(d["id"] == doc["id"] for d in found.json())
    assert client.get("/api/knowledge/search?q=x", headers=auth).status_code == 400

    _clear_rate()
    patched = client.patch(
        f"/api/knowledge/{doc['id']}", headers=auth, json={"title": "Oncall v2"})
    assert patched.status_code == 200
    assert patched.json()["title"] == "Oncall v2"

    other = client.post("/api/register", json={"handle": "rival"}).json()["token"]
    foreign = {"Authorization": f"Bearer {other}"}
    assert client.get(f"/api/knowledge/{doc['id']}", headers=foreign).status_code == 404
    _clear_rate()
    assert client.delete(f"/api/knowledge/{doc['id']}", headers=foreign).status_code == 404
    assert client.get("/api/knowledge/search?q=oncall", headers=foreign).json() == []

    _clear_rate()
    assert client.delete(f"/api/knowledge/{doc['id']}", headers=auth).status_code == 200
    assert client.get(f"/api/knowledge/{doc['id']}", headers=auth).status_code == 404


def test_knowledge_agent_tools_roundtrip(client):
    from backend import knowledge as kb_mod
    from backend.tools.registry import get_registry

    async def run():
        registry = get_registry()
        saved = await registry.execute(
            "knowledge_save", {"title": "T", "body": "agent learned this fact"},
            agent_name="swarm", channel_id="general", allowed=["knowledge_save"],
            sandbox_dir="", shell_runner=lambda cmd: "", workspace_helpers={},
        )
        assert "saved knowledge" in saved
        hits = await registry.execute(
            "knowledge_search", {"query": "learned"},
            agent_name="swarm", channel_id="general", allowed=["knowledge_search"],
            sandbox_dir="", shell_runner=lambda cmd: "", workspace_helpers={},
        )
        assert "agent learned this fact" in hits
        # Other agents cannot see it (owner isolation).
        rival = await registry.execute(
            "knowledge_search", {"query": "learned"},
            agent_name="rival-bot", channel_id="general", allowed=["knowledge_search"],
            sandbox_dir="", shell_runner=lambda cmd: "", workspace_helpers={},
        )
        assert "no matches" in rival
        assert await kb_mod.count_docs("agent:swarm") >= 1

    asyncio.run(run())


# --------------------------------------------------------------- delete authz ---
def test_message_delete_requires_author_or_admin(client, auth):
    _clear_rate()
    posted = client.post(
        "/api/channels/general/messages",
        json={"author": "uzeb", "body": "mine, do not touch"},
        headers=auth,
    ).json()
    other = client.post("/api/register", json={"handle": "mallory"}).json()["token"]
    foreign = {"Authorization": f"Bearer {other}"}
    _clear_rate()
    assert client.delete(f"/api/messages/{posted['id']}", headers=foreign).status_code == 403

    # Admin (first user) may still moderate.
    _clear_rate()
    assert client.delete(f"/api/messages/{posted['id']}", headers=auth).status_code == 200

    # Author can delete their own message.
    _clear_rate()
    own = client.post(
        "/api/channels/general/messages",
        json={"author": "mallory", "body": "mallory's own line"},
        headers=foreign,
    ).json()
    _clear_rate()
    assert client.delete(f"/api/messages/{own['id']}", headers=foreign).status_code == 200


# --------------------------------------------------------------- agent guidance ---
def test_build_messages_guides_knowledge_tools():
    system_with = main.agent._build_messages(
        "prompt", [], allowed_tools=["knowledge_search", "knowledge_save"])[0]["content"]
    assert "Knowledge habit" in system_with
    system_without = main.agent._build_messages(
        "prompt", [], allowed_tools=["remember"])[0]["content"]
    assert "Knowledge habit" not in system_without
    system_forget = main.agent._build_messages(
        "prompt", [], allowed_tools=["forget"])[0]["content"]
    assert "Memory hygiene" in system_forget


# --------------------------------------------------------------- kb boost ---
def test_context_boosts_channel_kb_and_trims_global_first():
    import backend.context as ctx_mod
    import backend.knowledge as kb_mod

    async def seed():
        await kb_mod.create_doc(
            "agent:swarm", "Deploy runbook", "deploy checklist for general channel",
            channel_id="general")
        await kb_mod.create_doc(
            "agent:swarm", "Deploy notes", "deploy checklist global fallback")

    asyncio.run(seed())
    history = [{"author_kind": "human", "author": "uzeb", "body": "deploy checklist?"}]
    full = asyncio.run(ctx_mod.build_context("swarm", "general", history, kb_limit=5))
    assert full["kb_hits"], "expected KB hits for the deploy query"
    assert full["kb_hits"][0].get("boosted") is True
    assert full["kb_hits"][0].get("channel_id") == "general"

    starved = asyncio.run(
        ctx_mod.build_context("swarm", "general", history, kb_limit=5, budget_chars=65))
    kept = [h for h in starved["kb_hits"] if h.get("boosted")]
    assert kept, "channel-scoped hit should survive trimming"
    assert all(h.get("boosted") for h in starved["kb_hits"])
