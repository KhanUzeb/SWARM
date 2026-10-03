"""Host-system tools are opt-in: SWARM_SYSTEM defaults to off.

Host-system tools run commands on the machine that runs the backend, so the
secure default is disabled. Enabling them on a non-loopback bind must warn
loudly at startup.
"""
from __future__ import annotations

import pytest

from backend.tools import system as system_mod


@pytest.fixture(autouse=True)
def _clean_system_env(monkeypatch):
    for name in (
        "SWARM_SYSTEM",
        "SWARM_SYSTEM_ROOT",
        "SWARM_SYSTEM_UNRESTRICTED",
        "SWARM_HOST",
        "SWARM_BIND",
    ):
        monkeypatch.delenv(name, raising=False)
    system_mod.reset_runtime()
    yield
    system_mod.reset_runtime()


def test_host_tools_off_by_default():
    assert system_mod.enabled() is False


@pytest.mark.parametrize("raw", ["1", "true", "TRUE", "yes", "on"])
def test_explicit_opt_in_enables(monkeypatch, raw):
    monkeypatch.setenv("SWARM_SYSTEM", raw)
    assert system_mod.enabled() is True


@pytest.mark.parametrize("raw", ["0", "false", "no", "off", ""])
def test_explicit_opt_out_disables(monkeypatch, raw):
    monkeypatch.setenv("SWARM_SYSTEM", raw)
    assert system_mod.enabled() is False


def test_no_warning_when_disabled(monkeypatch):
    monkeypatch.setenv("SWARM_HOST", "0.0.0.0")
    assert system_mod.warn_if_exposed() is None


def test_no_warning_on_loopback(monkeypatch):
    monkeypatch.setenv("SWARM_SYSTEM", "1")
    monkeypatch.setenv("SWARM_HOST", "127.0.0.1")
    assert system_mod.warn_if_exposed() is None


def test_warns_when_enabled_on_public_bind(monkeypatch):
    monkeypatch.setenv("SWARM_SYSTEM", "1")
    monkeypatch.setenv("SWARM_HOST", "0.0.0.0")
    message = system_mod.warn_if_exposed()
    assert message is not None
    assert "SWARM_SYSTEM=0" in message
    assert "non-loopback" in message


def test_reset_runtime_clears_warning_latch():
    """reset_runtime must let a later hydrate_root re-evaluate the warning."""
    system_mod._warned = False
    system_mod.reset_runtime()
    assert system_mod._warned is False