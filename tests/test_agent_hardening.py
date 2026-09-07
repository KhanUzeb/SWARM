"""Hardening coverage for the agent tool-call loop and tool registry.

Covers: synthesized tool_call_ids, narration preserved alongside tool
calls, malformed tool arguments short-circuiting, safe template/int
parsing in custom + plugin handlers, and safe numeric coercion in
search connectors.
"""
from __future__ import annotations

import asyncio

import backend.agent as agent
from backend.tools import connectors
from backend.tools.registry import get_registry


class _FakeFn:
    def __init__(self, name="", arguments=""):
        self.name = name
        self.arguments = arguments


class _FakeTC:
    def __init__(self, index=0, id=None, name="", arguments=""):
        self.index = index
        self.id = id
        self.function = _FakeFn(name, arguments)


class _FakeDelta:
    def __init__(self, content=None, tool_calls=None):
        self.content = content
        self.tool_calls = tool_calls


class _FakeChoice:
    def __init__(self, delta):
        self.delta = delta


class _FakeChunk:
    def __init__(self, delta):
        self.choices = [_FakeChoice(delta)]
        self.usage = None


class _FakeStream:
    def __init__(self, chunks):
        self._chunks = chunks

    def __aiter__(self):
        async def gen():
            for c in self._chunks:
                yield c

        return gen()


class _FakeClient:
    def __init__(self, chunks):
        self.chat = self
        self.completions = self
        self._chunks = chunks
        self.kwargs = None

    async def create(self, **kwargs):
        self.kwargs = kwargs
        return _FakeStream(self._chunks)


def _run(coro):
    return asyncio.run(coro)


def test_missing_tool_call_ids_are_synthesized_unique():
    chunks = [
        _FakeChunk(_FakeDelta(tool_calls=[
            _FakeTC(index=0, id=None, name="read", arguments="{}"),
            _FakeTC(index=1, id="", name="read", arguments="{}"),
        ])),
    ]
    content, tool_calls, _usage = _run(
        agent._complete_stream(_FakeClient(chunks), "model", [], None, None))
    assert content == ""
    assert len(tool_calls) == 2
    ids = [tc["id"] for tc in tool_calls]
    assert all(ids)
    assert len(set(ids)) == 2


def test_narration_preserved_alongside_tool_calls():
    seen = []

    async def collect(token):
        seen.append(token)

    chunks = [
        _FakeChunk(_FakeDelta(content="working on it ")),
        _FakeChunk(_FakeDelta(tool_calls=[
            _FakeTC(index=0, id="call_1", name="read", arguments="{}"),
        ])),
        _FakeChunk(_FakeDelta(content="almost done")),
    ]
    content, tool_calls, _usage = _run(agent._complete_stream(
        _FakeClient(chunks), "model", [], None, collect))
    assert tool_calls and tool_calls[0]["id"] == "call_1"
    assert content == "working on it almost done"
    # Pre-tool narration streams live; narration after the first tool call
    # is kept for the message but not streamed.
    assert seen == ["working on it "]


def test_malformed_tool_arguments_do_not_execute(monkeypatch):
    executed = []

    calls = {"n": 0}

    async def fake_complete(*_args, **_kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            return "", [{"id": "1", "name": "read", "arguments": "{oops"}], None
        return "done", [], None

    async def fake_tool(*_args, **_kwargs):
        executed.append(True)
        return "ok"

    monkeypatch.setattr(agent, "_complete_stream", fake_complete)
    monkeypatch.setattr(agent, "_execute_tool", fake_tool)
    messages: list = []
    result = _run(agent._run_with_client(
        object(), "model", {"name": "swarm"}, "general", messages,
        use_tools=True, allowed=["read"], on_tools_ready=None,
        on_stream_start=None, on_token=None, trace=None,
    ))
    assert executed == []
    assert result["reply"] == "done"
    assert len(result["tool_events"]) == 1
    assert "malformed" in result["tool_events"][0]["result"]
    # The follow-up tool message still carries a valid tool_call_id.
    assert messages[-1] == {
        "role": "tool", "tool_call_id": "1",
        "content": result["tool_events"][0]["result"],
    }


def test_custom_http_get_bad_template_returns_error_not_raise():
    reg = get_registry()
    row = {
        "handler_type": "http_get",
        "handler_config": {"url": "https://x.example/{missing}"},
        "description": "t",
    }
    out = _run(reg._exec_custom(row, {"other": "1"}))
    assert out.startswith("(template error:")


def test_plugin_http_get_and_shell_bad_template_return_error():
    reg = get_registry()
    base = {"plugin_id": "p", "plugin_dir": "", "tool_name": "t"}
    out = _run(reg._exec_plugin(
        {**base, "handler": {"type": "http_get", "url": "https://x.example/{missing}"}},
        {"other": "1"}, agent_name="swarm", channel_id="general"))
    assert out.startswith("(plugin template error:")
    out = _run(reg._exec_plugin(
        {**base, "handler": {"type": "shell", "command": "echo {missing}"}},
        {"other": "1"}, agent_name="swarm", channel_id="general"))
    assert out.startswith("(plugin template error:")


def test_channel_digest_bad_limit_does_not_raise(client):
    reg = get_registry()
    out = _run(reg.execute(
        "channel_digest", {"limit": "not-a-number"},
        agent_name="swarm", channel_id="general", allowed=["channel_digest"],
        sandbox_dir="", shell_runner=None, workspace_helpers={}))
    assert out == "(no messages)"


def test_search_connectors_coerce_bad_counts(monkeypatch):
    async def fake_key(_name):
        return "k"

    def fake_http(*_args, **_kwargs):
        return {"results": [], "answer": None}

    monkeypatch.setattr(connectors, "_key", fake_key)
    monkeypatch.setattr(connectors, "_http_json", fake_http)
    assert _run(connectors.exa_search("hello world", num_results="five")) == "(no exa results)"
    assert _run(connectors.tavily_search("hello world", max_results="five")) == "(no tavily results)"
