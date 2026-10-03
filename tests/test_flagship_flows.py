"""End-to-end tests for the three flagship flows, with providers mocked.

A. Approval gate — allow and deny, end to end through the real
   `request_approval` tool and the real `/api/approvals/{id}/resolve` route.
B. Handoff ordering — 2 and 3 bots, mention order and sequential (not
   interleaved) execution, plus the @handoff chain depth cap.
C. Routine — the real scheduler path, driven deterministically.
D. Error UX (3.2) — provider failure, missing key, rate limit and tool
   failure each become a readable in-thread message, never a raw exception
   and never a 500.

Provider mocking happens at the same seam the rest of the suite uses: the
attempt list returned by `ai_support.resolver.iter_provider_attempts` plus
the credential lookup in `ai_support.store.resolve_key`. Everything below
them (the tool loop, the tool registry, the approval tool, the DB, the
scheduler) is the real code. No network, no real API key, no sleeps of
minutes: the routine clock is moved by setting `next_run_at`.
"""
from __future__ import annotations

import asyncio
import inspect
import json
import time
from typing import Any

import backend.db as db
import backend.main as main

# Provider keys that would otherwise leak in from the developer's shell.
_ALWAYS_CLEARED = (
    "GROQ_API_KEY", "OPENROUTER_API_KEY", "OPENAI_API_KEY", "ANTHROPIC_API_KEY",
    "HF_TOKEN", "HUGGINGFACE_API_KEY", "TOGETHER_API_KEY", "GOOGLE_API_KEY",
    "MISTRAL_API_KEY", "DEEPSEEK_API_KEY", "XAI_API_KEY", "FIREWORKS_API_KEY",
    "PERPLEXITY_API_KEY", "NVIDIA_API_KEY", "OPENCODE_API_KEY", "ZEN_API_KEY",
    "SWARM_OPENAI_COMPAT_API_KEY", "SWARM_OPENAI_COMPAT_BASE_URL",
)


def _clear_rate() -> None:
    main._last_write.clear()


def _wait_for(predicate, timeout: float = 15.0, what: str = "background work"):
    deadline = time.time() + timeout
    while time.time() < deadline:
        result = predicate()
        if result:
            return result
        time.sleep(0.05)
    raise AssertionError(f"timed out waiting for {what}")


def _messages(client, auth, channel: str = "general") -> list[dict]:
    res = client.get(f"/api/channels/{channel}/messages", headers=auth)
    assert res.status_code == 200, res.text
    return res.json()


# ------------------------------------------------------- provider doubles --

class _FakeFn:
    def __init__(self, name: str = "", arguments: str = ""):
        self.name = name
        self.arguments = arguments


class _FakeTC:
    def __init__(self, index: int = 0, id: str | None = None,
                 name: str = "", arguments: str = ""):
        self.index = index
        self.id = id
        self.function = _FakeFn(name, arguments)


class _FakeDelta:
    def __init__(self, content: str | None = None, tool_calls=None):
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


def _text_round(text: str) -> list[_FakeChunk]:
    return [_FakeChunk(_FakeDelta(content=text))]


def _tool_round(name: str, args: dict[str, Any], call_id: str = "call_1") -> list[_FakeChunk]:
    return [_FakeChunk(_FakeDelta(tool_calls=[
        _FakeTC(0, call_id, name, json.dumps(args)),
    ]))]


