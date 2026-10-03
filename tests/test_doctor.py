"""`swarm doctor` — the local health report.

Offline and deterministic: the only network probe is opt-in and even that is
mocked here, because a test suite that depends on someone's wifi is a test
suite that fails in CI. The tests are written around what the operator actually
needs — does it work, and if not what do I change — rather than around the
internal shape of each check.
"""

from __future__ import annotations

import json

import pytest

from backend import doctor as doctor_mod
from backend import settings as settings_mod
from backend.settings import reset_settings_cache


@pytest.fixture(autouse=True)
def _isolated_env(monkeypatch, tmp_path):
    """No inherited SWARM_* state, a temp sandbox, and an empty provider store.

    The store isolation matters: `resolve_key` reads the SQLite file at
    db.DB_PATH, which is a module global. Without pointing it at a fresh temp
    file, whichever test ran earlier decides whether groq reads as "connected
    via GROQ_API_KEY" or "connected via sealed store" — a real order
    dependency, not a flaky test.
    """
    import backend.db as db_mod

    for name in settings_mod.MANAGED_ENV_VARS:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("SWARM_SANDBOX_DIR", str(tmp_path / "sandbox"))
    monkeypatch.setenv("SWARM_DB_PATH", str(tmp_path / "swarm.db"))
    monkeypatch.setenv("PYTHON_DOTENV_DISABLED", "1")
    for name in ("GROQ_API_KEY", "OPENROUTER_API_KEY"):
        monkeypatch.delenv(name, raising=False)

    original_path = db_mod.DB_PATH
    db_mod.DB_PATH = tmp_path / "provider-store.db"
    import asyncio

    asyncio.run(db_mod.init_db())
    reset_settings_cache()
    yield
    db_mod.DB_PATH = original_path
    reset_settings_cache()


def _row(report, name):
    return next(r for r in report["checks"] if r["check"] == name)


# ------------------------------------------------------------- the report --

def test_every_check_names_itself_and_explains_itself():
    """A check with no explanation is worse than no check."""
    for row in doctor_mod.run_doctor()["checks"]:
        assert row["check"], row
        assert row["detail"], row
        assert row["status"] in {doctor_mod.OK, doctor_mod.WARN, doctor_mod.FAIL, doctor_mod.SKIP}


def test_the_report_covers_everything_it_promises():
    """python, keys, db path, sandbox, risky settings, connectivity."""
    names = {row["check"] for row in doctor_mod.run_doctor()["checks"]}
    for expected in (
        "python", "dependencies", "settings", "providers",
        "db_path", "sandbox", "host_tools", "declared_bind", "connectivity",
    ):
        assert expected in names, expected


def test_no_network_call_without_the_flag(monkeypatch):
    """The default path must not touch the network at all.

    An operator on a plane should get a full report, not a hang.
    """
    def explode(*_args, **_kwargs):
        raise AssertionError("doctor must not open a socket by default")

    monkeypatch.setattr(doctor_mod.urllib.request, "urlopen", explode)
    report = doctor_mod.run_doctor()
    connectivity = [r for r in report["checks"] if r["check"] == "connectivity"]
    assert connectivity and connectivity[0]["status"] == doctor_mod.SKIP


def test_connectivity_is_opt_in(monkeypatch):
    """The row is always there so the report shape is stable; only the flag
    decides whether it is skipped or actually run."""
    monkeypatch.setattr(
        doctor_mod, "check_connectivity",
        lambda: doctor_mod._check("connectivity", doctor_mod.OK, "stubbed"),
    )
    off = _row(doctor_mod.run_doctor(connectivity=False), "connectivity")
    assert off["status"] == doctor_mod.SKIP
    on = _row(doctor_mod.run_doctor(connectivity=True), "connectivity")
    assert on == {"check": "connectivity", "status": doctor_mod.OK, "detail": "stubbed"}


def test_an_unreachable_host_is_a_warning_not_a_failure(monkeypatch):
    """Offline is a normal state for a local-first tool, not a broken install."""
    import urllib.error

    def refuse(*_args, **_kwargs):
        raise urllib.error.URLError("no route to host")

    monkeypatch.setattr(doctor_mod.urllib.request, "urlopen", refuse)
    row = doctor_mod.check_connectivity()
    assert row["status"] == doctor_mod.WARN
    assert "unreachable" in row["detail"]


