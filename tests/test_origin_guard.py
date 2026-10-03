"""The origin guard must stay strict, and must stop being the thing that
blocks a legitimate local install.

Before: any browser Origin outside four hardcoded localhost URLs got a bare
403 "origin not allowed", so reaching Swarm at http://my-box.lan:8000 or on
any port other than 8000/5173 failed with no hint about what to change. That
is the most common way a self-hosted local install looks broken.

The fix allows an origin whose host:port is exactly the host:port the request
was sent to. That is not cross-origin - it is the browser reporting that the
page came from the server now answering it. Everything else still needs
SWARM_ALLOWED_ORIGINS.
"""
from __future__ import annotations

import pytest

from backend.security import allowed_origins, origin_allowed


@pytest.fixture(autouse=True)
def _no_env(monkeypatch):
    monkeypatch.delenv("SWARM_ALLOWED_ORIGINS", raising=False)
    yield


def test_configured_origin_is_allowed():
    assert origin_allowed("http://localhost:8000") is True
    assert origin_allowed("http://127.0.0.1:5173") is True


def test_trailing_slash_tolerated():
    assert origin_allowed("http://localhost:8000/") is True


def test_foreign_origin_is_rejected():
    """The guard must not become a free pass."""
    assert origin_allowed("https://evil.example") is False


def test_same_host_is_allowed():
    """A self-hoster reached by machine name is not a cross-origin caller."""
    assert origin_allowed("http://my-box.lan:8000", "my-box.lan:8000") is True
    assert origin_allowed("http://192.168.1.20:8000", "192.168.1.20:8000") is True


def test_same_host_requires_matching_port():
    """Port is part of an origin. Different port is still cross-origin."""
    assert origin_allowed("http://my-box.lan:5173", "my-box.lan:8000") is False


def test_same_host_requires_matching_hostname():
    assert origin_allowed("http://evil.example:8000", "my-box.lan:8000") is False


def test_localhost_and_loopback_are_not_interchangeable_via_same_host():
    """A browser treats these as different origins; so must the same-host path.

    Both are in the default allowlist, so this exercises the same-host branch
    with a host that is NOT allowlisted, where the two must not match.
    """
    assert origin_allowed("http://localhost:9999", "127.0.0.1:9999") is False


def test_no_request_host_falls_back_to_allowlist_only():
    assert origin_allowed("http://my-box.lan:8000") is False


def test_malformed_origin_is_rejected_not_crashed():
    for bad in ("not-a-url", "://", "http://", "http://:8000", "///"):
        assert origin_allowed(bad, "my-box.lan:8000") in (True, False)


def test_missing_origin_is_allowed():
    """CLI, curl and the WebSocket handshake send no Origin."""
    assert origin_allowed(None) is True
    assert origin_allowed("") is True


def test_defaults_are_the_four_localhost_urls():
    assert allowed_origins() == {
        "http://localhost:8000",
        "http://127.0.0.1:8000",
        "http://localhost:5173",
        "http://127.0.0.1:5173",
    }


def test_env_config_extends_and_wildcard_is_dropped(monkeypatch):
    monkeypatch.setenv("SWARM_ALLOWED_ORIGINS", "http://a.test, http://b.test , *")
    assert allowed_origins() == {"http://a.test", "http://b.test"}
    assert origin_allowed("http://a.test") is True
    # "*" must never be a literal match - it never matches.
    assert origin_allowed("*") is False