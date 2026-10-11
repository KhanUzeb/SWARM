"""One settings object, one truthy() parser, one boot-time validation.

The bug this file guards is a product bug, not a style one. `SWARM_SYSTEM`
used to accept `on` while `SWARM_DEMO` did not, in three different files. An
operator who learned "on works" got a silent no-op from the other flag with no
error anywhere — so the tests here are written to pin *one* vocabulary, one
default per flag, and one loud failure at startup, rather than to pin any
particular parse of any one file.

Every table below is a loop inside one test on purpose: the contract is "the
whole vocabulary means the same thing everywhere", and splitting it across
dozens of near-identical functions made that harder to read, not easier.
"""

from __future__ import annotations

import json

import pytest

from backend import settings as settings_mod
from backend.settings import (
    FALSY_VALUES,
    TRUTHY_VALUES,
    Settings,
    SettingsError,
    get_settings,
    reset_settings_cache,
    truthy,
    validate_settings,
)

# The vocabularies must stay a superset of these — widening is fine, silently
# dropping a spelling is not.
_TRUTHY = ("1", "true", "TRUE", "True", "yes", "YES", "on", "ON", "t", "y")
_FALSY = ("0", "false", "FALSE", "no", "NO", "off", "OFF", "f", "n")


@pytest.fixture(autouse=True)
def _clean_settings_env(monkeypatch):
    """Every managed variable unset, and the memoised parse dropped.

    Without the reset the settings cache would keep a value from a previous
    test, which is precisely the class of bug this file exists to prevent.
    """
    for name in settings_mod.MANAGED_ENV_VARS:
        monkeypatch.delenv(name, raising=False)
    reset_settings_cache()
    yield
    reset_settings_cache()


# --------------------------------------------------------------- truthy --

def test_truthy_vocabulary_is_one_shared_vocabulary():
    """One vocabulary for every flag, so `on` cannot be a silent no-op.

    A spelling that is both true and false would be a coin flip, so the two
    sets are also checked for overlap.
    """
    assert {v.lower() for v in _TRUTHY} <= TRUTHY_VALUES, (
        "a spelling the product accepts went missing"
    )
    assert {v.lower() for v in _FALSY} <= FALSY_VALUES, (
        "a spelling the product accepts went missing"
    )
    assert not (TRUTHY_VALUES & FALSY_VALUES), "a spelling reads both true and false"

    for raw in _TRUTHY:
        assert truthy(raw, default=False) is True, raw
        assert truthy(raw, default=True) is True, raw
    for raw in _FALSY:
        assert truthy(raw, default=False) is False, raw
        assert truthy(raw, default=True) is False, raw
    # Unset and blank take the caller default, not the parse's.
    for raw in (None, "", "   "):
        assert truthy(raw, default=True) is True, repr(raw)
        assert truthy(raw, default=False) is False, repr(raw)
    # An unrecognised value must not silently read as true.
    assert truthy("enabled", default=False) is False
    assert truthy("enabled", default=True) is True


def test_every_boolean_flag_agrees_on_every_spelling(monkeypatch):
    """The exact regression: `on` worked for one flag and not the other.

    Polarisation must not decide the answer — both flags are off by default
    and both must honour the same spellings.
    """
    monkeypatch.setenv("SWARM_SYSTEM", "on")
    monkeypatch.setenv("SWARM_DEMO", "on")
    reset_settings_cache()
    parsed = get_settings()
    assert parsed.system_enabled() is True
    assert parsed.demo_mode() is True

    for flag in ("SWARM_SYSTEM", "SWARM_DEMO"):
        for raw in _TRUTHY + _FALSY + ("",):
            for name in ("SWARM_SYSTEM", "SWARM_DEMO"):
                # Both flags carry the same spelling, so a per-flag parse
                # difference cannot hide behind the other flag's value.
                monkeypatch.setenv(name, raw)
            reset_settings_cache()
            parsed = get_settings()
            expected = truthy(raw, default=False)
            assert parsed.system_enabled() is expected, (flag, raw)
            assert parsed.demo_mode() is expected, (flag, raw)


def test_the_shipped_defaults_are_a_contract():
    """The shipped defaults are a contract, not an opinion."""
    parsed = get_settings()
    assert parsed.system_enabled() is False      # host tools opt-in
    assert parsed.system_unrestricted_ok() is False
    assert parsed.demo_mode() is False
    assert parsed.browser_enabled() is True     # inverted: unset means on
    assert parsed.computer() == "local"
    assert parsed.busy_timeout_ms() == 5000
    assert parsed.effective_retention_days() is None
    assert parsed.effective_gc_max_mb() == 512.0
    assert parsed.effective_gc_max_age_hours() == 72.0
    assert parsed.secret_configured() is False
    assert parsed.exposed_on_non_loopback() is False
    assert parsed.origin_list() == []


# ---------------------------------------------------------- validation --

_STRICT_FLAGS = ("SWARM_SYSTEM", "SWARM_DEMO", "SWARM_SYSTEM_UNRESTRICTED")


