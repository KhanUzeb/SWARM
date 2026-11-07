"""Mocked OpenAI SDK surface tests: ``_complete_stream``, the compat client, Langfuse no-op.

All tests are deterministic and offline: no network, no real API keys. The
streaming path is exercised with in-memory fake clients (same chunk shapes
the SDK yields) plus one wire-level test through the real ``AsyncOpenAI``
with ``httpx.MockTransport``. The compat client is constructed with a faked
``AsyncOpenAI`` so construction asserts kwargs without hitting a network.
Langfuse is exercised only on its no-op / best-effort path.
"""
from __future__ import annotations

import asyncio
import json

import pytest

import backend.agent as agent_mod


def _run(coro):
    return asyncio.run(coro)


# ------------------------------------------------------------ fake SDK chunks --
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
    def __init__(self, delta, usage=None):
        self.choices = [_FakeChoice(delta)]
        self.usage = usage


class _FakeStream:
    def __init__(self, chunks):
        self._chunks = chunks

    def __aiter__(self):
        async def gen():
            for c in self._chunks:
                yield c

        return gen()


class _FakeClient:
    """Minimal ``client.chat.completions.create`` stand-in; records kwargs."""

    def __init__(self, chunks, base_url=""):
        self.chat = self
        self.completions = self
        self._chunks = chunks
        self.base_url = base_url
        self.kwargs = None

    async def create(self, **kwargs):
        self.kwargs = kwargs
        if isinstance(self._chunks, BaseException):
            raise self._chunks
        return _FakeStream(self._chunks)


# ----------------------------------------------------------- _complete_stream --

def test_stream_accumulates_text_and_forwards_tokens():
    seen: list[str] = []

    async def on_token(delta):
        seen.append(delta)

    client = _FakeClient([
        _FakeChunk(_FakeDelta(content="hello ")),
        _FakeChunk(_FakeDelta(content="world")),
    ])
    content, tool_calls, _usage = _run(
        agent_mod._complete_stream(client, "some-model", [{"role": "user", "content": "hi"}], None, on_token)
    )
    assert content == "hello world"
    assert tool_calls == []
    assert seen == ["hello ", "world"]
    # Wire kwargs: streaming on, default model branch.
    assert client.kwargs["stream"] is True
    assert client.kwargs["max_tokens"] == 600
    assert "tools" not in (client.kwargs or {})
    assert client.kwargs["model"] == "some-model"


def test_stream_assembles_fragmented_tool_call_across_chunks():
    client = _FakeClient([
        _FakeChunk(_FakeDelta(tool_calls=[_FakeTC(0, "call_9", "read", '{"pa')])),
        _FakeChunk(_FakeDelta(tool_calls=[_FakeTC(0, None, None, 'th":"x"')])),
        _FakeChunk(_FakeDelta(tool_calls=[_FakeTC(0, None, None, "}")])),
    ])
    content, tool_calls, _usage = _run(
        agent_mod._complete_stream(client, "m", [], None, None)
    )
    assert content == ""
    assert tool_calls == [{"id": "call_9", "name": "read", "arguments": '{"path":"x"}'}]


def test_stream_propagates_usage_and_ignores_empty_chunks():
    usage = object()

    class _Usage:
        prompt_tokens = 7
        completion_tokens = 5

    u = _Usage()
    client = _FakeClient([
        _FakeChunk(_FakeDelta(content=""), usage=None),
        _FakeChunk(_FakeDelta(content="done"), usage=u),
    ])
    # Chunk with no choices still counts for usage? Empty choices skipped; usage seized first.
    content, tool_calls, got_usage = _run(
        agent_mod._complete_stream(client, "m", [], None, None)
    )
    assert content == "done"
    assert got_usage is u


def test_stream_cutoff_returns_partial_content_when_no_tools():
    async def on_token(delta):
        pass

    class _Boom(BaseException):
        pass

    # Simulate a stream that yields partial text then raises mid-iteration.
    class _DyingStream:
        def __aiter__(self):
            async def gen():
                yield _FakeChunk(_FakeDelta(content="partial answer"))
                raise RuntimeError("connection reset")

            return gen()

    class _DyingClient:
        base_url = ""
        chat = None
        completions = None

        async def create(self, **_kw):
            return _DyingStream()

    client = _DyingClient()
    client.chat = client
    client.completions = client
    content, tool_calls, _usage = _run(
        agent_mod._complete_stream(client, "m", [], None, on_token)
    )
    assert content == "partial answer" + agent_mod.CUTOFF_NOTE
    assert tool_calls == []


def test_stream_cutoff_reraises_mid_stream_error_when_tools_present():
    class _DyingStream:
        def __aiter__(self):
            async def gen():
                yield _FakeChunk(_FakeDelta(tool_calls=[_FakeTC(0, "c1", "read", "{}")]))
                raise RuntimeError("connection reset")

            return gen()

    class _DyingClient:
        base_url = ""
        chat = None
        completions = None

        async def create(self, **_kw):
            return _DyingStream()

    client = _DyingClient()
    client.chat = client
    client.completions = client
    with pytest.raises(RuntimeError, match="connection reset"):
        _run(agent_mod._complete_stream(client, "m", [], None, None))


