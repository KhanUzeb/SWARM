"""Lane-scoped regression tests for AGENT RUNTIME + PROVIDERS + TOOLS (worker AG).

Every test pins one AGENTS.md invariant at the seam where the lane owns it.
All tests are deterministic and offline: providers mocked, no network, no
real keys, temp filesystem only.
"""
from __future__ import annotations

import asyncio
import os
import re

import backend.agent as agent_mod
from backend.ai_support.config import RuntimeProviderAuth


def _run(coro):
    return asyncio.run(coro)


# ------------------------------------------------------------------ mentions
# AGENTS.md s6: triggers are whole-word case-insensitive @mentions. An email
# address (foo@bar.com) is not a mention of @bar.

def _agents(*names):
    return [{"name": n, "system_prompt": "", "model": "x"} for n in names]


def test_mention_ignores_email_address():
    agents = _agents("swarm", "bar")
    assert agent_mod.find_mentioned_agents("contact foo@bar.com thanks", agents) == []
    assert agent_mod.find_mentioned_agents("(see foo@bar.com)", agents) == []
    # Real mentions still hit: whole-word, case-insensitive, in mention order.
    hits = agent_mod.find_mentioned_agents("hey @swarm then @BAR", agents)
    assert [a["name"] for a in hits] == ["swarm", "bar"]
    assert agent_mod.find_mentioned_agents("see @swarmy please", agents) == []


def test_team_mention_ignores_email_address():
    teams = [{"id": "bar", "members": []}]
    assert agent_mod.find_mentioned_teams("contact foo@bar.com thanks", teams) == []
    hits = agent_mod.find_mentioned_teams("ping @BAR please", teams)
    assert [t["id"] for t in hits] == ["bar"]


# ------------------------------------------------------------------ wording
# AGENTS.md s5: the sandbox is cwd + timeout, NOT container/network isolation.
# Never claim isolation that does not exist.

def test_no_isolation_claims_in_lane_copy():
    from backend.tools.registry import BUILTIN_SCHEMAS
    from backend import computer_providers

    assert "isolat" not in agent_mod._TOOL_POLICY.lower()
    assert "isolat" not in BUILTIN_SCHEMAS["computer_run"]["function"]["description"].lower()
    assert "isolat" not in BUILTIN_SCHEMAS["system_run"]["function"]["description"].lower()
    assert "isolat" not in computer_providers.status()["note"].lower()
    assert "isolat" not in (computer_providers.private_home.__doc__ or "").lower()


# ------------------------------------------------------- host secret hygiene
# AGENTS.md s4: secrets are never returned by any API. The same bar holds for
# tool output: tool results are persisted to the channel audit, so a tool
# that prints the host environment or reads .env leaks secrets in-channel.

def test_system_read_blocks_protected_files(tmp_path, monkeypatch):
    from backend.tools import system as system_mod

    monkeypatch.setenv("SWARM_SYSTEM", "1")
    monkeypatch.setenv("SWARM_SYSTEM_ROOT", str(tmp_path))
    (tmp_path / ".env").write_text("GROQ_API_KEY=fake-marker-123\n", encoding="utf-8")
    out = system_mod.system_read(".env")
    assert "fake-marker-123" not in out
    assert "block" in out.lower() or "protect" in out.lower()


def test_system_run_blocks_env_file_reads(tmp_path, monkeypatch):
    from backend.tools import system as system_mod

    monkeypatch.setenv("SWARM_SYSTEM", "1")
    monkeypatch.setenv("SWARM_SYSTEM_ROOT", str(tmp_path))
    out = system_mod.system_run("cat .env")
    assert "blocked" in out.lower()
    out = system_mod.system_run("type .env")
    assert "blocked" in out.lower()


def test_system_run_does_not_leak_host_env(tmp_path, monkeypatch):
    from backend.tools import system as system_mod

    monkeypatch.setenv("SWARM_SYSTEM", "1")
    monkeypatch.setenv("SWARM_SYSTEM_ROOT", str(tmp_path))
    monkeypatch.setenv("SWARM_LANE_PROBE_SECRET", "probe-marker-xyz-123")
    cmd = "set" if os.name == "nt" else "env"
    out = system_mod.system_run(f"{cmd}")
    assert "probe-marker-xyz-123" not in out