def test_an_http_error_still_counts_as_reachable(monkeypatch):
    """The server answered, so the network is fine. Only the request was wrong."""
    import urllib.error

    def refuse(*_args, **_kwargs):
        raise urllib.error.HTTPError("https://x", 405, "nope", {}, None)

    monkeypatch.setattr(doctor_mod.urllib.request, "urlopen", refuse)
    row = doctor_mod.check_connectivity()
    assert row["status"] == doctor_mod.OK
    assert "405" in row["detail"]


# ---------------------------------------------------------- keys resolve --

def test_a_configured_provider_key_is_reported_as_present(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "gsk_test_not_a_real_key")
    reset_settings_cache()
    rows = [r for r in doctor_mod.check_providers() if r["check"] == "provider:groq"]
    assert rows and rows[0]["status"] == doctor_mod.OK
    assert "GROQ_API_KEY" in rows[0]["detail"]


def test_the_reported_provider_row_never_contains_the_key(monkeypatch):
    """A doctor report gets pasted into a public issue. Keys must not ride along."""
    monkeypatch.setenv("GROQ_API_KEY", "gsk_secret_value_do_not_print")
    reset_settings_cache()
    rendered = json.dumps(doctor_mod.check_providers())
    assert "gsk_secret_value_do_not_print" not in rendered


def test_no_key_is_a_warning_with_a_way_out():
    """No key at all is the state of a fresh clone, and must read as a warning
    with instructions rather than an error the operator cannot act on."""
    rows = doctor_mod.check_providers()
    assert len(rows) == 1
    assert rows[0]["status"] == doctor_mod.WARN
    assert "GROQ_API_KEY" in rows[0]["fix"]
    assert "SWARM_DEMO" in rows[0]["fix"]


def test_demo_mode_is_not_reported_as_a_broken_install(monkeypatch):
    """SWARM_DEMO=1 is a supported way to boot without a key."""
    monkeypatch.setenv("SWARM_DEMO", "1")
    reset_settings_cache()
    row = doctor_mod.check_providers()[0]
    assert row["status"] == doctor_mod.WARN
    assert "demo mode is on" in row["detail"]


# ------------------------------------------------------------ paths --

def test_an_unwritable_db_path_fails_with_a_fix(tmp_path):
    import backend.db as db_mod

    original = db_mod.DB_PATH
    db_mod.DB_PATH = tmp_path / "no-such-dir" / "swarm.db"
    try:
        row = doctor_mod.check_db_path()
    finally:
        db_mod.DB_PATH = original
    assert row["status"] == doctor_mod.FAIL
    assert "SWARM_DB_PATH" in row["fix"]


def test_a_missing_sandbox_directory_is_created_and_ok(tmp_path):
    """The sandbox is created on demand, so a missing dir is not a failure."""
    row = doctor_mod.check_sandbox()
    assert row["status"] == doctor_mod.OK


def test_the_probe_leaves_no_file_behind(tmp_path):
    """The writability probe must not litter the directory it inspects."""
    before = {p.name for p in tmp_path.iterdir()}
    doctor_mod._dir_writable(tmp_path)
    assert {p.name for p in tmp_path.iterdir()} == before


# ------------------------------------------------------- risky settings --

def _status_of(rows, name):
    return next(r for r in rows if r["check"] == name)["status"]


def test_host_tools_on_is_a_warning(monkeypatch):
    monkeypatch.setenv("SWARM_SYSTEM", "1")
    reset_settings_cache()
    row = next(r for r in doctor_mod.check_exposure() if r["check"] == "host_tools")
    assert row["status"] == doctor_mod.WARN
    assert "run commands on this machine" in row["detail"]


