"""One settings object, one truthy() parser, one boot-time validation.

The bug this file guards is a product bug, not a style one. `SWARM_SYSTEM`
used to accept `on` while `SWARM_DEMO` did not, in three different files. An
operator who learned "on works" got a silent no-op from the other flag with no
error anywhere — so the tests here are written to pin *one* vocabulary, one
default per flag, and one loud failure at startup, rather than to pin any
particular parse of any one file.
"""

from __future__ import annotations

import json

import pytest

from backend import settings as settings_mod
from backend.settings import (
    FALSY_VALUES,
    TRUTHY_VALUES,
    SettingProblem,
    Settings,
    SettingsError,
    get_settings,
    reset_settings_cache,
    truthy,
    validate_settings,
)


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

@pytest.mark.parametrize("raw", ["1", "true", "TRUE", "True", "yes", "YES", "on", "ON", "t", "y"])
def test_every_accepted_spelling_reads_true(raw):
    """One vocabulary for every flag, so `on` cannot be a silent no-op."""
    assert truthy(raw, default=False) is True
    assert truthy(raw, default=True) is True


@pytest.mark.parametrize("raw", ["0", "false", "FALSE", "no", "NO", "off", "OFF", "f", "n"])
def test_the_negatives_read_false(raw):
    assert truthy(raw, default=False) is False
    assert truthy(raw, default=True) is False


def test_unset_and_blank_take_the_caller_default():
    """An unset flag must keep its documented default, not the parse's.

    SWARM_SYSTEM's default is off and SWARM_BROWSER's is on; both are unset in
    the environment, and polarity must not decide the answer.
    """
    assert truthy(None, default=False) is False
    assert truthy(None, default=True) is True
    assert truthy("", default=True) is True
    assert truthy("   ", default=False) is False


def test_an_unrecognised_value_falls_back_to_the_default():
    """`enabled` is not a spelling. It must not silently read as true."""
    assert truthy("enabled", default=False) is False
    assert truthy("enabled", default=True) is True


def test_the_two_vocabularies_do_not_overlap():
    """A spelling that is both true and false would be a coin flip."""
    assert not (TRUTHY_VALUES & FALSY_VALUES)


# ------------------------------------------------------ the old bug, fixed --

def test_on_means_on_for_both_system_and_demo(monkeypatch):
    """The exact regression: `on` worked for one flag and not the other."""
    monkeypatch.setenv("SWARM_SYSTEM", "on")
    monkeypatch.setenv("SWARM_DEMO", "on")
    parsed = get_settings()
    assert parsed.system_enabled() is True
    assert parsed.demo_mode() is True


@pytest.mark.parametrize("raw", ["1", "true", "yes", "on", "t", "y"])
def test_every_prior_spelling_still_works_for_system_and_demo(monkeypatch, raw):
    """Widen, never narrow: everything that worked before still works."""
    monkeypatch.setenv("SWARM_SYSTEM", raw)
    monkeypatch.setenv("SWARM_DEMO", raw)
    reset_settings_cache()
    parsed = get_settings()
    assert parsed.system_enabled() is True
    assert parsed.demo_mode() is True


@pytest.mark.parametrize("raw", ["0", "false", "no", "off", ""])
def test_the_off_values_agree_across_both_flags(monkeypatch, raw):
    monkeypatch.setenv("SWARM_SYSTEM", raw)
    monkeypatch.setenv("SWARM_DEMO", raw)
    reset_settings_cache()
    parsed = get_settings()
    assert parsed.system_enabled() is False, raw
    assert parsed.demo_mode() is False, raw


def test_defaults_unchanged_when_nothing_is_set():
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

@pytest.mark.parametrize("name", ["SWARM_SYSTEM", "SWARM_DEMO", "SWARM_SYSTEM_UNRESTRICTED"])
def test_a_bad_variable_is_reported_by_name(monkeypatch, name):
    monkeypatch.setenv(name, "enabled")
    problems = get_settings().problems()
    assert [p.variable for p in problems] == [name]
    assert "1/0" in problems[0].message
    # The problem object quotes the offending value, which is what makes the
    # message actionable, and SWARM_SECRET is never one of these fields.