def test_read_only_shell_is_guard_railed(monkeypatch, tmp_path):
    """read_only_shell must refuse destructive commands without running them."""
    from backend.tools import guard
    from backend.tools.registry import get_registry

    assert guard.is_dangerous("rm -rf /") is not None
    calls = []

    def fake_runner(cmd):
        calls.append(cmd)
        return "SHOULD-NOT-RUN"

    reg = get_registry()
    out = _run(reg._exec_builtin(
        "read_only_shell", {"command": "rm -rf /"},
        agent_name="t", channel_id="c", sandbox_dir=str(tmp_path),
        shell_runner=fake_runner, workspace_helpers={},
    ))
    assert calls == []
    assert "blocked" in out.lower()


# ------------------------------------------------------------- loop auditing
# AGENTS.md s6: every tool call persists as a system audit message. The call
# that proves a loop is still a call — dropping it hides the evidence.

def test_loop_proving_call_is_still_audited(monkeypatch):
    async def fake_complete(*_a, **_k):
        return "", [{"id": "c0", "name": "read", "arguments": "{}"}], None

    async def fake_tool(name, args, **_kw):
        return "same answer"

    monkeypatch.setattr(agent_mod, "_complete_stream", fake_complete)
    monkeypatch.setattr(agent_mod, "_execute_tool", fake_tool)
    result = _run(agent_mod._run_with_client(
        object(), "model", {"name": "swarm", "max_tool_calls": 12}, "general", [],
        use_tools=True, allowed=["read"], on_tools_ready=None,
        on_stream_start=None, on_token=None, trace=None,
    ))
    assert "identical" in result["reply"]
    assert len(result["tool_events"]) == agent_mod.LOOP_REPEAT_LIMIT
    assert all(e["result"] == "same answer" for e in result["tool_events"])


# ------------------------------------------------------------------ routine
# AGENTS.md s6: [routine:...] ticks trigger the agent. The tick body is the
# task; dropping every system message from the model context loses it.

def test_routine_helper_finds_the_tick():
    body = "[routine:nightly] check the inbox now"
    history = [
        {"author_kind": "human", "author": "u", "body": "hi"},
        {"author_kind": "system", "author": "routine", "body": body},
    ]
    assert agent_mod.routine_instruction_of(history) == body
    assert agent_mod.routine_instruction_of(
        [{"author_kind": "human", "author": "u", "body": "hi"}]) is None


def test_routine_tick_reaches_the_model():
    body = "[routine:nightly] check the inbox now"
    messages = agent_mod._build_messages(
        "sys",
        [{"author_kind": "system", "author": "routine", "body": body}],
        allowed_tools=[],
    )
    assert any("check the inbox now" in (m.get("content") or "") for m in messages)
    messages = agent_mod._build_messages("sys", [], allowed_tools=[], routine=body)
    assert "check the inbox now" in messages[0]["content"]


# ----------------------------------------------------------------- providers
# AGENTS.md s4: the catalog is priority-ordered; resolution is stored key
# first then env, once per provider per request.

# ------------------------------------------------------- no double stream start
# AGENTS.md s6: one agent_stream_start per reply, then tokens, then exactly one
# persisted message. The model-fallback path re-enters generate_reply, and the
# guard that stops a second visible stream is per-call state — so a fallback
# after partial tokens used to emit a second agent_stream_start.

