"""Behavior tests for honest token-budget enforcement.

Completeness is per provider call (including any closing round), but a known
nonnegative token count is a lower bound on spend: once reported usage reaches
the ceiling the run makes no further provider call, even when other fields or
rounds are missing. The check happens between calls, so one in-flight call may
exceed the ceiling before its usage is known — no overspend guarantee is made.
All tests are offline and deterministic.
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import backend.agent as agent_mod


def _run(coro):
    return asyncio.run(coro)


class _Usage:
    """The provider-neutral shape agent.py reads from stream chunks."""

    def __init__(self, prompt, completion):
        self.prompt_tokens = prompt
        self.completion_tokens = completion


def _drive(monkeypatch, usages, *, max_steps: int = 2, max_tokens: int = 1000):
    """Return (result, counts); usages[i] is provider call i's usage value."""
    calls = {"complete": 0, "tool": 0}

    async def fake_complete(client, model, messages, on_stream_start, on_token,
                            *, tool_schemas=None):
        idx = calls["complete"]
        calls["complete"] += 1
        if on_stream_start is not None:
            await on_stream_start()
        usage = usages[idx] if idx < len(usages) else None
        # Tool-bearing rounds pass schemas; a closing round passes None.
        if tool_schemas:
            return (
                "",
                [{"id": f"c{idx}", "name": "list_workspace", "arguments": "{}"}],
                usage,
            )
        if on_token is not None:
            await on_token("closing summary")
        return "closing summary", [], usage

    async def fake_tool(name, args, **kwargs):
        calls["tool"] += 1
        return f"result-{calls['tool']}"

    monkeypatch.setattr(agent_mod, "_complete_stream", fake_complete)
    monkeypatch.setattr(agent_mod, "_execute_tool", fake_tool)

    result = _run(agent_mod._run_with_client(
        object(), "test-model",
        {"name": "swarm", "max_steps": max_steps, "max_seconds": 300.0,
         "max_tokens": max_tokens, "max_tool_calls": 6},
        "dm-swarm", [{"role": "user", "content": "ping"}],
        use_tools=True, allowed=["list_workspace"],
        on_tools_ready=None, on_stream_start=None, on_token=None, trace=None,
    ))
    return result, calls


def test_no_provider_reports_usage_is_incomplete(monkeypatch):
    """Zero counters with no reports are unknown, never a measured spend."""
    result, calls = _drive(monkeypatch, [None, None, None])
    assert result["usage"] == {
        "prompt_tokens": 0, "completion_tokens": 0, "usage_complete": False,
        "rounds_seen": 3, "rounds_reported": 0,
    }
    assert "this reply hit its step budget" in result["reply"]
    assert (
        "token usage was incomplete (0/3 provider calls reported usage), "
        "so the token budget was not enforced"
    ) in result["reply"]
    assert (calls["complete"], calls["tool"]) == (3, 2)


def test_early_report_stays_partial_when_later_rounds_omit(monkeypatch):
    """One reporting round cannot make a mixed sequence look complete."""
    result, _ = _drive(monkeypatch, [_Usage(200, 0), None, None])
    assert result["usage"]["prompt_tokens"] == 200
    assert result["usage"]["usage_complete"] is False
    assert result["usage"]["rounds_seen"] == 3
    assert result["usage"]["rounds_reported"] == 1
    assert "(1/3 provider calls reported usage)" in result["reply"]
    assert "token budget was not enforced" in result["reply"]


def test_late_reports_stay_partial_when_an_earlier_round_omitted(monkeypatch):
    """A later report must not flip completeness back to true."""
    result, _ = _drive(monkeypatch, [None, _Usage(300, 0), _Usage(50, 0)])
    assert result["usage"]["prompt_tokens"] == 350
    assert result["usage"]["usage_complete"] is False
    assert result["usage"]["rounds_seen"] == 3
    assert result["usage"]["rounds_reported"] == 2
    assert "(2/3 provider calls reported usage)" in result["reply"]


def test_all_rounds_including_closing_report_is_complete(monkeypatch):
    """Only an all-reported run has a complete total; closing usage is counted."""
    result, _ = _drive(monkeypatch, [
        _Usage(100, 0), _Usage(100, 0), _Usage(20, 0),
    ])
    assert result["usage"] == {
        "prompt_tokens": 220, "completion_tokens": 0, "usage_complete": True,
        "rounds_seen": 3, "rounds_reported": 3,
    }
    assert "this reply hit its step budget" in result["reply"]
    assert "token usage" not in result["reply"]
    assert "not enforced" not in result["reply"]