def test_a_bad_variable_is_reported_by_name_with_its_options(monkeypatch):
    """The problem names itself, quotes the value, and lists the real options.

    The quoted value is what makes the message actionable, and SWARM_SECRET is
    never one of these fields (it has no vocabulary, so any value passes).
    """
    for name in _STRICT_FLAGS:
        for other in _STRICT_FLAGS + ("SWARM_COMPUTER_PROVIDER",):
            if other != name:
                monkeypatch.delenv(other, raising=False)
        monkeypatch.setenv(name, "enabled")
        reset_settings_cache()
        problems = get_settings().problems()
        assert [p.variable for p in problems] == [name], problems
        assert "1/0" in problems[0].message, problems[0].message
        # The value is quoted on the object, which is what makes the message
        # actionable without the operator guessing which spelling they used.
        assert problems[0].value == "enabled", problems[0]

    for name in _STRICT_FLAGS:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("SWARM_COMPUTER_PROVIDER", "cloud")
    reset_settings_cache()
    problem = get_settings().problems()[0]
    assert problem.variable == "SWARM_COMPUTER_PROVIDER"
    for option in ("local", "none", "fake"):
        assert option in problem.message, problem.message


def test_every_bad_variable_is_reported_at_once(monkeypatch):
    """One boot should say everything wrong, not one thing per restart.

    The same error text the operator reads is asserted here: it must name the
    variable, quote the offending value, and offer the vocabulary.
    """
    monkeypatch.setenv("SWARM_SYSTEM", "enabled")
    monkeypatch.setenv("SWARM_DEMO", "sometimes")
    monkeypatch.setenv("SWARM_COMPUTER_PROVIDER", "cloud")
    reset_settings_cache()
    with pytest.raises(SettingsError) as excinfo:
        validate_settings()
    names = {p.variable for p in excinfo.value.problems}
    assert names == {"SWARM_SYSTEM", "SWARM_DEMO", "SWARM_COMPUTER_PROVIDER"}
    text = str(excinfo.value)
    assert "SWARM_DEMO" in text
    assert "enabled" in text
    assert "yes/no" in text


def test_blank_and_secret_values_never_stop_the_boot(monkeypatch):
    """An empty env var is what a copied `.env.example` produces.

    SWARM_SECRET has no vocabulary either, so any value must pass.
    """
    for raw in ("", "   "):
        monkeypatch.setenv("SWARM_SYSTEM", raw)
        monkeypatch.setenv("SWARM_COMPUTER_PROVIDER", raw)
        reset_settings_cache()
        assert get_settings().problems() == [], raw

    monkeypatch.setenv("SWARM_SECRET", "not a boolean")
    reset_settings_cache()
    assert get_settings().problems() == []
    assert validate_settings() is get_settings()


def test_startup_actually_stops_on_a_bad_value():
    """The lifespan refuses to boot, not just the helper.

    This is the whole point of validating at startup: a self-hoster has no
    other signal that their variable was ignored.
    """
    import os

    from fastapi.testclient import TestClient

    import backend.main as main_mod

    previous = os.environ.get("SWARM_SYSTEM")
    os.environ["SWARM_SYSTEM"] = "enabled"
    reset_settings_cache()
    try:
        with pytest.raises(SettingsError) as excinfo:
            with TestClient(main_mod.app):
                pass
        assert "SWARM_SYSTEM" in str(excinfo.value)
    finally:
        if previous is None:
            os.environ.pop("SWARM_SYSTEM", None)
        else:
            os.environ["SWARM_SYSTEM"] = previous
        reset_settings_cache()


# ------------------------------------------------------------- lenient --

def test_lenient_settings_never_read_nonsense_as_a_bound(monkeypatch):
    """A typo must never read as "delete my transcript" or "wait zero seconds".

    This is the one place where a lenient fallback *is* the safety property,
    so it must not be tightened into a boot failure. The GC caps get the same
    treatment for a sharper reason: `sweep_oversize` deletes until the total
    fits, so a negative cap makes every file in the sandbox qualify and a zero
    age deletes all of them.
    """
    for raw in ("", "  ", "abc", "0", "-5", "1e400"):
        monkeypatch.setenv("SWARM_RETENTION_DAYS", raw)
        reset_settings_cache()
        assert get_settings().effective_retention_days() is None, raw
        assert get_settings().problems() == [], raw
    monkeypatch.setenv("SWARM_RETENTION_DAYS", "7")
    reset_settings_cache()
    assert get_settings().effective_retention_days() == 7

    for raw, expected in (("not-a-number", 5000), ("-1", 0), ("1234", 1234)):
        monkeypatch.setenv("SWARM_SQLITE_BUSY_TIMEOUT_MS", raw)
        reset_settings_cache()
        assert get_settings().busy_timeout_ms() == expected, raw

    for raw in ("", "abc", "-3", "0"):
        monkeypatch.setenv("SWARM_GC_MAX_MB", raw)
        monkeypatch.setenv("SWARM_GC_MAX_AGE_HOURS", raw)
        reset_settings_cache()
        assert get_settings().effective_gc_max_mb() == 512.0, raw
        assert get_settings().effective_gc_max_age_hours() == 72.0, raw