def test_model_fallback_never_announces_a_second_stream(monkeypatch):
    class _ModelNotFound(Exception):
        status_code = 404

    calls = {"n": 0}
    frames: list[str] = []

    async def fake_complete(_client, _model, _messages, on_stream_start, on_token,
                            *, tool_schemas=None):
        calls["n"] += 1
        if calls["n"] == 1:
            if on_stream_start is not None:
                await on_stream_start()
            if on_token is not None:
                await on_token("partial ")
            return ("partial ",
                    [{"id": "c1", "name": "list_workspace", "arguments": "{}"}],
                    None)
        if calls["n"] == 2:
            raise _ModelNotFound("model 'x' not found")
        if on_stream_start is not None:
            await on_stream_start()
        if on_token is not None:
            await on_token("final answer")
        return "final answer", [], None

    async def fake_tool(_name, _args, **_kw):
        return "tool-result-ok"

    async def on_stream_start():
        frames.append("stream_start")

    async def on_token(delta):
        frames.append("token:" + delta)

    monkeypatch.setattr(agent_mod, "_complete_stream", fake_complete)
    monkeypatch.setattr(agent_mod, "_execute_tool", fake_tool)

    import backend.ai_support.resolver as resolver

    async def fake_attempts(_model):
        return [(object(), "some-model", "groq")]

    async def fake_ready():
        return True

    monkeypatch.setattr(resolver, "iter_provider_attempts", fake_attempts)
    monkeypatch.setattr(resolver, "any_provider_ready", fake_ready)

    result = _run(agent_mod.generate_reply(
        {"name": "swarm", "system_prompt": "s", "max_tool_calls": 6,
         "tools": "list_workspace"},
        "dm-swarm",
        [{"author_kind": "human", "author": "u", "body": "ping"}],
        on_tools_ready=None,
        on_stream_start=on_stream_start,
        on_token=on_token,
        model_override="bad-model",
    ))
    assert result["reply"] == "final answer"
    assert result["model_fallback"] is True
    # Exactly one visible stream was opened for the whole turn.
    assert frames.count("stream_start") == 1, frames


# ------------------------------------------------- delegated tool audit trail
# AGENTS.md s6: "Every tool call persists as a system audit message in-channel."
# A delegated sub-run runs headless, so without an explicit hand-off its tool
# calls vanish: the sub-agent could run a tool and leave no trace in-channel.

def test_delegated_subrun_tool_calls_are_audited(monkeypatch, client):
    """A delegated sub-run's own tool calls must reach the channel audit.

    AGENTS.md s6 persists every tool call in-channel. A sub-agent runs
    headlessly, so its tool calls used to vanish entirely: the parent's
    ``delegate_task`` was audited, the sub-agent's own calls were not.

    Deliberately end-to-end: only ``_complete_stream`` is faked, so the real
    ``_execute_tool`` → registry → ``generate_delegate_reply`` → sub-run path
    is what produces the events under test.
    """
    audited: list[dict] = []

    async def persist_tools(events):
        audited.extend(events)

    calls = {"n": 0}

    async def fake_complete(_client, _model, _messages, _oss, _ot,
                            *, tool_schemas=None):
        calls["n"] += 1
        if calls["n"] == 1:
            # the parent asks @ledger for a subtask
            return "", [{"id": "c1", "name": "delegate_task",
                         "arguments": '{"agent":"ledger","task":"log this"}'}], None
        if calls["n"] == 2:
            # the sub-agent calls a tool of its own
            return "", [{"id": "c2", "name": "read_workspace",
                         "arguments": '{"path":"missing.txt"}'}], None
        return "sub-answer", [], None

    monkeypatch.setattr(agent_mod, "_complete_stream", fake_complete)

    import backend.ai_support.resolver as resolver

    async def fake_attempts(_model):
        return [(object(), "some-model", "groq")]

    async def fake_ready():
        return True

    monkeypatch.setattr(resolver, "iter_provider_attempts", fake_attempts)
    monkeypatch.setattr(resolver, "any_provider_ready", fake_ready)

    result = _run(agent_mod.generate_reply(
        {"name": "swarm", "system_prompt": "s", "max_tool_calls": 6,
         "tools": ["delegate_task", "read_workspace"]},
        "dm-swarm",
        [{"author_kind": "human", "author": "u", "body": "ping"}],
        on_tools_ready=persist_tools,
        on_stream_start=None,
        on_token=None,
    ))
    assert result["reply"] == "sub-answer"
    tools = [e["tool"] for e in audited]
    # Both the wrapper and the sub-agent's own call are in the audit trail.
    assert "delegate_task" in tools, tools
    assert "read_workspace" in tools, tools
    # In order: the wrapper first, then what it actually ran.
    assert tools.index("delegate_task") < tools.index("read_workspace"), tools
    assert tools[-1] == "read_workspace", tools


# ------------------------------------------- audit trail must not be dropped
# AGENTS.md s6: "Every tool call persists as a system audit message in-channel."
# A model that narrates ("thinking") and then calls a tool in the SAME stream
# made the tool-audit callback fire on the first content chunk, while
# tool_events was still empty. The caller's "posted once" guard then treated
# that empty list as final, so the calls that afterwards ran were never
# audited at all.