def test_malformed_field_still_stops_on_known_tokens_with_no_extra_call(monkeypatch):
    """Known tokens over the cap stop the run even when a field is missing."""
    result, calls = _drive(monkeypatch, [_Usage(1200, "not-a-number"), None])
    assert calls == {"complete": 1, "tool": 1}  # no closing call after exhaustion
    assert result["usage"] == {
        "prompt_tokens": 1200, "completion_tokens": 0, "usage_complete": False,
        "rounds_seen": 1, "rounds_reported": 0,
    }
    assert "token budget" in result["reply"]
    assert "1000" in result["reply"]
    assert "reported tokens" in result["reply"]
    assert "token totals are partial (0/1 provider calls reported usage)" in result["reply"]
    assert "not enforced" not in result["reply"]
    assert "no further provider call" in result["reply"]


def test_mixed_rounds_over_cap_stop_immediately(monkeypatch):
    """A completed round plus a partial one crosses the cap: stop at once."""
    result, calls = _drive(monkeypatch, [_Usage(700, 0), _Usage(400, None)])
    assert calls == {"complete": 2, "tool": 2}
    assert result["usage"] == {
        "prompt_tokens": 1100, "completion_tokens": 0, "usage_complete": False,
        "rounds_seen": 2, "rounds_reported": 1,
    }
    assert "stopped after 1100 reported tokens" in result["reply"]
    assert "token totals are partial (1/2 provider calls reported usage)" in result["reply"]
    assert "no further provider call" in result["reply"]


def test_full_reports_over_cap_stop_without_a_closing_call(monkeypatch):
    """Even a fully reported run makes no extra call after hitting the cap."""
    result, calls = _drive(monkeypatch, [_Usage(1500, 0)])
    assert calls == {"complete": 1, "tool": 1}
    assert result["usage"] == {
        "prompt_tokens": 1500, "completion_tokens": 0, "usage_complete": True,
        "rounds_seen": 1, "rounds_reported": 1,
    }
    assert "token budget" in result["reply"]
    assert "not enforced" not in result["reply"]
    assert "token usage" not in result["reply"]
    assert "no further provider call" in result["reply"]


def test_malformed_token_fields_are_incomplete_not_zero():
    """Unknown fields never contribute a fabricated zero to the total."""
    cases = [
        (None, None),                  # no usage object at all
        (_Usage(1500, "bad"), False),  # malformed completion
        (SimpleNamespace(prompt_tokens=9), False),  # missing attribute
        (_Usage(None, 5), False),      # missing prompt field
        (_Usage(-1, 5), False),        # negative prompt field
        (_Usage(True, 5), False),      # boolean is not a token count
        (_Usage("1500", 5), False),    # numeric string is not a count
    ]
    for usage, reported in cases:
        bucket = agent_mod.empty_usage()
        assert "usage_reported" not in bucket
        assert agent_mod._usage_from(usage, bucket) is bool(reported)
        assert bucket["rounds_seen"] == 1
        assert bucket["usage_complete"] is False

    # Only the known field of a partial usage object is added.
    partial = agent_mod.empty_usage()
    agent_mod._usage_from(_Usage(1500, "bad"), partial)
    assert partial["prompt_tokens"] == 1500
    assert partial["completion_tokens"] == 0  # not a measured zero

    # A reported zero in both fields is a genuine measurement.
    zero = agent_mod.empty_usage()
    assert agent_mod._usage_from(_Usage(0, 0), zero) is True
    assert agent_mod._usage_is_complete(zero)


def test_fractional_and_nonfinite_token_counts_are_rejected():
    """int(float) truncation must never turn a malformed count into spend."""
    rejected = [
        2.7, -2.7, float("nan"), float("inf"), float("-inf"),
        "1500", "2.0", True, False, None, -1,
    ]
    for value in rejected:
        assert agent_mod._token_field(value) is None, value

    assert agent_mod._token_field(0) == 0
    assert agent_mod._token_field(1500) == 1500
    # An integral float is still an exact count, not a rounded estimate.
    assert agent_mod._token_field(1500.0) == 1500

    # A fractional field cannot trip the cap by being truncated upward or
    # counted as a real measurement.
    bucket = agent_mod.empty_usage()
    assert agent_mod._usage_from(_Usage(999.9, 5), bucket) is False
    assert bucket["prompt_tokens"] == 0
    assert bucket["completion_tokens"] == 5
    assert bucket["usage_complete"] is False

    bucket = agent_mod.empty_usage()
    assert agent_mod._usage_from(_Usage(999.0, 5), bucket) is True
    assert bucket["prompt_tokens"] == 999
