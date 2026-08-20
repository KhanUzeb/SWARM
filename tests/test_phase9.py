import asyncio

import backend.agent as agent
import backend.db as db
import backend.main as main
from backend.models import AgentCreate, DEFAULT_GROQ_MODEL, FAST_GROQ_MODEL, resolve_groq_model


def test_groq_model_aliases():
    assert resolve_groq_model("llama-3.1-8b-instant") == FAST_GROQ_MODEL
    assert resolve_groq_model("llama-3.3-70b-versatile") == DEFAULT_GROQ_MODEL
    assert resolve_groq_model("openai/gpt-oss-20b") == "openai/gpt-oss-20b"
    created = AgentCreate(name="x", system_prompt="hi", model="llama-3.1-8b-instant")
    assert created.model == FAST_GROQ_MODEL


def _clear_rate():
    main._last_write.clear()


def test_seeded_harness(client):
    agents = {a["name"]: a for a in client.get("/api/agents").json()}
    assert "read_only_shell" in agents["swarm"]["tools"]
    assert "remember" in agents["swarm"]["tools"]
    assert "read_only_shell" not in agents["ledger"]["tools"]
    assert agents["swarm"]["history_window"] == 12
    assert agents["ledger"]["max_tool_calls"] == 3
    assert agents["swarm"]["model"] == DEFAULT_GROQ_MODEL
    assert agents["ledger"]["model"] == DEFAULT_GROQ_MODEL


def test_get_agent_includes_memories(client):
    data = client.get("/api/agents/swarm").json()
    assert data["name"] == "swarm"
    assert "memories" in data
    assert data["memories"] == []
    assert client.get("/api/agents/nope").status_code == 404


def test_create_and_patch_agent(client, auth):
    _clear_rate()
    created = client.post(
        "/api/agents",
        json={
            "name": "scribe",
            "system_prompt": "You take notes.",
            "tools": ["remember", "recall"],
            "history_window": 8,
        },
        headers=auth,
    )
    assert created.status_code == 200
    body = created.json()
    assert body["name"] == "scribe"
    assert body["tools"] == ["remember", "recall"]
    assert body["history_window"] == 8
    assert body["model"] == DEFAULT_GROQ_MODEL

    _clear_rate()
    patched = client.patch(
        "/api/agents/scribe",
        json={"history_window": 20, "channel_scope": "general"},
        headers=auth,
    )
    assert patched.status_code == 200
    assert patched.json()["history_window"] == 20
    assert patched.json()["channel_scope"] == "general"

    _clear_rate()
    again = client.post(
        "/api/agents",
        json={"name": "scribe", "system_prompt": "dup"},
        headers=auth,
    )
    assert again.status_code == 409


def test_memory_roundtrip_and_context(client):
    async def _go():
        note = await db.add_memory("swarm", "ship date is Friday", channel_id="general")
        assert note["kind"] == "note"
        notes, summary = await db.get_context_memories("swarm", "general")
        assert any("Friday" in n["body"] for n in notes)
        hits = await db.search_memory("swarm", "Friday", channel_id="general")
        assert hits
        return notes, summary

    notes, summary = asyncio.run(_go())
    messages = agent._build_messages(
        "You are swarm.",
        [{"author_kind": "human", "author": "uzeb", "body": "hi"}],
        notes=notes,
        summary=summary,
    )
    assert "Known notes:" in messages[0]["content"]
    assert "ship date is Friday" in messages[0]["content"]


def test_classify_and_retryable():
    missing = RuntimeError("missing_key")
    assert "no API key" in agent.classify_error(missing)
    rate = RuntimeError("rate limit exceeded")
    rate.status_code = 429
    assert "rate limited" in agent.classify_error(rate)
    assert agent.is_retryable(rate)
    timeout = TimeoutError("timeout")
    assert agent.is_retryable(timeout)
    assert "timed out" in agent.classify_error(timeout)


def test_compact_summary_only_when_window_full():
    history = [
        {"author_kind": "human", "author": "a", "body": f"m{i}"}
        for i in range(5)
    ]
    assert agent.compact_summary(history, 12) is None
    history = history + [{"author_kind": "human", "author": "a", "body": f"m{i}"} for i in range(5, 14)]
    text = agent.compact_summary(history, 12)
    assert text is not None
    assert "m0" in text
    assert "m13" not in text  # still inside the live window


def test_retry_once_then_succeeds(client, monkeypatch):
    calls = {"n": 0, "model": None}

    async def fake_complete(_client, model, *_args, **_kwargs):
        calls["n"] += 1
        calls["model"] = model
        if calls["n"] == 1:
            err = RuntimeError("rate limit")
            err.status_code = 429
            raise err
        return "hello after retry", [], None

    monkeypatch.setattr(agent, "_groq_client", lambda: object())
    monkeypatch.setattr(agent, "_openrouter_client", lambda: None)
    monkeypatch.setattr(agent, "_complete_stream", fake_complete)
    monkeypatch.setattr(agent, "RETRY_DELAY_SECONDS", 0)

    row = {
        "name": "swarm",
        "system_prompt": "You are swarm.",
        "model": "llama-3.3-70b-versatile",
        "history_window": 12,
        "max_tool_calls": 3,
        "tools": ["remember", "recall"],
    }
    history = [{"author_kind": "human", "author": "uzeb", "body": "hi @swarm"}]
    result = asyncio.run(agent.generate_reply(row, "general", history))
    assert result["reply"] == "hello after retry"
    assert calls["n"] == 2
    assert calls["model"] == DEFAULT_GROQ_MODEL


def test_classified_error_when_no_provider(client, monkeypatch):
    def boom():
        raise RuntimeError("missing_key")

    monkeypatch.setattr(agent, "_groq_client", boom)
    monkeypatch.setattr(agent, "_openrouter_client", lambda: None)
    row = {
        "name": "swarm",
        "system_prompt": "You are swarm.",
        "model": "x",
        "history_window": 12,
        "max_tool_calls": 3,
        "tools": [],
    }
    result = asyncio.run(agent.generate_reply(
        row, "general", [{"author_kind": "human", "author": "uzeb", "body": "hi"}],
    ))
    assert result["reply"].startswith("[agent error:")
    assert "API key" in result["reply"]