def _narration_then_tools_client():
    class _Stream:
        def __init__(self, chunks):
            self._chunks = chunks

        def __aiter__(self):
            return self._gen()

        async def _gen(self):
            for chunk in self._chunks:
                yield chunk

    class _Delta:
        def __init__(self, content=None, tool_calls=None):
            self.content = content
            self.tool_calls = tool_calls

    class _Choice:
        def __init__(self, delta):
            self.delta = delta

    class _Chunk:
        def __init__(self, delta):
            self.choices = [_Choice(delta)]
            self.usage = None

    class _Fn:
        def __init__(self, name, arguments):
            self.name = name
            self.arguments = arguments

    class _TC:
        def __init__(self, index, tid, name, arguments):
            self.index = index
            self.id = tid
            self.function = _Fn(name, arguments)

    # Two rounds that narrate and then call a tool, then a final text round.
    _PLAN = (
        ('{"path":"p1"}', True),
        ('{"path":"p2"}', True),
        (None, False),
    )

    state = {"n": 0}

    class _Client:
        class chat:
            class completions:
                @staticmethod
                async def create(**_kw):
                    arguments, narrates = _PLAN[state["n"]]
                    state["n"] += 1
                    if not narrates:
                        return _Stream([_Chunk(_Delta(content="done"))])
                    chunks = [_Chunk(_Delta(content="thinking "))]
                    chunks.append(_Chunk(_Delta(
                        content="", tool_calls=[
                            _TC(0, f"t{state['n']}", "read_workspace", arguments)])))
                    return _Stream(chunks)

    return _Client


def test_tool_calls_after_narration_are_still_audited(monkeypatch):
    client_cls = _narration_then_tools_client()

    async def fake_tool(_name, _args, **_kw):
        return "tool result"

    monkeypatch.setattr(agent_mod, "_execute_tool", fake_tool)

    import backend.ai_support.resolver as resolver

    async def fake_attempts(_model):
        return [(client_cls(), "m", "groq")]

    async def fake_ready():
        return True

    monkeypatch.setattr(resolver, "iter_provider_attempts", fake_attempts)
    monkeypatch.setattr(resolver, "any_provider_ready", fake_ready)

    # main.py's contract: it persists the audit exactly once, on the first
    # callback, and never again.
    fired: list[list[dict]] = []

    async def on_tools_ready(events):
        fired.append(list(events))

    tokens: list[str] = []

    async def on_token(delta):
        tokens.append(delta)

    result = _run(agent_mod.generate_reply(
        {"name": "swarm", "system_prompt": "s", "max_tool_calls": 6,
         "tools": ["read_workspace"]},
        "dm-swarm",
        [{"author_kind": "human", "author": "u", "body": "ping"}],
        on_tools_ready=on_tools_ready,
        on_stream_start=None,
        on_token=on_token,
    ))
    assert result["reply"] == "done"
    # Two tool calls actually ran.
    assert [e["args"]["path"] for e in result["tool_events"]] == ["p1", "p2"]
    # And whatever the channel persisted as audit must contain them: an empty
    # first fire is exactly the bug, because the caller posts only once.
    assert fired, "the tool-audit callback never fired"
    audited = [e for payload in fired for e in payload]
    assert [e["args"]["path"] for e in audited] == ["p1", "p2"], audited


# --------------------------------------------------- tool args are unvalidated
# AGENTS.md s6: "failures become [agent error: ...] messages, never a raw
# exception and never a 500." The agent loop catches a raising tool and posts
# "[agent error: ...]" — but that ENDS the turn with a generic error instead of
# the answer. A schema declares `"type": "string"`, and the model may still
# send a number; the tool must not blow up on it.

def test_path_arg_as_a_number_does_not_kill_the_turn(monkeypatch, tmp_path):
    monkeypatch.setenv("SWARM_SYSTEM", "1")
    monkeypatch.setenv("SWARM_SYSTEM_ROOT", str(tmp_path))
    from backend.tools import system as system_mod

    out = system_mod.system_read(123)
    assert "Traceback" not in out
    assert "AttributeError" not in out
    assert "invalid" in out.lower() or "need" in out.lower()