class ScriptedProvider:
    """Fake OpenAI-compatible client driven by a script.

    `runs` is a list of *runs* (one per agent turn). Each run is a list of
    *rounds* (one provider call each). A round is either a list of chunks or
    an async callable returning chunks (so a round may consult the DB) or
    an exception instance to raise.
    """

    def __init__(self, runs: list[Any]):
        self.runs = [list(run) for run in runs]
        self.run_index = 0
        self.round_index = 0
        self.requests: list[dict[str, Any]] = []
        self.chat = self
        self.completions = self

    async def create(self, **kwargs):
        self.requests.append(kwargs)
        entry = self._next_round()
        if isinstance(entry, BaseException):
            raise entry
        # A round may be a plain chunk list or an async factory (so it can
        # consult the DB before answering). Resolve the factory first, then
        # re-check: an exception may also be produced dynamically.
        chunks = await entry() if inspect.iscoroutinefunction(entry) else entry
        if isinstance(chunks, BaseException):
            raise chunks
        return _FakeStream(chunks)

    def _next_round(self):
        run = self.runs[min(self.run_index, len(self.runs) - 1)]
        if self.round_index >= len(run):
            self.run_index += 1
            self.round_index = 0
            run = self.runs[min(self.run_index, len(self.runs) - 1)]
            if self.run_index >= len(self.runs):
                # The script is a finite sequence of agent turns; a repeat
                # would silently fake a tool loop. Fail loudly instead.
                raise AssertionError(
                    f"provider scripted only {len(self.runs)} run(s) but was called "
                    f"for run {self.run_index + 1}; extend the script"
                )
        entry = run[self.round_index]
        self.round_index += 1
        return entry

    @property
    def turns(self) -> int:
        return self.run_index + 1


class _ProviderStatus(Exception):
    """Provider HTTP failure carrying a status code, like the SDK's."""

    def __init__(self, status_code: int, message: str):
        super().__init__(message)
        self.status_code = status_code


def _install_provider(monkeypatch, provider, *, key: str | None = "test-key-not-real"):
    """Mock credentials + the attempt list; everything below stays real."""
    from backend.ai_support import resolver, store

    async def fake_resolve_key(provider_id, **_kwargs):
        return key

    async def fake_attempts(_agent_model):
        return [(provider, "test-model", "groq")]

    monkeypatch.setattr(store, "resolve_key", fake_resolve_key)
    monkeypatch.setattr(resolver, "iter_provider_attempts", fake_attempts)


def _assert_readable_error(body: str, *, raw_must_not_appear: tuple[str, ...] = ()) -> None:
    """An in-thread failure must read like a message, not like a crash."""
    assert body.strip(), "the failure message is empty"
    assert main.agent.is_agent_error(body), (
        f"AGENTS.md s6 requires failures to be '[agent error: ...]' messages; got: {body!r}"
    )
    assert "Traceback" not in body, f"stack trace leaked into the thread: {body!r}"
    assert "  File \"" not in body, f"stack frame leaked into the thread: {body!r}"
    for raw in raw_must_not_appear:
        assert raw not in body, f"raw exception text {raw!r} leaked into the thread: {body!r}"
    # Human-readable: sentences, not a repr. No bare class names or dotted
    # exception paths, and it must carry a next step rather than dead-end.
    assert "Error(" not in body and "Exception(" not in body, (
        f"exception repr leaked into the thread: {body!r}"
    )
    assert len(body) > len("[agent error: ]"), "error message has no substance"
    assert not body.rstrip().endswith(":"), f"error message dead-ends without a next step: {body!r}"


# --------------------------------------------------------------- A. approval --

def _approval_run_allow(monkeypatch, *, deny: bool):
    """Script two agent turns for the approval flow.

    Turn 1 calls the real `request_approval` tool. Turn 2 (triggered by the
    human resolving the gate) reads the real approval row and, only when it
    was approved, performs the action — a `remember` write that is visible
    as a memory row and as a `system` audit message.
    """
    async def _ask():
        return _tool_round(
            "request_approval",
            {"action": "send the outreach email", "detail": "to Dana about the launch"},
        )

    async def _read_gate():
        # These rounds run on the turn the human's decision triggered, i.e.
        # after request_approval persisted a row and the human resolved it, so
        # the gate is readable here. status=None: it is no longer pending.
        rows = await db.list_approvals(channel_id="dm-swarm", status=None)
        gate = next(r for r in rows if r["action"] == "send the outreach email")
        approval.update(gate)
        return gate

    async def _send_after_approval():
        gate = await _read_gate()
        assert gate["status"] == "approved", (
            f"the gated action was scripted but the gate says {gate['status']!r}"
        )
        return _tool_round("remember", {"body": "sent the outreach email to Dana"})

    async def _stand_down():
        gate = await _read_gate()
        assert gate["status"] == "denied", (
            f"expected a denied gate, found {gate['status']!r}"
        )
        return _text_round("Understood — I will not send it. Standing down.")

    approval: dict[str, Any] = {}
    second_turn = ([_stand_down] if deny else [
        _send_after_approval,
        _text_round("Sent the outreach to Dana, and recorded it."),
    ])
    provider = ScriptedProvider([
        [_ask, _text_round("I need approval before I send anything.")],
        second_turn,
    ])
    _install_provider(monkeypatch, provider)
    return provider


