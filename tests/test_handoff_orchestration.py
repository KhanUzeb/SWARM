"""Deterministic coverage for bot-to-bot handoff orchestration.

Pins the contract in backend/main.py: handoffs preserve the originating
objective, run recipients in mention order, stop at HANDOFF_DEPTH, and never
route around a pending approval.

Dedupe note (Lane D hygiene, 2026-11-07): the two order/depth cases used to
live here as ``test_handoff_routes_to_mentioned_recipient_in_order`` and
``test_handoff_stops_at_depth_boundary``, byte-for-byte duplicates of the
flagship flows ``test_handoff_chain_runs_bot_to_bot_in_order`` and
``test_handoff_chain_stops_at_the_depth_cap`` (same recipient plan, same
sequential-log assertion, same depth-cap assertion). They were removed here
with replacement coverage owned by ``tests/test_flagship_flows.py`` — no
assertion was lost, only the copy was. What remains here is the coverage
that is unique to this file: objective preservation and approval blocking.
"""
from __future__ import annotations

import asyncio
import time

import backend.db as db
import backend.main as main

from tests.test_flagship_flows import _clear_rate, _wait_for, _messages, _recording_agents, _agent_replies


def test_handoff_preserves_original_objective(client, auth, monkeypatch):
    async def fake_reply(agent_row, channel_id, history, on_tools_ready=None,
                          on_stream_start=None, on_token=None, **_kwargs):
        name = agent_row["name"]
        if name == "swarm":
            return {"reply": "handoff to @ledger", "tool_events": [], "usage": {}}
        return {"reply": "done", "tool_events": [], "usage": {}}

    monkeypatch.setattr(main.agent, "generate_reply", fake_reply)
    monkeypatch.setattr("backend.agent.generate_reply", fake_reply)
    _clear_rate()
    client.post("/api/channels/general/messages",
                json={"author": "uzeb", "body": "@swarm original objective please"}, headers=auth)
    _wait_for(lambda: len(_agent_replies(client, auth)) >= 2 or None,
              what="handoff")
    # The work session created for the recipient carries the original objective.
    sessions = client.get("/api/work", headers=auth).json()
    assert any(s.get("objective") == "@swarm original objective please" for s in sessions)


def test_approval_prevents_handoff(client, auth, monkeypatch):
    async def fake_reply(agent_row, channel_id, history, on_tools_ready=None,
                          on_stream_start=None, on_token=None, **_kwargs):
        name = agent_row["name"]
        if name == "swarm":
            if on_tools_ready is not None:
                await on_tools_ready([{"tool": "request_approval",
                                      "args": "{}", "result": "pending",
                                      "blocked": "review"}])
            return {"reply": "needs approval — @ledger should not run",
                    "tool_events": [{"tool": "request_approval", "blocked": "review"}],
                    "usage": {}}
        return {"reply": "should not appear", "tool_events": [], "usage": {}}

    monkeypatch.setattr(main.agent, "generate_reply", fake_reply)
    monkeypatch.setattr("backend.agent.generate_reply", fake_reply)
    _clear_rate()
    client.post("/api/channels/general/messages",
                json={"author": "uzeb", "body": "@swarm do the thing"}, headers=auth)
    _wait_for(lambda: len(_agent_replies(client, auth)) >= 1 or None,
              what="first reply")
    time.sleep(0.8)
    authors = [a for a, _ in _agent_replies(client, auth)]
    assert authors == ["swarm"], f"approval must stop handoff; got {authors}"