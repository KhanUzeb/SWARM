"""Behavior tests for the memory budget in the *real* agent prompt.

The context package is an intermediate result, not the product. What matters
is the system message actually handed to the provider. These tests drive
``generate_reply`` through a fake provider and assert on the captured
``messages`` argument, so a trim that stays inside ``build_context`` but never
reaches the prompt cannot pass. All offline and deterministic (no model calls).
"""
from __future__ import annotations

import asyncio

import backend.agent as agent_mod
import backend.db as db


def _run(coro):
    return asyncio.run(coro)


def _agent_row() -> dict:
    # tools=[] -> no tool round, so one completion and one captured prompt.
    return {
        "name": "swarm",
        "system_prompt": "sys",
        "tools": "[]",
        "history_window": 12,
        "max_tool_calls": 6,
    }


def _drive(monkeypatch, history: list[dict], *, channel_id: str = "general"):
    """Run one generate_reply against a fake provider; return (messages, result)."""
    import backend.ai_support.resolver as resolver

    captured: dict[str, list] = {}

    async def fake_complete(client, model, messages, on_stream_start, on_token,
                            *, tool_schemas=None):
        captured["messages"] = [dict(m) for m in messages]
        if on_stream_start is not None:
            await on_stream_start()
        if on_token is not None:
            await on_token("done")
        return "done", [], None

    async def fake_attempts(model):
        return [(object(), "test-model", "fake")]

    async def fake_ready():
        return True

    monkeypatch.setattr(agent_mod, "demo_mode_enabled", lambda: False)
    monkeypatch.setattr(agent_mod, "_complete_stream", fake_complete)
    # generate_reply re-imports these names at call time, so patching the
    # resolver module before the call is enough.
    monkeypatch.setattr(resolver, "iter_provider_attempts", fake_attempts)
    monkeypatch.setattr(resolver, "any_provider_ready", fake_ready)

    result = _run(agent_mod.generate_reply(
        _agent_row(), channel_id, history, model_override="test-model",
    ))
    return captured["messages"], result


def _history(body: str = "ping") -> list[dict]:
    return [{"author_kind": "human", "author": "u", "body": body}]


def _system(messages: list[dict]) -> str:
    return messages[0]["content"]


# ---------------------------------------------------------------------- T1
def test_get_context_memories_called_once_per_turn(monkeypatch):
    """One reply turn must load the injection window exactly once."""
    calls = {"n": 0}
    original = db.get_context_memories

    async def counting(name, channel_id, limit=12):
        calls["n"] += 1
        return await original(name, channel_id, limit)

    monkeypatch.setattr(db, "get_context_memories", counting)
    _, result = _drive(monkeypatch, _history())
    assert calls["n"] == 1
    assert result["reply"] == "done"


# ---------------------------------------------------------------------- T2
def test_over_budget_notes_absent_from_prompt(monkeypatch):
    """A note build_context trims must not reach the model's system message."""
    async def seed():
        # 12 notes of ~1307 chars each exceed the 12_000-char budget, so the
        # oldest ones are trimmed by build_context's note-trim step.
        for i in range(12):
            await db.add_memory("swarm", f"NOTE_{i}_" + "x" * 1300,
                                channel_id="general")

    _run(seed())
    messages, result = _drive(monkeypatch, _history())
    system = _system(messages)
    assert "Known notes:" in system
    # Oldest note is trimmed away; newest note survives in the real prompt.
    assert "NOTE_0_" not in system
    assert "NOTE_11_" in system
    # The trim is also what the package stats report, not a hidden extra read.
    assert result["context"]["memory_notes"] < 12


# ---------------------------------------------------------------------- T3
def test_channel_kb_still_boosted_and_trimmed(monkeypatch):
    """Channel KB hits keep reaching the prompt after the memory fix."""
    import backend.knowledge as kb_mod

    async def seed():
        await kb_mod.init_db()
        await kb_mod.create_doc(
            "agent:swarm", "Deploy runbook", "deploy checklist for general channel",
            channel_id="general", source="agent")
        await kb_mod.create_doc(
            "agent:swarm", "Deploy notes", "deploy checklist global fallback",
            source="agent")

    _run(seed())
    history = _history("deploy checklist?")
    messages, result = _drive(monkeypatch, history)
    system = _system(messages)
    assert "Knowledge base (use when relevant):" in system
    assert "deploy checklist for general channel" in system
    assert result["context"]["kb_hits"] >= 1


# ---------------------------------------------------------------------- T4
def test_global_note_semantics_unchanged(monkeypatch):
    """Global notes stay cross-channel; default remember stays channel-scoped."""
    async def seed():
        from backend.tools.registry import get_registry

        registry = get_registry()

        async def remember(body, *, scope=None):
            args = {"body": body}
            if scope is not None:
                args["scope"] = scope
            return await registry.execute(
                "remember", args, agent_name="swarm", channel_id="general",
                allowed=["remember"], sandbox_dir="", shell_runner=lambda cmd: "",
                workspace_helpers={},
            )

        assert "globally" in await remember("GLOBAL_MARKER all channels", scope="global")
        assert "in #general" in await remember("CHANNEL_MARKER only general")

    _run(seed())
    # Read from a different channel: the explicit global note is still visible
    # and labelled, while the default-scoped note does not leak across channels.
    messages, _ = _drive(monkeypatch, _history(), channel_id="other-room")
    system = _system(messages)
    assert "(global) GLOBAL_MARKER all channels" in system
    assert "CHANNEL_MARKER only general" not in system

    async def check_scope():
        notes, _ = await db.get_context_memories("swarm", "general", 12)
        channel_note = next(n for n in notes if n["body"] == "CHANNEL_MARKER only general")
        assert channel_note["channel_id"] == "general"

    _run(check_scope())


# ---------------------------------------------------------------------- T5
def test_existing_memory_rows_still_load():
    """Legacy rows with NULL and non-NULL channel_id are both still loaded."""
    async def scenario():
        await db.add_memory("swarm", "legacy global row", channel_id=None)
        await db.add_memory("swarm", "legacy channel row", channel_id="general")
        notes, summary = await db.get_context_memories("swarm", "general", 12)
        bodies = [n["body"] for n in notes]
        assert "legacy global row" in bodies
        assert "legacy channel row" in bodies
        assert all(n["agent_name"] == "swarm" for n in notes)
        assert summary is None

    _run(scenario())