def test_approval_gate_allow_continues_the_flow(client, auth, monkeypatch):
    """allow: the gate records pending, the human approves, the work continues."""
    _approval_run_allow(monkeypatch, deny=False)

    async def run_first_turn():
        row = await db.fetch_agent("swarm")
        assert row is not None
        return await main._run_agent("dm-swarm", row)

    first = asyncio.run(run_first_turn())

    # The gate stopped the turn instead of pushing on.
    assert "approval" in first["reply"].lower()
    pending = asyncio.run(db.list_approvals(channel_id="dm-swarm", status="pending"))
    assert len(pending) == 1, "the real request_approval tool must create one row"
    gate = pending[0]
    assert gate["agent_name"] == "swarm"
    assert gate["status"] == "pending"
    assert "send the outreach email" in gate["action"]
    # The bot is visibly held, not idle.
    assert client.get("/api/agents/swarm", headers=auth).json()["status"] == "needs_approval"
    # Every tool call is audited in-channel.
    audit = [m for m in _messages(client, auth, "dm-swarm")
             if m["author_kind"] == "system" and "request_approval" in m["body"]]
    assert audit, "a tool call must persist as a system audit message"

    # The human resolves it.
    _clear_rate()
    resolved = client.post(
        f"/api/approvals/{gate['id']}/resolve",
        headers=auth,
        json={"status": "approved"},
    )
    assert resolved.status_code == 200, resolved.text
    assert resolved.json()["status"] == "approved"

    # Approval row + bot status reflect the decision.
    row = asyncio.run(db.get_approval(gate["id"]))
    assert row["status"] == "approved", "the approval row must record the decision"
    assert row["resolved_at"], "an approved row must be stamped as resolved"

    # The in-thread message reflects the decision, with the right instruction.
    decision_msg = _wait_for(
        lambda: [m for m in _messages(client, auth, "dm-swarm")
                 if "Approved: send the outreach email" in m["body"]] or None,
        what="the approval decision message",
    )[0]
    assert "Continue from here." in decision_msg["body"]
    assert decision_msg["author_kind"] == "human"

    # The flow continues: the action runs only after approval.
    done = _wait_for(
        lambda: [m for m in _messages(client, auth, "dm-swarm")
                 if m["body"] == "Sent the outreach to Dana, and recorded it."] or None,
        what="the post-approval reply",
    )
    assert done[0]["author_kind"] == "agent"
    memories = client.get("/api/agents/swarm/memory", headers=auth).json()
    assert any("Dana" in m["body"] for m in memories), (
        "after an approval the action must actually be performed"
    )
    # Resolving the gate released the bot rather than leaving it held.
    _wait_for(
        lambda: client.get("/api/agents/swarm", headers=auth).json()["status"] == "idle"
        or None,
        what="the bot to return to idle",
    )