def test_browser_keeps_its_inverted_lenient_contract(monkeypatch):
    """`SWARM_BROWSER` is not on the strict boolean list, on purpose.

    Its historical rule is "anything not in {0,false,no,off} means on", so
    SWARM_BROWSER=enabled has always been true and must stay true. Tightening
    it to the shared vocabulary would be the narrowing this file forbids.
    """
    monkeypatch.setenv("SWARM_BROWSER", "enabled")
    reset_settings_cache()
    assert get_settings().browser_enabled() is True
    for raw in ("0", "false", "no", "off"):
        monkeypatch.setenv("SWARM_BROWSER", raw)
        reset_settings_cache()
        assert get_settings().browser_enabled() is False, raw
    assert get_settings().problems() == []


# ---------------------------------------------------------- db / gc reads --

def test_the_migrated_call_sites_read_the_settings_object(monkeypatch):
    """The migrated call sites must agree with the object, not with a copy.

    db, gc and the computer-provider fallback each own a value the operator
    set; if any of them parsed it a second way, `swarm doctor` would report
    one thing while the code did another.
    """
    from backend import computer_providers as computers
    from backend import db as db_mod
    from backend import gc as gc_mod

    monkeypatch.setenv("SWARM_SQLITE_BUSY_TIMEOUT_MS", "1234")
    monkeypatch.setenv("SWARM_RETENTION_DAYS", "9")
    monkeypatch.setenv("SWARM_GC_MAX_MB", "7")
    monkeypatch.setenv("SWARM_DEMO", "on")
    monkeypatch.setenv("SWARM_COMPUTER_PROVIDER", "cloud")
    reset_settings_cache()
    assert db_mod.busy_timeout_ms() == 1234
    assert db_mod.retention_days() == 9
    assert gc_mod._max_bytes() == 7 * 1024 * 1024
    assert db_mod._demo_mode() is True
    # An unknown provider must not quietly boot without a computer host.
    assert computers.get_provider() == "local"
    assert [p.variable for p in get_settings().problems()] == ["SWARM_COMPUTER_PROVIDER"]


# ------------------------------------------------------------- origins --

def test_allowed_origins_drops_a_wildcard_and_defaults_to_localhost(monkeypatch):
    """The guard and CORSMiddleware must agree: "*" reaches neither."""
    from backend.security import DEFAULT_ALLOWED_ORIGINS, allowed_origins

    monkeypatch.setenv("SWARM_ALLOWED_ORIGINS", "*")
    reset_settings_cache()
    assert allowed_origins() == set()

    monkeypatch.setenv("SWARM_ALLOWED_ORIGINS", "http://a.example, *, http://b.example")
    reset_settings_cache()
    assert allowed_origins() == {"http://a.example", "http://b.example"}

    monkeypatch.delenv("SWARM_ALLOWED_ORIGINS", raising=False)
    reset_settings_cache()
    assert allowed_origins() == set(DEFAULT_ALLOWED_ORIGINS)


def test_exposure_detection_and_every_documented_computer_provider(monkeypatch):
    """Unset means loopback-only, because that is uvicorn's own default.

    The three documented computer providers must all stay bootable.
    """
    from backend import computer_providers as computers

    assert get_settings().exposed_on_non_loopback() is False
    for host in ("127.0.0.1", "localhost", "::1"):
        monkeypatch.setenv("SWARM_HOST", host)
        reset_settings_cache()
        assert get_settings().exposed_on_non_loopback() is False, host
    monkeypatch.setenv("SWARM_HOST", "0.0.0.0")
    reset_settings_cache()
    assert get_settings().exposed_on_non_loopback() is True

    for value in ("local", "none", "fake"):
        monkeypatch.setenv("SWARM_COMPUTER_PROVIDER", value)
        reset_settings_cache()
        assert computers.get_provider() == value, value
        assert get_settings().problems() == [], value


# -------------------------------------------------------------- caching --

def test_the_cache_follows_the_environment_not_the_import(monkeypatch):
    """Tests and operators change the environment after import; the object
    must not serve a stale parse, and repeated reads stay the same object."""
    assert get_settings().system_enabled() is False
    monkeypatch.setenv("SWARM_SYSTEM", "1")
    assert get_settings().system_enabled() is True
    monkeypatch.setenv("SWARM_SYSTEM", "0")
    assert get_settings().system_enabled() is False
    assert get_settings() is get_settings()


def test_settings_is_instantiable_and_its_snapshot_never_carries_a_secret(monkeypatch):
    """`Settings()` must not require SWARM_* to exist at all.

    The snapshot is designed to be pasted into a public issue, so a secret
    reports set/not-set only.
    """
    assert Settings().problems() == []
    monkeypatch.setenv("SWARM_SECRET", "the-real-secret-value")
    reset_settings_cache()
    snapshot = settings_mod.settings_snapshot()
    assert "the-real-secret-value" not in json.dumps(snapshot)
    row = next(v for v in snapshot["variables"] if v["variable"] == "SWARM_SECRET")
    assert row == {"variable": "SWARM_SECRET", "set": True, "value": None}
    assert snapshot["effective"]["secret_configured"] is True