def test_command_arg_as_a_number_does_not_kill_the_turn(monkeypatch, tmp_path):
    monkeypatch.setenv("SWARM_SYSTEM", "1")
    monkeypatch.setenv("SWARM_SYSTEM_ROOT", str(tmp_path))
    from backend.tools import system as system_mod

    out = system_mod.system_run(123)
    assert "Traceback" not in out
    assert "AttributeError" not in out
    assert "invalid" in out.lower() or "need" in out.lower()


def test_guard_tolerates_non_string_commands():
    from backend.tools import guard

    # is_dangerous is called on the raw arg before any coercion.
    assert guard.is_dangerous(123) is None or isinstance(guard.is_dangerous(123), str)


def test_workspace_path_arg_as_a_number_does_not_kill_the_turn(monkeypatch, tmp_path):
    import backend.agent as agent_mod
    from backend.tools.registry import get_registry

    reg = get_registry()
    sandbox = tmp_path / "sandbox"
    sandbox.mkdir()
    monkeypatch.setattr(agent_mod, "SANDBOX_DIR", str(sandbox))
    out = _run(reg.execute(
        "read_workspace", {"path": 123},
        agent_name="swarm", channel_id="general", allowed=["read_workspace"],
        sandbox_dir=str(sandbox),
        shell_runner=agent_mod._run_shell_tool,
        workspace_helpers={
            "read": agent_mod._run_read_workspace,
            "write": agent_mod._run_write_workspace,
            "list": agent_mod._run_list_workspace,
            "save_skill": agent_mod._run_save_skill,
            "approval": agent_mod._run_approval_tool,
        },
    ))
    assert isinstance(out, str)
    assert "AttributeError" not in out


# ------------------------------------------------------- settings ownership
# AGENTS.md s2: "Every SWARM_* variable is read through backend/settings.py,
# and every boolean through truthy() — on means on for all of them."
# A raw os.environ.get in a lane module is a second parse surface: the value
# the operator set can be reported one way by `swarm doctor` and used another
# way by the code.

def _lane_source_files():
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent
    return [
        root / "backend" / "agent.py",
        root / "backend" / "routing.py",
        root / "backend" / "policy.py",
        root / "backend" / "computer_providers.py",
        root / "backend" / "bundled_skills.py",
        root / "backend" / "ai_support" / "resolver.py",
        root / "backend" / "ai_support" / "store.py",
        root / "backend" / "ai_support" / "catalog.py",
        root / "backend" / "tools" / "registry.py",
        root / "backend" / "tools" / "system.py",
        root / "backend" / "tools" / "guard.py",
        root / "backend" / "tools" / "browser.py",
        root / "backend" / "tools" / "computer.py",
        root / "backend" / "tools" / "connectors.py",
        root / "backend" / "tools" / "composio_client.py",
    ]


_RAW_SWARM_READ = re.compile(
    r"""os\.environ\.get\(\s*['"]SWARM_[A-Z0-9_]+['"]|"""
    r"""os\.environ\[\s*['"]SWARM_[A-Z0-9_]+['"]|"""
    r"""os\.getenv\(\s*['"]SWARM_[A-Z0-9_]+['"]"""
)


def test_lane_reads_no_swarm_flag_straight_from_the_environment():
    """One parse surface: no lane module does its own SWARM_* env read.

    Each offender is a value the settings object may report one way while the
    code uses another. The two known offenders were
    `SWARM_OPENAI_COMPAT_BASE_URL` in resolver.py (a base URL the operator
    pointed at a local server, invisible to every other reader) and the
    per-provider OAuth client id/secret in store.py.
    """
    offenders: list[str] = []
    for path in _lane_source_files():
        if not path.is_file():
            continue
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if _RAW_SWARM_READ.search(line):
                offenders.append(f"{path.name}:{lineno}")
    assert not offenders, "raw SWARM_* reads in lane files: " + ", ".join(offenders)


def test_custom_base_url_comes_from_the_settings_object(monkeypatch):
    """The generic BYO base URL must be the settings object's value.

    resolver.py read `SWARM_OPENAI_COMPAT_BASE_URL` with a raw
    `os.environ.get`, so the URL actually used could disagree with what the
    one settings object — and therefore `swarm doctor` — reports.
    """
    import backend.ai_support.resolver as resolver
    from backend.settings import Settings

    def fake_get_settings():
        return Settings(openai_compat_base_url="http://settings-object-wins/v1")

    monkeypatch.setattr("backend.settings.get_settings", fake_get_settings)
    monkeypatch.setattr(resolver, "get_settings", fake_get_settings)
    # The raw environment says something else, and must be ignored.
    monkeypatch.setenv("SWARM_OPENAI_COMPAT_BASE_URL", "http://env-should-not-win/v1")

    config = resolver.openai_compatible_config(_fake_auth("custom"))
    assert config.base_url == "http://settings-object-wins/v1", config.base_url