def test_approval_gate_deny_does_not_proceed(client, auth, monkeypatch):
    """deny: the action must NOT run. A deny that still executes is the bug."""
    _approval_run_allow(monkeypatch, deny=True)

    async def run_first_turn():
        row = await db.fetch_agent("swarm")
        return await main._run_agent("dm-swarm", row)

    asyncio.run(run_first_turn())
    pending = asyncio.run(db.list_approvals(channel_id="dm-swarm", status="pending"))
    assert len(pending) == 1
    gate = pending[0]

    _clear_rate()
    resolved = client.post(
        f"/api/approvals/{gate['id']}/resolve",
        headers=auth,
        json={"status": "denied"},
    )
    assert resolved.status_code == 200, resolved.text
    assert resolved.json()["status"] == "denied"

    row = asyncio.run(db.get_approval(gate["id"]))
    assert row["status"] == "denied", "the approval row must record the decision"
    assert row["resolved_at"]

    # The in-thread message tells the bot to stand down.
    decision = _wait_for(
        lambda: [m for m in _messages(client, auth, "dm-swarm")
                 if "Denied: send the outreach email" in m["body"]] or None,
        what="the denial message",
    )[0]
    assert "Do not proceed with that action." in decision["body"]
    assert "Continue from here." not in decision["body"]

    # Let any background work settle, then prove nothing was performed.
    _wait_for(
        lambda: [m for m in _messages(client, auth, "dm-swarm")
                 if "Standing down" in m["body"]] or None,
        what="the post-denial reply",
    )
    # The bot is released after a denial. Poll rather than reading once: the
    # reply is persisted just before the status flips, so a single read races
    # it — but a status that never settles would fail here too.
    _wait_for(
        lambda: client.get("/api/agents/swarm", headers=auth).json()["status"] == "idle"
        or None,
        what="the bot to return to idle after a denial",
    )

    memories = client.get("/api/agents/swarm/memory", headers=auth).json()
    assert not any("Dana" in m["body"] for m in memories), (
        "DENY SAFETY: the gated action ran even though a human denied it"
    )
    audit = [m for m in _messages(client, auth, "dm-swarm")
             if m["author_kind"] == "system" and "remember(" in m["body"]]
    assert not audit, (
        f"DENY SAFETY: the gated action was executed after a denial: {audit}"
    )
    # The gate is not left pending for a second click.
    assert not asyncio.run(db.list_approvals(channel_id="dm-swarm", status="pending"))


def test_denied_gate_cannot_be_resolved_twice(client, auth, monkeypatch):
    """A second decision on a resolved gate is refused, not re-run."""
    _install_provider(monkeypatch, ScriptedProvider([[_tool_round(
        "request_approval", {"action": "delete the release branch"})]]))

    async def run_first_turn():
        row = await db.fetch_agent("swarm")
        return await main._run_agent("dm-swarm", row)

    asyncio.run(run_first_turn())
    gate = asyncio.run(db.list_approvals(channel_id="dm-swarm", status="pending"))[0]

    _clear_rate()
    first = client.post(f"/api/approvals/{gate['id']}/resolve",
                        headers=auth, json={"status": "denied"})
    assert first.status_code == 200
    _clear_rate()
    second = client.post(f"/api/approvals/{gate['id']}/resolve",
                         headers=auth, json={"status": "approved"})
    assert second.status_code == 409, "a resolved gate must not be re-decided"
    assert asyncio.run(db.get_approval(gate["id"]))["status"] == "denied"


# ------------------------------------------------------------- B. handoffs --

def _recording_agents(monkeypatch, plan: dict[str, str], log: list[str]):
    """Mock agent replies, recording enter/exit so overlap is detectable."""
    async def fake_reply(agent_row, channel_id, history, on_tools_ready=None,
                         on_stream_start=None, on_token=None, **_kwargs):
        name = agent_row["name"]
        log.append(f"enter:{name}")
        await asyncio.sleep(0.15)  # a slow first agent must not be overtaken
        reply = plan[name]
        log.append(f"exit:{name}")
        return {"reply": reply, "tool_events": [], "usage": {}}

    monkeypatch.setattr(main.agent, "generate_reply", fake_reply)


def _agent_replies(client, auth, channel: str = "general") -> list[tuple[str, str]]:
    return [(m["author"], m["body"]) for m in _messages(client, auth, channel)
            if m["author_kind"] == "agent"]


def test_two_bots_reply_in_mention_order_sequentially(client, auth, monkeypatch):
    """2 bots: reply order matches mention order, and never interleaves."""
    log: list[str] = []
    _recording_agents(
        monkeypatch,
        {"ledger": "decision log", "coder": "code notes"},
        log,
    )
    _clear_rate()
    res = client.post(
        "/api/channels/general/messages",
        json={"author": "uzeb", "body": "@ledger log the decision then @coder write the tests"},
        headers=auth,
    )
    assert res.status_code == 200, res.text

    replies = _wait_for(
        lambda: ([m for m in _agent_replies(client, auth)
                  if m in (("ledger", "decision log"), ("coder", "code notes"))] or None)
        if len({a for a, _ in
                [m for m in _agent_replies(client, auth)
                 if m in (("ledger", "decision log"), ("coder", "code notes"))]}) == 2 else None,
        what="both bots to reply",
    )
    assert [a for a, _ in replies] == ["ledger", "coder"], (
        f"AGENTS.md s6: multi-mentions reply in mention order; got {replies}"
    )

    # Sequential, not interleaved: every enter is closed before the next one.
    assert log == ["enter:ledger", "exit:ledger", "enter:coder", "exit:coder"], (
        f"multi-mention replies interleaved instead of running one after another: {log}"
    )