def test_every_bad_variable_is_reported_at_once(monkeypatch):
    """One boot should say everything wrong, not one thing per restart."""
    monkeypatch.setenv("SWARM_SYSTEM", "enabled")
    monkeypatch.setenv("SWARM_DEMO", "sometimes")
    monkeypatch.setenv("SWARM_COMPUTER_PROVIDER", "cloud")
    with pytest.raises(SettingsError) as excinfo:
        validate_settings()
    names = {p.variable for p in excinfo.value.problems}
    assert names == {"SWARM_SYSTEM", "SWARM_DEMO", "SWARM_COMPUTER_PROVIDER"}


def test_the_error_text_tells_the_operator_what_to_type():
    error = SettingsError([SettingProblem("SWARM_DEMO", "enabled", "not a boolean — use 1/0, true/false, yes/no, on/off")])
    text = str(error)
    assert "SWARM_DEMO" in text
    assert "enabled" in text
    assert "yes/no" in text


def test_an_unknown_enum_lists_the_real_options(monkeypatch):
    monkeypatch.setenv("SWARM_COMPUTER_PROVIDER", "cloud")
    problem = get_settings().problems()[0]
    assert problem.variable == "SWARM_COMPUTER_PROVIDER"
    for option in ("local", "none", "fake"):
        assert option in problem.message


@pytest.mark.parametrize("raw", ["", "   "])
def test_blank_is_not_a_problem(monkeypatch, raw):
    """An empty env var is what a copied `.env.example` produces."""
    monkeypatch.setenv("SWARM_SYSTEM", raw)
    monkeypatch.setenv("SWARM_COMPUTER_PROVIDER", raw)
    assert get_settings().problems() == []


def test_validation_passes_when_nothing_is_set():
    assert validate_settings() is get_settings()


def test_a_secret_value_is_never_reported_as_a_problem(monkeypatch):
    """SWARM_SECRET has no vocabulary, so any value must pass validation."""
    monkeypatch.setenv("SWARM_SECRET", "not a boolean")
    assert get_settings().problems() == []


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

@pytest.mark.parametrize("raw", ["", "  ", "abc", "0", "-5", "1e400"])
def test_retention_keeps_everything_on_nonsense(monkeypatch, raw):
    """A typo must never read as "delete my transcript".

    This is the one setting where a lenient fallback *is* the safety property,
    so it must not be tightened into a boot failure.
    """
    monkeypatch.setenv("SWARM_RETENTION_DAYS", raw)
    reset_settings_cache()
    assert get_settings().effective_retention_days() is None, raw
    assert get_settings().problems() == [], raw


def test_retention_accepts_a_positive_number(monkeypatch):
    monkeypatch.setenv("SWARM_RETENTION_DAYS", "7")
    reset_settings_cache()
    assert get_settings().effective_retention_days() == 7


def test_busy_timeout_falls_back_and_never_goes_negative(monkeypatch):
    monkeypatch.setenv("SWARM_SQLITE_BUSY_TIMEOUT_MS", "not-a-number")
    reset_settings_cache()
    assert get_settings().busy_timeout_ms() == 5000
    monkeypatch.setenv("SWARM_SQLITE_BUSY_TIMEOUT_MS", "-1")
    reset_settings_cache()
    assert get_settings().busy_timeout_ms() == 0
    monkeypatch.setenv("SWARM_SQLITE_BUSY_TIMEOUT_MS", "1234")
    reset_settings_cache()
    assert get_settings().busy_timeout_ms() == 1234


@pytest.mark.parametrize("raw", ["", "abc"])
def test_gc_caps_keep_their_defaults_on_nonsense(monkeypatch, raw):
    monkeypatch.setenv("SWARM_GC_MAX_MB", raw)
    monkeypatch.setenv("SWARM_GC_MAX_AGE_HOURS", raw)
    reset_settings_cache()
    assert get_settings().effective_gc_max_mb() == 512.0
    assert get_settings().effective_gc_max_age_hours() == 72.0


@pytest.mark.parametrize("raw", ["-3", "0"])
def test_a_non_positive_gc_cap_is_refused(monkeypatch, raw):
    """These are deletion bounds, not preferences.

    `sweep_oversize` deletes until the total fits, so a negative cap makes
    every file in the sandbox qualify and a zero age deletes all of them. An
    unusable value therefore keeps the default instead of being obeyed.
    """
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