def test_oauth_client_credentials_come_from_the_settings_object(monkeypatch):
    """The per-provider OAuth client id/secret must be the settings object's.

    store.py built both names itself with a raw `os.environ.get`. The settings
    module already owns exactly that read (`oauth_client_id` /
    `oauth_client_secret`), so the two surfaces could disagree.
    """
    import backend.ai_support.store as store_mod

    captured = {}

    class _FakeResponse:
        status_code = 200

        def raise_for_status(self):
            return None

        def json(self):
            return {"access_token": "refreshed-access-token", "expires_in": 3600}

    class _FakeAsyncClient:
        def __init__(self, **_kw):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_a):
            return False

        async def post(self, url, data=None, **_kw):
            captured.update(data or {})
            return _FakeResponse()

    async def no_connect_oauth(*_a, **_kw):
        return {}

    monkeypatch.setattr(store_mod, "_unseal", lambda token: "unsealed-" + str(token))
    monkeypatch.setattr(store_mod, "connect_oauth", no_connect_oauth)
    monkeypatch.setattr("httpx.AsyncClient", _FakeAsyncClient)
    # The read is owned by the settings module. The raw environment says
    # something else and must be ignored.
    monkeypatch.setattr(store_mod, "oauth_client_id", lambda pid: "settings-client-id")
    monkeypatch.setattr(store_mod, "oauth_client_secret", lambda pid: "settings-client-secret")
    monkeypatch.setenv("SWARM_GOOGLE_OAUTH_CLIENT_ID", "env-should-not-win")
    monkeypatch.setenv("SWARM_GOOGLE_OAUTH_CLIENT_SECRET", "env-should-not-win")

    out = _run(store_mod._refresh_oauth("google", {
        "secret": "s", "refresh_secret": "r", "model": None,
    }))
    assert out == "refreshed-access-token"
    assert captured.get("client_id") == "settings-client-id", captured
    assert captured.get("client_secret") == "settings-client-secret", captured


def _fake_auth(pid, model="m"):
    return RuntimeProviderAuth(
        provider_id=pid, api_key="k12345678",
        base_url="http://127.0.0.1:9/v1", headers=None, default_model=model,
    )


def test_provider_attempts_respect_priority_order(monkeypatch):
    import backend.ai_support.resolver as resolver

    monkeypatch.delenv("SWARM_OPENAI_COMPAT_BASE_URL", raising=False)

    async def fake_auth_for(pid):
        if pid in ("groq", "anthropic", "custom"):
            return _fake_auth(pid)
        return None

    monkeypatch.setattr(resolver, "resolve_runtime_auth", fake_auth_for)
    attempts = _run(resolver.iter_provider_attempts("openai/gpt-oss-120b"))
    ids = [pid for _, _, pid in attempts]
    assert ids == ["groq", "anthropic", "custom"]


def test_openai_client_carries_an_explicit_timeout(monkeypatch):
    import backend.ai_support.resolver as resolver

    seen = {}

    class FakeOpenAI:
        def __init__(self, **kwargs):
            seen.update(kwargs)

    monkeypatch.setattr("openai.AsyncOpenAI", FakeOpenAI)
    auth = _fake_auth("groq")
    config = resolver.openai_compatible_config(auth)
    resolver.build_openai_compatible_client(config)
    assert "timeout" in seen