def test_three_bots_reply_in_mention_order(client, auth, monkeypatch):
    """3 bots: all three reply, in mention order."""
    plan = {"coder": "code notes", "ledger": "decision log", "swarm": "the summary"}
    _recording_agents(monkeypatch, plan, [])
    _clear_rate()
    res = client.post(
        "/api/channels/general/messages",
        json={"author": "uzeb",
              "body": "@coder write the tests, @ledger log the decision, @swarm summarize"},
        headers=auth,
    )
    assert res.status_code == 200, res.text

    _wait_for(lambda: len(_agent_replies(client, auth)) >= 3 or None,
              what="three bot replies")
    replies = _agent_replies(client, auth)
    assert [a for a, _ in replies] == ["coder", "ledger", "swarm"], (
        f"three-bot mention order not preserved: {replies}"
    )


def test_handoff_chain_runs_bot_to_bot_in_order(client, auth, monkeypatch):
    """@handoff: an agent reply that names the next bot runs that bot."""
    log: list[str] = []
    plan = {
        "swarm": "shipping plan is ready — @ledger log the decisions",
        "ledger": "decisions logged — @coder turn them into tests",
        "coder": "tests written",
    }
    _recording_agents(monkeypatch, plan, log)
    _clear_rate()
    client.post("/api/channels/general/messages",
                json={"author": "uzeb", "body": "@swarm ship the release"},
                headers=auth)
    _wait_for(lambda: len(_agent_replies(client, auth)) >= 3 or None,
              what="the handoff chain to finish")
    assert [a for a, _ in _agent_replies(client, auth)] == ["swarm", "ledger", "coder"]
    assert log == ["enter:swarm", "exit:swarm", "enter:ledger", "exit:ledger",
                   "enter:coder", "exit:coder"], f"handoff chain interleaved: {log}"


def test_handoff_chain_stops_at_the_depth_cap(client, auth, monkeypatch):
    """AGENTS.md s6 caps @handoff chains at depth 3 (4 participants, 3 hops)."""
    plan = {
        "swarm": "start — @ledger go",
        "ledger": "logged — @coder go",
        "coder": "coded — @swarm loop back",
    }
    _recording_agents(monkeypatch, plan, [])
    _clear_rate()
    client.post("/api/channels/general/messages",
                json={"author": "uzeb", "body": "@swarm start the loop"},
                headers=auth)
    _wait_for(lambda: len(_agent_replies(client, auth)) >= 4 or None,
              what="the capped chain to finish")
    time.sleep(0.5)  # give an uncapped chain a chance to keep going
    authors = [a for a, _ in _agent_replies(client, auth)]
    assert authors == ["swarm", "ledger", "coder", "swarm"], (
        f"handoff chain did not stop at the depth cap; got {authors}"
    )
    assert main.HANDOFF_DEPTH == 3


# --------------------------------------------------------------- C. routine --