def test_host_tools_on_a_public_bind_says_so(monkeypatch):
    """The combination that matters: reachable and executing."""
    monkeypatch.setenv("SWARM_SYSTEM", "1")
    monkeypatch.setenv("SWARM_HOST", "0.0.0.0")
    reset_settings_cache()
    rows = doctor_mod.check_exposure()
    assert _status_of(rows, "exposed_bind") == doctor_mod.WARN
    assert any(r["check"] == "host_tools" and "0.0.0.0" in r["detail"] for r in rows)


def test_demo_mode_on_is_a_warning(monkeypatch):
    monkeypatch.setenv("SWARM_DEMO", "on")
    reset_settings_cache()
    assert _status_of(doctor_mod.check_exposure(), "demo_mode") == doctor_mod.WARN


def test_missing_secret_is_a_warning(monkeypatch):
    rows = doctor_mod.check_exposure()
    assert _status_of(rows, "secret") == doctor_mod.WARN
    monkeypatch.setenv("SWARM_SECRET", "a-private-string")
    reset_settings_cache()
    assert _status_of(doctor_mod.check_exposure(), "secret") == doctor_mod.OK


def test_unrestricted_system_root_is_a_warning(monkeypatch):
    monkeypatch.setenv("SWARM_SYSTEM_UNRESTRICTED", "1")
    reset_settings_cache()
    assert _status_of(doctor_mod.check_exposure(), "system_root") == doctor_mod.WARN


def test_warnings_do_not_fail_the_run(monkeypatch):
    """A doctor that blocks legitimate local choices gets ignored entirely."""
    monkeypatch.setenv("SWARM_DEMO", "1")
    monkeypatch.setenv("SWARM_SYSTEM", "1")
    monkeypatch.setenv("SWARM_HOST", "0.0.0.0")
    reset_settings_cache()
    report = doctor_mod.run_doctor()
    assert report["summary"]["warn"] >= 3
    assert report["ok"] is True


# ------------------------------------------------------------ redaction --

def test_the_snapshot_never_prints_a_secret(monkeypatch):
    monkeypatch.setenv("SWARM_SECRET", "the-real-secret-value")
    reset_settings_cache()
    rendered = json.dumps(doctor_mod.run_doctor())
    assert "the-real-secret-value" not in rendered


def test_a_failure_puts_its_fix_first(monkeypatch, tmp_path):
    """With something actually broken, the fix is the first next step."""
    import backend.db as db_mod

    original = db_mod.DB_PATH
    db_mod.DB_PATH = tmp_path / "missing" / "deep" / "swarm.db"
    try:
        report = doctor_mod.run_doctor()
    finally:
        db_mod.DB_PATH = original
    assert report["ok"] is False
    assert report["next_steps"], "a broken install must say what to do"
    assert "SWARM_DB_PATH" in report["next_steps"][0]
    assert "not usable" in doctor_mod.format_report(report)


def test_next_steps_are_empty_for_a_clean_install(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "gsk_test")
    monkeypatch.setenv("SWARM_SECRET", "private")
    reset_settings_cache()
    report = doctor_mod.run_doctor()
    assert report["ok"] is True
    assert report["next_steps"] == []


# ------------------------------------------------------------ bad config --

def test_a_bad_variable_is_reported_as_a_failure_with_its_fix(monkeypatch):
    monkeypatch.setenv("SWARM_SYSTEM", "enabled")
    reset_settings_cache()
    row = doctor_mod.check_settings()[0]
    assert row["status"] == doctor_mod.FAIL
    assert row["problems"][0]["variable"] == "SWARM_SYSTEM"
    assert doctor_mod.run_doctor()["ok"] is False


def test_main_exits_nonzero_on_a_bad_variable(monkeypatch, capsys):
    monkeypatch.setenv("SWARM_DEMO", "enabled")
    reset_settings_cache()
    assert doctor_mod.main([]) == 1
    assert "SWARM_DEMO" in capsys.readouterr().err


def test_main_exits_zero_on_a_usable_install(monkeypatch, capsys):
    monkeypatch.setenv("GROQ_API_KEY", "gsk_test")
    reset_settings_cache()
    assert doctor_mod.main([]) == 0
    out = capsys.readouterr().out
    assert "swarm doctor" in out
    assert "[ok  ] python" in out or "[ok]" in out