import asyncio

import backend.agent as agent
import backend.ai_support.resolver as ai_resolver
import backend.db as db
from backend.models import AgentCreate, DEFAULT_GROQ_MODEL, FAST_GROQ_MODEL, resolve_groq_model


def test_model_aliases_and_tool_budget():
    from backend.models import AgentPatch
    assert resolve_groq_model("llama-3.1-8b-instant") == FAST_GROQ_MODEL
    assert resolve_groq_model("llama-3.3-70b-versatile") == DEFAULT_GROQ_MODEL
    assert resolve_groq_model("openai/gpt-oss-20b") == "openai/gpt-oss-20b"
    assert AgentCreate(name="x", system_prompt="hi", model="llama-3.1-8b-instant").model == FAST_GROQ_MODEL
    assert agent.max_tool_calls_of({}) == agent.MAX_TOOL_CALLS == 6
    assert agent.max_tool_calls_of({"max_tool_calls": 2}) == 2
    assert agent.max_tool_calls_of({"max_tool_calls": 99}) == agent.TOOL_CALL_HARD_CAP == 12
    assert AgentCreate(name="x", system_prompt="hi").max_tool_calls == 6
    assert AgentPatch(max_tool_calls=12).max_tool_calls == 12
    try:
        AgentPatch(max_tool_calls=13)
    except Exception:  # noqa: BLE001 — pydantic ValidationError
        pass
    else:
        raise AssertionError("max_tool_calls=13 should be rejected")


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


def test_error_classification_and_no_provider(client, monkeypatch):
    missing = RuntimeError("missing_key")
    assert "no API key" in agent.classify_error(missing)
    rate = RuntimeError("rate limit exceeded")
    rate.status_code = 429
    assert "rate limited" in agent.classify_error(rate)
    assert agent.is_retryable(rate)
    timeout = TimeoutError("timeout")
    assert agent.is_retryable(timeout)
    assert "timed out" in agent.classify_error(timeout)

    async def no_ready():
        return False

    monkeypatch.setattr(ai_resolver, "any_provider_ready", no_ready)
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

    async def fake_groq_key():
        return "test-key"

    async def no_or_key():
        return None

    async def fake_attempts(_model):
        return [(object(), DEFAULT_GROQ_MODEL, "groq")]

    async def yes_ready():
        return True

    monkeypatch.setattr(ai_resolver, "any_provider_ready", yes_ready)
    monkeypatch.setattr(ai_resolver, "iter_openai_compatible_attempts", fake_attempts)
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


def test_tool_call_caps(monkeypatch):
    """Batched calls stop at the cap; a capped round still closes with a
    no-tools summary round."""
    executed = []

    async def fake_batch(*_args, **_kwargs):
        return "", [
            {"id": "1", "name": "read", "arguments": "{}"},
            {"id": "2", "name": "read", "arguments": "{}"},
        ], None

    async def fake_tool(*_args, **_kwargs):
        executed.append(True)
        return "ok"

    monkeypatch.setattr(agent, "_complete_stream", fake_batch)
    monkeypatch.setattr(agent, "_execute_tool", fake_tool)
    row = {"name": "swarm", "max_tool_calls": 1}
    capped = asyncio.run(agent._run_with_client(
        object(), "model", row, "general", [], use_tools=True,
        allowed=["read"], on_tools_ready=None, on_stream_start=None,
        on_token=None, trace=None,
    ))
    assert len(executed) == 1
    assert len(capped["tool_events"]) == 1
    assert "tool-call cap" in capped["reply"]

    calls = []

    async def fake_rounds(client, model, messages, on_start=None, on_token=None, tool_schemas=None):
        calls.append(bool(tool_schemas))
        if len(calls) <= 2:
            # Work round, then a capped round that still wants tools.
            return "", [{"id": "1", "name": "read", "arguments": "{}"}], None
        return "did the thing, one step left", [], None

    monkeypatch.setattr(agent, "_complete_stream", fake_rounds)
    closed = asyncio.run(agent._run_with_client(
        object(), "model", row, "general", [], use_tools=True,
        allowed=["read"], on_tools_ready=None, on_stream_start=None,
        on_token=None, trace=None,
    ))
    assert len(closed["tool_events"]) == 1
    assert closed["reply"] == "did the thing, one step left"
    assert len(calls) == 3  # work round, capped round, no-tools closing round