def test_routine_fires_and_posts_its_digest(client, auth, monkeypatch):
    """A due routine fires on the scheduler path and posts into its channel."""
    provider = ScriptedProvider([[_text_round("Morning digest: 2 blockers open.")]])
    _install_provider(monkeypatch, provider)

    _clear_rate()
    created = client.post(
        "/api/routines",
        headers=auth,
        json={"agent_name": "swarm", "title": "Morning digest",
              "instructions": "Summarize the open blockers.",
              "interval_minutes": 60, "enabled": True},
    )
    assert created.status_code == 200, created.text
    routine = created.json()

    # Time travel: the routine is due right now. No waiting on the 20s tick.
    async def _force_due():
        await db.update_routine(routine["id"], {"next_run_at": 1})
        return await db.list_routine_runs(routine["id"])

    assert asyncio.run(_force_due()) == []
    before = asyncio.run(db.get_routine(routine["id"]))

    fired = asyncio.run(main.run_due_routines())
    assert fired >= 1, "a due routine must be picked up by the scheduler"

    runs = asyncio.run(db.list_routine_runs(routine["id"]))
    assert len(runs) == 1, f"expected exactly one routine run, got {runs}"
    assert runs[0]["status"] == "ok", runs[0]
    assert "blockers" in (runs[0]["excerpt"] or ""), runs[0]

    # It posted the [routine:…] trigger and the digest into the bot's 1:1.
    history = _messages(client, auth, "dm-swarm")
    trigger = [m for m in history if "[routine:Morning digest]" in m["body"]]
    assert trigger, "the routine must post its [routine:...] trigger into the channel"
    assert trigger[0]["author_kind"] == "system"
    digest = [m for m in history if m["body"] == "Morning digest: 2 blockers open."]
    assert digest and digest[0]["author_kind"] == "agent", "the routine must post its digest"

    # The schedule advanced, so it does not fire twice.
    after = asyncio.run(db.get_routine(routine["id"]))
    assert after["last_run_at"] is not None
    assert after["next_run_at"] > before["next_run_at"]
    assert asyncio.run(main.run_due_routines()) == 0, "a fired routine must reschedule"


def test_routine_scheduler_loop_fires_due_routines(client, auth, monkeypatch):
    """The real `_routine_loop` task fires a due routine without help."""
    _install_provider(monkeypatch, ScriptedProvider([[_text_round("Standup: nothing blocked.")]]))
    _clear_rate()
    routine = client.post(
        "/api/routines",
        headers=auth,
        json={"agent_name": "ledger", "title": "Daily standup",
              "instructions": "List blockers.", "interval_minutes": 30, "enabled": True},
    ).json()

    async def _drive_loop():
        await db.update_routine(routine["id"], {"next_run_at": 1})
        # The loop runs its tick before its first sleep, so one pass is enough.
        task = asyncio.create_task(main._routine_loop())
        try:
            for _ in range(100):  # ~2s worst case, no ROUTINE_TICK_SECONDS wait
                await asyncio.sleep(0.02)
                if await db.list_routine_runs(routine["id"]):
                    break
        finally:
            task.cancel()
            with suppress_cancel():
                await task

    asyncio.run(_drive_loop())

    runs = asyncio.run(db.list_routine_runs(routine["id"]))
    assert len(runs) == 1 and runs[0]["status"] == "ok", f"scheduler loop did not fire: {runs}"
    history = _messages(client, auth, "dm-ledger")
    assert any("[routine:Daily standup]" in m["body"] for m in history)
    assert any(m["body"] == "Standup: nothing blocked." for m in history)


def test_routine_provider_failure_is_readable_in_thread(client, auth, monkeypatch):
    """A routine whose provider fails posts the readable error, not a crash.

    The run is recorded `ok` because the turn itself succeeded — the failure
    was reported to the human in-thread, which is the documented contract.
    """
    _install_provider(monkeypatch, ScriptedProvider([
        [RuntimeError("scheduler boom: the orchestrator fell over")],
    ]))
    _clear_rate()
    routine = client.post(
        "/api/routines",
        headers=auth,
        json={"agent_name": "swarm", "title": "Fragile job",
              "instructions": "Do the thing.", "interval_minutes": 15, "enabled": True},
    ).json()

    async def _fire():
        await db.update_routine(routine["id"], {"next_run_at": 1})
        return await main.run_due_routines()

    assert asyncio.run(_fire()) == 1
    runs = asyncio.run(db.list_routine_runs(routine["id"]))
    assert len(runs) == 1

    bodies = [m["body"] for m in _messages(client, auth, "dm-swarm")]
    errors = [b for b in bodies if main.agent.is_agent_error(b)]
    assert errors, "a failing routine must still report the failure in-channel"
    _assert_readable_error(errors[0], raw_must_not_appear=("orchestrator fell over",))
    assert not any("scheduler boom" in b for b in bodies), (
        "a raw exception leaked into the channel from a routine"
    )