def test_db_reads_go_through_the_settings_object(monkeypatch):
    """The migrated call sites must agree with the object, not with a copy."""
    from backend import db as db_mod
    from backend import gc as gc_mod

    monkeypatch.setenv("SWARM_SQLITE_BUSY_TIMEOUT_MS", "1234")
    monkeypatch.setenv("SWARM_RETENTION_DAYS", "9")
    monkeypatch.setenv("SWARM_GC_MAX_MB", "7")
    reset_settings_cache()
    assert db_mod.busy_timeout_ms() == 1234
    assert db_mod.retention_days() == 9
    assert gc_mod._max_bytes() == 7 * 1024 * 1024


def test_demo_mode_agrees_across_every_reader(monkeypatch):
    """db and settings must not disagree about whether demo is on."""
    from backend import db as db_mod

    monkeypatch.setenv("SWARM_DEMO", "on")
    reset_settings_cache()
    assert db_mod._demo_mode() is True
    assert get_settings().demo_mode() is True


def test_computer_provider_falls_back_to_local(monkeypatch):
    """An unknown provider must not quietly boot without a computer host."""
    from backend import computer_providers as computers

    monkeypatch.setenv("SWARM_COMPUTER_PROVIDER", "cloud")
    reset_settings_cache()
    assert computers.get_provider() == "local"
    assert [p.variable for p in get_settings().problems()] == ["SWARM_COMPUTER_PROVIDER"]


# ------------------------------------------------------------- origins --

def test_allowed_origins_drops_a_bare_wildcard(monkeypatch):
    """The guard and CORSMiddleware must agree: "*" reaches neither."""
    from backend.security import allowed_origins

    monkeypatch.setenv("SWARM_ALLOWED_ORIGINS", "*")
    reset_settings_cache()
    assert allowed_origins() == set()

    monkeypatch.setenv("SWARM_ALLOWED_ORIGINS", "http://a.example, *, http://b.example")
    reset_settings_cache()
    assert allowed_origins() == {"http://a.example", "http://b.example"}


def test_allowed_origins_defaults_to_localhost_when_unset():
    from backend.security import DEFAULT_ALLOWED_ORIGINS, allowed_origins

    assert allowed_origins() == set(DEFAULT_ALLOWED_ORIGINS)


def test_exposure_detection(monkeypatch):
    """Unset means loopback-only, because that is uvicorn's own default."""
    assert get_settings().exposed_on_non_loopback() is False
    for host in ("127.0.0.1", "localhost", "::1"):
        monkeypatch.setenv("SWARM_HOST", host)
        reset_settings_cache()
        assert get_settings().exposed_on_non_loopback() is False, host
    monkeypatch.setenv("SWARM_HOST", "0.0.0.0")
    reset_settings_cache()
    assert get_settings().exposed_on_non_loopback() is True


@pytest.mark.parametrize("value", ["local", "none", "fake"])
def test_every_documented_computer_provider_still_works(monkeypatch, value):
    from backend import computer_providers as computers

    monkeypatch.setenv("SWARM_COMPUTER_PROVIDER", value)
    reset_settings_cache()
    assert computers.get_provider() == value
    assert get_settings().problems() == []


# -------------------------------------------------------------- caching --

def test_the_cache_follows_the_environment_not_the_import(monkeypatch):
    """Tests and operators change the environment after import; the object
    must not serve a stale parse."""
    assert get_settings().system_enabled() is False
    monkeypatch.setenv("SWARM_SYSTEM", "1")
    assert get_settings().system_enabled() is True
    monkeypatch.setenv("SWARM_SYSTEM", "0")
    assert get_settings().system_enabled() is False


def test_repeated_reads_return_the_same_object():
    assert get_settings() is get_settings()


def test_settings_is_instantiable_without_any_environment():
    """`Settings()` must not require SWARM_* to exist at all."""
    assert Settings().problems() == []


def test_settings_snapshot_never_carries_a_secret(monkeypatch):
    """The snapshot is designed to be pasted into a public issue."""
    monkeypatch.setenv("SWARM_SECRET", "the-real-secret-value")
    reset_settings_cache()
    snapshot = settings_mod.settings_snapshot()
    assert "the-real-secret-value" not in json.dumps(snapshot)
    row = next(v for v in snapshot["variables"] if v["variable"] == "SWARM_SECRET")
    assert row == {"variable": "SWARM_SECRET", "set": True, "value": None}
    assert snapshot["effective"]["secret_configured"] is True