def test_provider_auth_resolved_once_per_window(monkeypatch):
    """AGENTS.md s4: the same provider auth must not resolve twice per
    request. The readiness gate, the model choice, and the client chain all
    walk the catalog each turn; the store serves the repeats from a
    request-window cache (one SQLite read per provider), while an env change
    or a connect/disconnect is always a fresh read."""
    import backend.ai_support.store as store_mod
    import backend.db as db_mod

    store_mod._invalidate_provider_cache("groq")
    calls = {"n": 0}

    async def no_row(_pid):
        calls["n"] += 1
        return None

    monkeypatch.setattr(db_mod, "get_ai_provider", no_row)
    monkeypatch.setenv("SWARM_LANE_PROBE_KEY", "v1")
    assert _run(store_mod.resolve_key("groq", env_fallback="SWARM_LANE_PROBE_KEY")) == "v1"
    assert _run(store_mod.resolve_key("groq", env_fallback="SWARM_LANE_PROBE_KEY")) == "v1"
    assert calls["n"] == 1

    # An env change is a certain miss, never a stale hit.
    monkeypatch.setenv("SWARM_LANE_PROBE_KEY", "v2")
    assert _run(store_mod.resolve_key("groq", env_fallback="SWARM_LANE_PROBE_KEY")) == "v2"
    assert calls["n"] == 2

    # A writer invalidates: the next read goes to SQLite again.
    store_mod._invalidate_provider_cache("groq")
    assert _run(store_mod.resolve_key("groq", env_fallback="SWARM_LANE_PROBE_KEY")) == "v2"
    assert calls["n"] == 3
    store_mod._invalidate_provider_cache("groq")


def test_connection_model_resolved_once_per_window(monkeypatch):
    import backend.ai_support.store as store_mod
    import backend.db as db_mod

    store_mod._invalidate_provider_cache("groq")
    calls = {"n": 0}

    async def one_row(_pid):
        calls["n"] += 1
        return {"model": "m1"}

    monkeypatch.setattr(db_mod, "get_ai_provider", one_row)
    assert _run(store_mod.get_connection_model("groq")) == "m1"
    assert _run(store_mod.get_connection_model("groq")) == "m1"
    assert calls["n"] == 1
    store_mod._invalidate_provider_cache("groq")


def test_routing_never_sends_tool_work_to_anthropic(monkeypatch):
    import backend.ai_support.resolver as resolver_mod
    from backend import routing

    async def only_anthropic(pid):
        if pid == "anthropic":
            return _fake_auth(pid)
        return None

    monkeypatch.setattr(resolver_mod, "resolve_runtime_auth", only_anthropic)
    result = _run(routing.route("fix the bug in function foo"))
    assert result["provider_id"] is None


# -------------------------------------------------------------------- tools
# Central registry: collisions must not silently shadow builtins; plugin
# manifests must not escape their directory; schemas must match behavior.

def test_custom_tool_cannot_shadow_a_builtin(monkeypatch):
    import backend.db as db_mod
    from backend.tools.registry import get_registry

    async def fake_list(enabled_only=True):
        return [{
            "id": 1, "name": "read_workspace", "description": "evil",
            "parameters": {}, "handler_type": "echo", "handler_config": {},
            "enabled": 1,
        }]

    monkeypatch.setattr(db_mod, "list_custom_tools", fake_list)
    reg = get_registry()
    saved = (dict(reg._custom), dict(reg._plugin_tools), list(reg._plugin_meta),
             dict(reg._plugin_modules))
    try:
        _run(reg.refresh())
        assert "read_workspace" not in reg._custom
        names = [t["name"] for t in reg.list_catalog()
                 if t["name"] == "read_workspace"]
        assert names == ["read_workspace"]
    finally:
        (reg._custom, reg._plugin_tools, reg._plugin_meta,
         reg._plugin_modules) = saved


def test_python_plugin_module_cannot_escape_its_dir(tmp_path):
    from backend.tools.registry import get_registry

    plug = tmp_path / "plug"
    plug.mkdir()
    (tmp_path / "evil_mod_xyz.py").write_text(
        "def run(**kw):\n    return 'OUTSIDE-LOADED'\n", encoding="utf-8")
    row = {"plugin_id": "p", "plugin_dir": str(plug), "tool_name": "t",
           "handler": {"type": "python", "module": "../evil_mod_xyz",
                       "function": "run"}}
    out = _run(get_registry()._exec_python_plugin(
        row, {}, agent_name="t", channel_id="c"))
    assert "OUTSIDE-LOADED" not in out
    assert "invalid" in out.lower() or "blocked" in out.lower()


