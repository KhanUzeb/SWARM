"""The server, not the prompt, decides what a tool call may do.

The agent loop is the only place every path (chat, handoff, delegation,
workflow) converges, so these tests exercise `agent._execute_tool` directly.
"""
from __future__ import annotations

import asyncio

import pytest

import backend.agent as agent_mod
import backend.db as db
from backend import policy


def test_classification_keeps_local_work_allowed():
    assert policy.evaluate_tool("read_workspace")["decision"] == policy.ALLOW
    assert policy.evaluate_tool("write_workspace")["decision"] == policy.ALLOW
    assert policy.evaluate_tool("remember")["decision"] == policy.ALLOW
    assert policy.evaluate_tool("fetch_url")["decision"] == policy.ALLOW
    assert policy.evaluate_tool("delegate_task")["decision"] == policy.ALLOW


def test_privilege_granting_and_custom_tools_need_review():
    # Creating a bot names its prompt and tool list, so it is a privilege
    # grant. A custom name that looks read-only can still make network calls.
    assert policy.evaluate_tool("create_agent")["decision"] == policy.REVIEW
    assert policy.evaluate_tool("get_price_feed")["decision"] == policy.REVIEW
    assert policy.evaluate_tool("get_price_feed")["reasons"] == [
        "unrecognised tool defaults to review"
    ]


@pytest.mark.parametrize(
    "tool",
    ["system_run", "computer_run", "read_only_shell", "browser_click",
     "system_write", "plugin:someone:send", "custom_unclassified"],
)
def test_consequential_and_unknown_tools_require_review(tool):
    assert policy.evaluate_tool(tool)["decision"] == policy.REVIEW


def test_destructive_names_are_denied_outright():
    for tool in ("delete_production", "drop_tables", "rm_rf", "exfiltrate_all"):
        assert policy.evaluate_tool(tool)["decision"] == policy.DENY


def test_argument_hash_is_order_insensitive_and_value_sensitive():
    a = policy.canonical_args_hash({"path": "a.txt", "content": "hi"})
    b = policy.canonical_args_hash({"content": "hi", "path": "a.txt"})
    c = policy.canonical_args_hash({"content": "bye", "path": "a.txt"})
    assert a == b
    assert a != c


def test_review_creates_a_bound_pending_approval(client):
    async def run():
        with pytest.raises(policy.PolicyReviewRequired) as info:
            await agent_mod._execute_tool(
                "system_run", {"command": "echo gated"},
                agent_name="swarm", channel_id="general", allowed=["system_run"],
            )
        approval = info.value.approval
        assert approval["tool"] == "system_run"
        assert approval["args_hash"] == policy.canonical_args_hash(
            {"command": "echo gated"}
        )
        pending = await db.list_approvals(channel_id="general", status="pending")
        assert [row["id"] for row in pending] == [approval["id"]]
        return approval

    asyncio.run(run())


def test_approved_grant_runs_once_and_only_for_the_exact_call(client):
    async def run():
        args = {"command": "echo once"}
        with pytest.raises(policy.PolicyReviewRequired) as info:
            await agent_mod._execute_tool(
                "system_run", args,
                agent_name="swarm", channel_id="general", allowed=["system_run"],
            )
        approval_id = info.value.approval["id"]
        await db.resolve_approval(approval_id, "approved", resolved_by="uzeb")

        # The grant is consumed by the call it authorises. The command is
        # disabled by default, so reaching the tool (rather than the gate) is
        # what proves the gate opened.
        result = await agent_mod._execute_tool(
            "system_run", args,
            agent_name="swarm", channel_id="general", allowed=["system_run"],
        )
        assert "disabled" in result.lower()

        # Single use: the same call is gated again.
        with pytest.raises(policy.PolicyReviewRequired):
            await agent_mod._execute_tool(
                "system_run", args,
                agent_name="swarm", channel_id="general", allowed=["system_run"],
            )

        # A different command never inherits the grant.
        with pytest.raises(policy.PolicyReviewRequired):
            await agent_mod._execute_tool(
                "system_run", {"command": "echo twice"},
                agent_name="swarm", channel_id="general", allowed=["system_run"],
            )

    asyncio.run(run())


def test_grant_is_bound_to_agent_and_channel(client):
    async def run():
        args = {"command": "echo bound"}
        with pytest.raises(policy.PolicyReviewRequired) as info:
            await agent_mod._execute_tool(
                "system_run", args,
                agent_name="swarm", channel_id="general", allowed=["system_run"],
            )
        await db.resolve_approval(info.value.approval["id"], "approved")

        with pytest.raises(policy.PolicyReviewRequired):
            await agent_mod._execute_tool(
                "system_run", args,
                agent_name="ledger", channel_id="general", allowed=["system_run"],
            )

    asyncio.run(run())


def test_denied_tool_never_reaches_the_registry(client):
    async def run():
        with pytest.raises(policy.PolicyDenied):
            await agent_mod._execute_tool(
                "delete_production", {},
                agent_name="swarm", channel_id="general", allowed=["delete_production"],
            )

    asyncio.run(run())