def test_routine_scheduler_failure_is_recorded_failed(client, auth, monkeypatch):
    """When the routine itself cannot run, the run is recorded `failed`.

    A missing bot makes _execute_routine raise before the agent runs, which is
    the branch that must be persisted rather than swallowed.
    """
    _install_provider(monkeypatch, ScriptedProvider([[_text_round("never reached")]]))
    _clear_rate()
    routine = client.post(
        "/api/routines",
        headers=auth,
        json={"agent_name": "swarm", "title": "Doomed job",
              "instructions": "Do the thing.", "interval_minutes": 15, "enabled": True},
    ).json()

    async def _fire():
        # Retire the bot so the run cannot start.
        await db.archive_agent("swarm")
        await db.update_routine(routine["id"], {"next_run_at": 1})
        return await main.run_due_routines()

    assert asyncio.run(_fire()) == 1
    runs = asyncio.run(db.list_routine_runs(routine["id"]))
    assert len(runs) == 1, runs
    assert runs[0]["status"] == "failed", (
        f"a routine that could not run must be recorded as failed: {runs[0]}"
    )
    # The failure is logged, not posted raw into the channel.
    bodies = [m["body"] for m in _messages(client, auth, "dm-swarm")]
    assert not any("bot missing" in b for b in bodies), (
        "a raw scheduler exception leaked into the channel"
    )


class suppress_cancel:  # noqa: N801 - tiny context manager, used once
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return exc_type is not None and issubclass(exc_type, asyncio.CancelledError)


# ----------------------------------------------------------- D. error UX ----

def _collect_agent_error(client, auth, channel: str, timeout: float = 20.0):
    return _wait_for(
        lambda: [m for m in _messages(client, auth, channel)
                 if m["author_kind"] == "agent"
                 and main.agent.is_agent_error(m["body"])] or None,
        timeout=timeout, what="the in-thread error message",
    )


def test_provider_failure_is_a_readable_in_thread_message(client, auth, monkeypatch):
    """Provider down: readable, actionable, no raw exception, no 500."""
    boom = _ProviderStatus(503, "service unavailable: groq shard 7 exploded")
    # A 5xx is retryable: the agent loop retries the turn once, then the
    # provider fallback loop tries again, so the turn makes two calls.
    provider = ScriptedProvider([[boom, boom]])
    _install_provider(monkeypatch, provider)

    _clear_rate()
    res = client.post("/api/channels/dm-swarm/messages",
                      json={"author": "uzeb", "body": "summarize the thread"},
                      headers=auth)
    assert res.status_code == 200, f"a provider outage must not 500: {res.text}"
    failures = _collect_agent_error(client, auth, "dm-swarm")
    body = failures[0]["body"]
    _assert_readable_error(body, raw_must_not_appear=("shard 7", "503", "service unavailable"))
    assert "provider is down" in body, (
        f"the message must name the real problem and a next step, got: {body!r}"
    )
    # A failed turn must release the bot rather than leave it stuck "working".
    _wait_for(
        lambda: client.get("/api/agents/swarm", headers=auth).json()["status"] == "idle"
        or None,
        what="the bot to return to idle after a provider failure",
    )


def test_missing_provider_key_points_at_command_center(client, auth, monkeypatch):
    """No key at all: point at Command Center → AI providers."""
    from backend.ai_support import store

    async def no_key(_provider_id, **_kwargs):
        return None

    monkeypatch.setattr(store, "resolve_key", no_key)
    for name in _ALWAYS_CLEARED:
        monkeypatch.delenv(name, raising=False)

    _clear_rate()
    res = client.post("/api/channels/dm-swarm/messages",
                      json={"author": "uzeb", "body": "summarize the thread"},
                      headers=auth)
    assert res.status_code == 200, f"a missing key must not 500: {res.text}"
    failures = _collect_agent_error(client, auth, "dm-swarm")
    body = failures[0]["body"]
    _assert_readable_error(body)
    assert "Command Center" in body and "AI providers" in body, (
        f"the missing-key message must point at Command Center → AI providers, got: {body!r}"
    )