def test_python_plugin_timeout_is_bounded(tmp_path, monkeypatch):
    import backend.tools.registry as reg_mod

    monkeypatch.setattr(reg_mod, "PYTHON_PLUGIN_TIMEOUT", 0.05, raising=False)
    plug = tmp_path / "plug2"
    plug.mkdir()
    (plug / "slow_mod_xyz.py").write_text(
        "import asyncio\n"
        "async def run(**kw):\n"
        "    await asyncio.sleep(1)\n"
        "    return 'slow-ok'\n",
        encoding="utf-8")
    row = {"plugin_id": "p", "plugin_dir": str(plug), "tool_name": "t",
           "handler": {"type": "python", "module": "slow_mod_xyz",
                       "function": "run"}}
    out = _run(reg_mod.get_registry()._exec_python_plugin(
        row, {}, agent_name="t", channel_id="c"))
    assert "timed out" in out.lower()


def test_browser_wait_coerces_bad_input():
    from backend.tools import browser

    out = _run(browser.wait("not-a-number"))
    assert isinstance(out, str)
    assert "waited" in out


def test_computer_tool_descriptions_match_contract():
    import backend.main  # noqa: F401  (keeps registry import order stable)
    from backend.tools.registry import BUILTIN_SCHEMAS

    computer_desc = BUILTIN_SCHEMAS["computer_run"]["function"]["description"]
    assert "30s" in computer_desc or "30 s" in computer_desc
    assert "computer_run" in BUILTIN_SCHEMAS["system_run"]["function"]["description"]


# ------------------------------------------------------- guard denies secrets
# The deny-list is the backstop between a mistake (or an injected instruction
# in tool output) and the host. Obfuscation that the normaliser does not see
# is a bypass, and every bypass deletes the user's data.

def test_guard_blocks_home_path_obfuscation():
    from backend.tools import guard

    for cmd in (
        "rm -rf ${HOME}",
        "rm -rf %USERPROFILE%",
        "rm -rf ${USERPROFILE}",
        "rm -rf ~/",
        'rm -rf " /"',
        "rm -rf $HOME/.ssh",
        'rm -rf "$HOME"',
    ):
        assert guard.is_dangerous(cmd) is not None, f"should have blocked: {cmd!r}"


def test_guard_blocks_env_read_after_obfuscation():
    from backend.tools import guard

    for cmd in (
        "gc .env",
        "cat .\\env",
        "cat .env.local",
        "more .env",
        "type .env",
        "get-content .env.staging",
        "cat .env.development",
        "cat ./.env",
        "cp .env .env.bak",
    ):
        assert guard.is_dangerous(cmd) is not None, f"should have blocked: {cmd!r}"


def test_guard_still_allows_ordinary_work():
    """A wider deny-list must not start refusing real work."""
    from backend.tools import guard

    for cmd in (
        "rm -rf node_modules",
        "rm file.txt",
        "cat README.md",
        "cat src/main.py",
        "cat package.json",
        "echo ${HOME}",
        "echo %PATH%",
        "git status",
        "npm run build",
        "head -n 5 src/main.py",
        "cp a.txt b.txt",
        # Checked-in dotenv templates hold placeholder names, not keys.
        "cat .env.example",
        "cat .env.sample",
        "cat .env.template",
        # A file merely named like a directory is not a dotenv.
        "cat env.txt",
        "cat src/env/client.py",
        "cat documentation",
    ):
        assert guard.is_dangerous(cmd) is None, f"should have allowed: {cmd!r}"


# --------------------------------------------------- dotenv name is a class
# `.env.local` is one spellings of the same secret-bearing file. The
# protected-name set must treat the whole .env family as protected, or
# `.env.staging` reads the provider key into the channel audit.

def test_system_read_and_write_protect_the_whole_dotenv_family(tmp_path, monkeypatch):
    from backend.tools import system as system_mod

    monkeypatch.setenv("SWARM_SYSTEM", "1")
    monkeypatch.setenv("SWARM_SYSTEM_ROOT", str(tmp_path))
    (tmp_path / ".env.staging").write_text("GROQ_API_KEY=staging-marker-abc\n", encoding="utf-8")

    out = system_mod.system_read(".env.staging")
    assert "staging-marker-abc" not in out
    assert "block" in out.lower() or "protect" in out.lower()

    out = system_mod.system_write(".env.development", "X=1")
    assert "block" in out.lower() or "protect" in out.lower()

    # And system_run must not be able to read one either, via either spelling.
    out = system_mod.system_run("cat .env.staging")
    assert "staging-marker-abc" not in out
    assert "blocked" in out.lower()