def test_tool_schemas_are_forwarded_as_tools_plus_auto_choice():
    client = _FakeClient([_FakeChunk(_FakeDelta(content="ok"))])
    schemas = [{"type": "function", "function": {"name": "read"}}]
    _run(agent_mod._complete_stream(client, "m", [], None, None, tool_schemas=schemas))
    assert client.kwargs["tools"] == schemas
    assert client.kwargs["tool_choice"] == "auto"


def test_gpt_oss_model_branch_uses_reasoning_budget_without_openrouter():
    client = _FakeClient([_FakeChunk(_FakeDelta(content="ok"))], base_url="https://api.groq.com/openai/v1/")
    _run(agent_mod._complete_stream(client, "openai/gpt-oss-120b", [], None, None))
    assert client.kwargs["max_tokens"] == 2048
    assert client.kwargs.get("reasoning_effort") == "low"


def test_gpt_oss_via_openrouter_omits_reasoning_effort():
    client = _FakeClient([_FakeChunk(_FakeDelta(content="ok"))], base_url="https://openrouter.ai/api/v1")
    _run(agent_mod._complete_stream(client, "openai/gpt-oss-120b", [], None, None))
    assert client.kwargs["max_tokens"] == 2048
    assert "reasoning_effort" not in client.kwargs


def test_real_async_openai_client_over_mock_transport_text_only():
    """Wire-level: the real SDK POSTs streaming chat completions and we parse SSE."""
    import httpx
    from openai import AsyncOpenAI

    seen: list[dict] = []

    async def respond(request):
        seen.append(json.loads((await request.aread()).decode()))
        chunk = {
            "id": "chatcmpl-test",
            "object": "chat.completion.chunk",
            "created": 0,
            "model": "some-model",
            "choices": [{"index": 0, "delta": {"content": "hi there"}, "finish_reason": "stop"}],
        }
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            content=f"data: {json.dumps(chunk)}\n\ndata: [DONE]\n\n",
        )

    async def exercise():
        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as http:
            client = AsyncOpenAI(api_key="test-key", base_url="http://local.test/v1", http_client=http)
            try:
                return await agent_mod._complete_stream(
                    client, "some-model", [{"role": "user", "content": "work"}], None, None,
                )
            finally:
                await client.close()

    content, tool_calls, _usage = _run(exercise())
    assert content == "hi there"
    assert tool_calls == []
    assert len(seen) == 1
    assert seen[0]["stream"] is True
    assert seen[0]["model"] == "some-model"


def test_real_async_openai_client_over_mock_transport_tool_call():
    import httpx
    from openai import AsyncOpenAI

    seen: list[dict] = []

    async def respond(request):
        seen.append(json.loads((await request.aread()).decode()))
        chunk = {
            "id": "chatcmpl-test",
            "object": "chat.completion.chunk",
            "created": 0,
            "model": "some-model",
            "choices": [{
                "index": 0,
                "delta": {
                    "content": None,
                    "tool_calls": [{
                        "index": 0,
                        "id": "call_wire",
                        "type": "function",
                        "function": {"name": "read", "arguments": '{"path":"a.txt"}'},
                    }],
                },
                "finish_reason": "tool_calls",
            }],
        }
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            content=f"data: {json.dumps(chunk)}\n\ndata: [DONE]\n\n",
        )

    async def exercise():
        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as http:
            client = AsyncOpenAI(api_key="test-key", base_url="http://local.test/v1", http_client=http)
            try:
                schemas = [{"type": "function", "function": {"name": "read", "parameters": {"type": "object"}}}]
                return await agent_mod._complete_stream(
                    client, "some-model", [{"role": "user", "content": "work"}], None, None,
                    tool_schemas=schemas,
                )
            finally:
                await client.close()

    content, tool_calls, _usage = _run(exercise())
    assert tool_calls == [{"id": "call_wire", "name": "read", "arguments": '{"path":"a.txt"}'}]
    assert seen[0]["tools"] and seen[0]["tool_choice"] == "auto"


# ------------------------------------------------------- compat client build ----

def test_build_compat_client_forwards_timeout_retries_base_and_headers(monkeypatch):
    import backend.ai_support.resolver as resolver
    from backend.ai_support.config import OpenAICompatibleConfig

    seen: dict = {}

    class _FakeOpenAI:
        def __init__(self, **kwargs):
            seen.update(kwargs)

    monkeypatch.setattr("openai.AsyncOpenAI", _FakeOpenAI)
    config = OpenAICompatibleConfig(
        provider_id="groq",
        api_key="sk-test-not-a-real-key",
        base_url="https://api.groq.com/openai/v1/",
        headers={"X-Test": "1"},
        max_retries=3,
    )
    client = resolver.build_openai_compatible_client(config)
    assert isinstance(client, _FakeOpenAI)
    assert seen["api_key"] == "sk-test-not-a-real-key"  # never returned by any API; only passed to SDK
    assert seen["base_url"] == "https://api.groq.com/openai/v1"  # trailing slash normalized
    assert seen["default_headers"] == {"X-Test": "1"}
    assert seen["timeout"] > 0
    assert seen["max_retries"] == 3