def test_rate_limit_is_a_readable_in_thread_message(client, auth, monkeypatch):
    """429 from the provider: say so and tell the user to retry."""
    boom = _ProviderStatus(429, "rate_limit_error: tokens per min exceeded for org swarm-team")
    # The agent loop retries a retryable failure once, so the turn makes two
    # provider calls; then generate_reply's provider loop makes two more.
    provider = ScriptedProvider([
        [boom, boom, boom, boom],
    ])
    _install_provider(monkeypatch, provider)
    agent_mod = main.agent
    original_delay = agent_mod.RETRY_DELAY_SECONDS
    agent_mod.RETRY_DELAY_SECONDS = 0.01  # keep the suite fast; logic unchanged

    try:
        _clear_rate()
        res = client.post("/api/channels/dm-swarm/messages",
                          json={"author": "uzeb", "body": "summarize the thread"},
                          headers=auth)
        assert res.status_code == 200, f"a rate limit must not 500: {res.text}"
        failures = _collect_agent_error(client, auth, "dm-swarm")
    finally:
        agent_mod.RETRY_DELAY_SECONDS = original_delay

    body = failures[0]["body"]
    _assert_readable_error(body, raw_must_not_appear=("tokens per min", "org swarm-team"))
    assert "rate limited" in body.lower(), (
        f"the message must name the rate limit and a next step, got: {body!r}"
    )
    assert "try again" in body.lower(), f"no next step in: {body!r}"
    # The turn really did retry before giving up.
    assert len(provider.requests) >= 2, "a retryable failure must be retried once"


def test_tool_failure_is_readable_and_the_turn_survives(client, auth, monkeypatch):
    """A tool that raises must not kill the turn or leak its traceback."""
    from backend.tools.registry import get_registry

    registry = get_registry()
    original = registry.execute

    async def exploding_execute(name, args, **kwargs):
        if name == "remember":
            raise RuntimeError(
                "Traceback (most recent call last): File 'db.py', line 1 in add_memory: "
                "sqlite3.OperationalError: database is locked"
            )
        return await original(name, args, **kwargs)

    monkeypatch.setattr(registry, "execute", exploding_execute)

    # The model calls the tool, sees the failure, and still answers the user.
    provider = ScriptedProvider([
        [_tool_round("remember", {"body": "a durable fact"}),
         _text_round("I could not save that note, but here is the answer anyway.")],
    ])
    _install_provider(monkeypatch, provider)

    _clear_rate()
    res = client.post("/api/channels/dm-swarm/messages",
                      json={"author": "uzeb", "body": "remember the ship date"},
                      headers=auth)
    assert res.status_code == 200, f"a tool failure must not 500: {res.text}"

    # The turn survives: one failed tool does not abort the reply.
    reply = _wait_for(
        lambda: [m for m in _messages(client, auth, "dm-swarm")
                 if m["author_kind"] == "agent"
                 and m["body"] == "I could not save that note, but here is the answer anyway."]
        or None,
        timeout=20.0, what="the reply after the failed tool",
    )[0]
    assert reply["author_kind"] == "agent"

    # The failed call is audited in-channel (AGENTS.md s6: every tool call
    # persists as a system audit message) and reads as a failure, not a crash.
    audit = [m for m in _messages(client, auth, "dm-swarm")
             if m["author_kind"] == "system" and "remember(" in m["body"]]
    assert audit, "a failed tool call must still be audited in-channel"
    note = audit[0]["body"]
    assert "Traceback" not in note, f"a raw traceback was persisted: {note!r}"
    assert "database is locked" not in note, f"raw DB error leaked: {note!r}"
    assert "failed" in note.lower(), f"the audit line must say the tool failed: {note!r}"

    # No message anywhere in the thread leaks the raw exception.
    for m in _messages(client, auth, "dm-swarm"):
        assert "Traceback" not in m["body"]
        assert "sqlite3.OperationalError" not in m["body"]
    _wait_for(
        lambda: client.get("/api/agents/swarm", headers=auth).json()["status"] == "idle"
        or None,
        what="the bot to return to idle after a tool failure",
    )