def test_request_approval_binds_the_named_call(client):
    async def run():
        result = await agent_mod._execute_tool(
            "request_approval",
            {
                "action": "run the release check",
                "tool": "system_run",
                "arguments": {"command": "echo release"},
            },
            agent_name="swarm", channel_id="general",
            allowed=["request_approval"],
        )
        assert "pending" in result.lower()
        rows = await db.list_approvals(channel_id="general", status="pending")
        assert len(rows) == 1
        row = rows[0]
        assert row["tool"] == "system_run"
        assert row["args_hash"] == policy.canonical_args_hash(
            {"command": "echo release"}
        )
        assert row["expires_at"] > row["created_at"]

    asyncio.run(run())


def test_review_stops_the_turn_instead_of_continuing(client, monkeypatch):
    rounds = {"n": 0}

    async def fake_complete(*_a, **_kw):
        rounds["n"] += 1
        if rounds["n"] == 1:
            return "", [{
                "id": "c0", "name": "system_run",
                "arguments": '{"command":"echo gated"}',
            }], None
        return "should not be asked to continue", [], None

    monkeypatch.setattr(agent_mod, "_complete_stream", fake_complete)
    result = asyncio.run(agent_mod._run_with_client(
        object(), "model", {"name": "swarm", "max_tool_calls": 12}, "general", [],
        use_tools=True, allowed=["system_run"], on_tools_ready=None,
        on_stream_start=None, on_token=None, trace=None,
    ))
    assert rounds["n"] == 2, "the closing summary round runs, not another tool round"
    events = result["tool_events"]
    assert events and events[0].get("blocked") == "review"
    assert events[0].get("approval_id") is not None
    assert "approval" in result["reply"].lower() or "stopped" in result["reply"].lower()


def test_a_tool_the_agent_does_not_have_never_mints_an_approval(client):
    async def run():
        out = await agent_mod._execute_tool(
            "system_run", {"command": "echo nope"},
            agent_name="swarm", channel_id="general", allowed=[],
        )
        assert "disabled" in out
        assert await db.list_approvals(channel_id="general", status="pending") == []

    asyncio.run(run())


def test_an_expired_approval_is_not_a_grant(client):
    import time

    async def run():
        row = await db.create_approval(
            "swarm", "general", "stale call", tool="system_run",
            args_hash=policy.canonical_args_hash({"command": "echo stale"}),
            expires_at=time.time() - 1,
        )
        await db.resolve_approval(row["id"], "approved")
        assert await db.find_approval_grant(
            "swarm", "general", "system_run",
            policy.canonical_args_hash({"command": "echo stale"}),
        ) is None

    asyncio.run(run())


def test_a_grant_is_bound_to_the_person_who_asked(client):
    import time

    async def run():
        args_hash = policy.canonical_args_hash({"command": "echo mine"})
        row = await db.create_approval(
            "swarm", "general", "my call", tool="system_run",
            args_hash=args_hash, requested_by="alice",
            expires_at=time.time() + 60,
        )
        await db.resolve_approval(row["id"], "approved")
        assert await db.find_approval_grant(
            "swarm", "general", "system_run", args_hash, requested_by="bob",
        ) is None
        assert await db.find_approval_grant(
            "swarm", "general", "system_run", args_hash, requested_by="alice",
        ) is not None

    asyncio.run(run())


def test_the_person_who_approved_can_spend_the_grant(client):
    """An admin resolving another member's request must not dead-end.

    The approval row names the requester, but the turn that continues after
    the decision is triggered by the *resolver's* message. Matching only the
    requester meant the grant could never be spent and the bot asked again
    immediately; the resolver is the other half of the same authorisation.
    """
    import time

    async def run():
        args_hash = policy.canonical_args_hash({"command": "echo escalated"})
        row = await db.create_approval(
            "swarm", "general", "escalated call", tool="system_run",
            args_hash=args_hash, requested_by="alice",
            expires_at=time.time() + 60,
        )
        await db.resolve_approval(row["id"], "approved", resolved_by="bob")
        assert await db.find_approval_grant(
            "swarm", "general", "system_run", args_hash, requested_by="bob",
        ) is not None
        assert await db.find_approval_grant(
            "swarm", "general", "system_run", args_hash, requested_by="carol",
        ) is None

    asyncio.run(run())


def test_a_delegated_agents_block_stops_the_parent(client, monkeypatch):
    import types

    class FakeRegistry:
        async def execute(self, name, args, *, audit_sink=None, **_kw):
            if audit_sink is not None:
                audit_sink.append({
                    "tool": "system_run", "blocked": "review", "approval_id": 41,
                })
            return "child says it will continue"

    monkeypatch.setattr(agent_mod, "get_registry", lambda: FakeRegistry())

    async def run():
        with pytest.raises(policy.PolicyReviewRequired) as info:
            await agent_mod._execute_tool(
                "delegate_task", {"agent": "ledger", "task": "clean up"},
                agent_name="swarm", channel_id="general",
                allowed=["delegate_task"], audit_sink=[],
            )
        assert info.value.approval["id"] == 41

    asyncio.run(run())