def test_build_compat_client_omits_base_and_headers_when_absent(monkeypatch):
    import backend.ai_support.resolver as resolver
    from backend.ai_support.config import (
        DEFAULT_OPENAI_COMPATIBLE_MAX_RETRIES,
        DEFAULT_OPENAI_COMPATIBLE_TIMEOUT_SECONDS,
        OpenAICompatibleConfig,
    )

    seen: dict = {}

    class _FakeOpenAI:
        def __init__(self, **kwargs):
            seen.update(kwargs)

    monkeypatch.setattr("openai.AsyncOpenAI", _FakeOpenAI)
    config = OpenAICompatibleConfig(provider_id="openai", api_key="k12345678", base_url="", headers=None)
    resolver.build_openai_compatible_client(config)
    assert "base_url" not in seen
    assert "default_headers" not in seen
    assert seen["timeout"] == DEFAULT_OPENAI_COMPATIBLE_TIMEOUT_SECONDS
    assert seen["max_retries"] == DEFAULT_OPENAI_COMPATIBLE_MAX_RETRIES


def test_compat_config_reads_custom_base_from_settings(monkeypatch):
    """AGENTS.md section 2: the generic BYO base URL comes from Settings, not raw env."""
    import backend.ai_support.resolver as resolver
    from backend.ai_support.config import RuntimeProviderAuth
    from backend.settings import Settings

    monkeypatch.setattr(
        resolver, "get_settings",
        lambda: Settings(openai_compat_base_url="http://settings-object-wins/v1"),
    )
    monkeypatch.setenv("SWARM_OPENAI_COMPAT_BASE_URL", "http://env-should-not-win/v1")
    auth = RuntimeProviderAuth(
        provider_id="custom", api_key="k12345678",
        base_url="http://127.0.0.1:9/v1", headers=None, default_model="m",
    )
    config = resolver.openai_compatible_config(auth)
    assert config.base_url == "http://settings-object-wins/v1"


# ------------------------------------------------------------- Langfuse no-op --

def test_langfuse_client_is_none_without_keys(monkeypatch):
    monkeypatch.delenv("LANGFUSE_PUBLIC_KEY", raising=False)
    monkeypatch.delenv("LANGFUSE_SECRET_KEY", raising=False)
    assert agent_mod._langfuse_client() is None


def test_langfuse_client_is_none_when_only_one_key_set(monkeypatch):
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk-test")
    monkeypatch.delenv("LANGFUSE_SECRET_KEY", raising=False)
    assert agent_mod._langfuse_client() is None
    monkeypatch.delenv("LANGFUSE_PUBLIC_KEY", raising=False)
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk-test")
    assert agent_mod._langfuse_client() is None


def test_langfuse_client_is_none_when_sdk_raises(monkeypatch):
    import sys
    import types

    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk-test")
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk-test")

    class _Langfuse:
        def __init__(self):
            raise RuntimeError("init failed")

    fake_pkg = types.ModuleType("langfuse")
    fake_pkg.Langfuse = _Langfuse
    monkeypatch.setitem(sys.modules, "langfuse", fake_pkg)
    assert agent_mod._langfuse_client() is None


def test_langfuse_client_returns_instance_when_configured(monkeypatch):
    import sys
    import types

    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk-test")
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk-test")

    class _Langfuse:
        def __init__(self):
            pass

    fake_pkg = types.ModuleType("langfuse")
    fake_pkg.Langfuse = _Langfuse
    monkeypatch.setitem(sys.modules, "langfuse", fake_pkg)
    client = agent_mod._langfuse_client()
    assert isinstance(client, _Langfuse)


def test_trace_generation_failure_does_not_break_the_turn(monkeypatch):
    """Tracing is best-effort: a raising trace must not fail the reply."""

    class _BadTrace:
        def generation(self, *args, **kwargs):
            raise RuntimeError("tracing backend down")

    async def fake_complete(*_a, **_kw):
        return "hello", [], None

    async def fake_tool(*_a, **_kw):
        return "ok"

    monkeypatch.setattr(agent_mod, "_complete_stream", fake_complete)
    monkeypatch.setattr(agent_mod, "_execute_tool", fake_tool)
    result = _run(agent_mod._run_with_client(
        object(), "model", {"name": "swarm"}, "general", [],
        use_tools=False, allowed=[], on_tools_ready=None,
        on_stream_start=None, on_token=None, trace=_BadTrace(),
    ))
    assert result["reply"] == "hello"
