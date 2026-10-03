"""The write-rate limiter is bounded (docs/SECURITY-REVIEW.md finding 9).

`_last_write` is keyed by the caller's handle, and registering is open, so an
unauthenticated caller can mint unlimited distinct handles and drive unbounded
growth of a process-lifetime dict. The fix must not change what the limiter
*does*: 500ms between writes per handle is the rate limit AGENTS.md §3 and the
whole suite depends on. So these tests pin both halves — the behaviour the
tests already relied on, and the bound that stops the growth.
"""

from __future__ import annotations

import time

from backend import main as main_mod


def _clear():
    main_mod._last_write.clear()


# ------------------------------------------------------- existing behaviour --

def test_two_writes_inside_the_window_are_throttled():
    """The 500ms-per-handle limit the rest of the suite relies on."""
    _clear()
    assert main_mod._rate_limited("uzeb") is False
    assert main_mod._rate_limited("uzeb") is True


def test_two_different_handles_are_independent():
    """Keyed per handle: one person's spam is not another's problem."""
    _clear()
    assert main_mod._rate_limited("alice") is False
    assert main_mod._rate_limited("bob") is False
    assert main_mod._rate_limited("alice") is True


def test_the_window_reopens_after_the_limit(monkeypatch):
    """Throttled now, allowed once the window has passed."""
    _clear()
    assert main_mod._rate_limited("uzeb") is False
    assert main_mod._rate_limited("uzeb") is True
    time.sleep(main_mod.RATE_LIMIT_SECONDS + 0.05)
    assert main_mod._rate_limited("uzeb") is False


def test_a_throttled_call_does_not_extend_the_window():
    """A rejected write must not count as a write.

    Otherwise a client hammering the limiter would keep pushing its own
    deadline forward and never be allowed through.
    """
    _clear()
    assert main_mod._rate_limited("uzeb") is False
    first_seen = main_mod._last_write["uzeb"]
    for _ in range(5):
        assert main_mod._rate_limited("uzeb") is True
    assert main_mod._last_write["uzeb"] == first_seen


# ---------------------------------------------------------------- the bound --

def test_the_map_is_bounded_by_the_entry_cap(monkeypatch):
    """A burst of fresh handles cannot grow the dict without limit."""
    _clear()
    monkeypatch.setattr(main_mod, "_LAST_WRITE_MAX_ENTRIES", 50)
    for i in range(500):
        main_mod._rate_limited(f"handle-{i}")
    assert len(main_mod._last_write) <= 50


def test_stale_entries_are_swept_on_write(monkeypatch):
    """An entry older than the TTL cannot throttle anything, so it goes.

    This is the bound that matters over a long-lived process: without it the
    dict keeps every handle ever seen, capped or not.
    """
    _clear()
    monkeypatch.setattr(main_mod, "_LAST_WRITE_TTL_SECONDS", 0.05)
    main_mod._rate_limited("uzeb")
    assert "uzeb" in main_mod._last_write
    time.sleep(0.08)
    main_mod._rate_limited("someone-else")
    assert "uzeb" not in main_mod._last_write


def test_eviction_does_not_weaken_the_limit_for_a_live_handle(monkeypatch):
    """The sweep must not un-throttle a handle that is still inside its window.

    A 60s TTL is longer than the 0.5s window, so this holds by construction —
    but it is the property the change could have broken, so it is pinned.
    """
    _clear()
    monkeypatch.setattr(main_mod, "_LAST_WRITE_TTL_SECONDS", 60.0)
    assert main_mod._rate_limited("uzeb") is False
    main_mod._rate_limited("other")
    main_mod._rate_limited("third")
    assert main_mod._rate_limited("uzeb") is True


