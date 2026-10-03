"""`swarm doctor` — why won't it start?

A local-first tool gets debugged by the person who installed it, on their own
machine, with no one to ask. The most common failure is a configuration
mistake, and the least helpful thing the backend could do was log a warning
and boot anyway. This module answers the question directly:

    python -m backend.doctor          # or: swarm doctor

Design constraints, all of them learned from how self-hosting actually fails:

* **Offline-first.** Every check runs against local state. The only network
  probe is an explicit `--connectivity` flag, because "the model is not
  replying" and "the model is not configured" are different problems and
  conflating them wastes an afternoon.
* **Never prints a secret.** Secrets report as `set` / `not set`. This
  snapshot is designed to be pasted into a public issue, which is where a
  self-hoster will put it.
* **Never raises.** Every check returns a `status` of ok / warn / fail / skip
  and a sentence explaining it. A doctor that crashes is a doctor that does
  not get run.
* **Exit code 0 when the box is usable, 1 when something is actually broken.**
  Warnings (demo mode on, host tools on a public bind) do not fail the run —
  they are legitimate local choices, and a doctor that nags is ignored.
"""

from __future__ import annotations

import os
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

# Import order matters here and is not cosmetic. backend.models imports
# backend.tools.registry, which imports backend.db, which imports
# backend.models — a cycle that only resolves if backend.db enters the graph
# first. main.py gets this for free by importing db before models; doctor.py
# reaches ai_support (and therefore models) inside a check, so db is pulled in
# here at module level to keep the same entry point into the cycle.
from . import db as _db  # noqa: F401  — imported for cycle order, not used

from .settings import (
    SettingsError,
    get_settings,
    settings_snapshot,
    validate_settings,
)

OK = "ok"
WARN = "warn"
FAIL = "fail"
SKIP = "skip"

# 2s is enough to notice a blackholed TCP connect on loopback and short
# enough that an offline laptop does not sit there for a minute.
_CONNECTIVITY_TIMEOUT = 2.0


def _check(
    name: str,
    status: str,
    detail: str,
    fix: str = "",
    **extra: Any,
) -> dict[str, Any]:
    row: dict[str, Any] = {"check": name, "status": status, "detail": detail}
    if fix:
        row["fix"] = fix
    row.update(extra)
    return row


# --------------------------------------------------------------- python --

def check_python() -> dict[str, Any]:
    """Interpreter version. 3.11+ for `X | None` annotations at runtime."""
    version = sys.version_info
    label = ".".join(str(n) for n in version[:3])
    if version[:2] < (3, 11):
        return _check(
            "python", FAIL,
            f"Python {label} is too old for this codebase.",
            "Install Python 3.11 or newer and recreate the virtualenv.",
        )
    return _check("python", OK, f"Python {label}", executable=sys.executable)


def check_dependencies() -> dict[str, Any]:
    """The imports that must be present for the app to boot."""
    required = ("fastapi", "uvicorn", "aiosqlite", "pydantic", "pydantic_settings", "openai")
    missing = [name for name in required if not _importable(name)]
    if missing:
        return _check(
            "dependencies", FAIL,
            f"missing: {', '.join(missing)}",
            "uv pip install -r requirements.txt",
        )
    return _check("dependencies", OK, f"all {len(required)} required packages import")


def _importable(name: str) -> bool:
    from importlib.util import find_spec

    try:
        return find_spec(name) is not None
    except (ImportError, ValueError):
        return False


# ------------------------------------------------------------- settings --

def check_settings() -> list[dict[str, Any]]:
    """Every managed variable, then the strict-validation verdict.

    Validation runs here so `swarm doctor` reports the same error the server
    would refuse to boot on, rather than being more forgiving than the thing
    it is diagnosing.
    """
    snapshot = settings_snapshot()
    rows = [
        _check(
            "settings",
            FAIL if snapshot["problems"] else OK,
            _settings_message(snapshot["problems"]),
            "Fix the variable(s) above and restart.",
            problems=snapshot["problems"],
        )
    ]
    return rows


def _settings_message(problems: list[dict[str, str]]) -> str:
    if not problems:
        return f"{len(snapshot_variables())} SWARM_* variables parsed"
    names = ", ".join(p["variable"] for p in problems)
    return f"{len(problems)} unusable value(s): {names}"


def snapshot_variables() -> list[dict[str, Any]]:
    return settings_snapshot()["variables"]


# ------------------------------------------------------------- providers --

def check_providers() -> list[dict[str, Any]]:
    """Do provider keys resolve? Reports presence and source, never the key."""
    # providers_by_priority, not list_providers: the latter projects the rows
    # down to _PUBLIC_KEYS (it feeds a UI response) and drops env_fallback,
    # so every provider would look unconfigured.
    from .ai_support.providers import providers_by_priority

    settings = get_settings()
    rows: list[dict[str, Any]] = []
    demo = settings.demo_mode()

    async def probe() -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for spec in providers_by_priority():
            env_name = spec.get("env_fallback")
            env_ok = bool((os.environ.get(env_name) or "").strip()) if env_name else False
            # resolve_key() deliberately conflates the sealed store with the env
            # fallback, so it cannot tell us *where* a key came from. The
            # operator's next action depends on that: a key in .env is found by
            # editing .env, one in the store by opening Command Center. So the
            # store row is read directly.
            #
            # A missing SQLite file makes this raise, and on a fresh install
            # that is normal rather than a fault. It is swallowed because it
            # says nothing about whether the env key is set.
            try:
                row = await _db.get_ai_provider(spec["id"])
                stored = bool(row and row.get("secret"))
            except Exception:  # noqa: BLE001 — day one has no database yet
                stored = False
            if stored:
                via = "sealed store (Command Center)"
            elif env_ok:
                via = env_name
            else:
                continue
            out.append(_check(
                f"provider:{spec['id']}", OK,
                f"connected via {via}", model=spec.get("default_model"),
            ))
        return out

    try:
        import asyncio

        rows.extend(asyncio.run(probe()))
    except Exception as exc:  # noqa: BLE001 — a doctor must not crash
        rows.append(_check(
            "providers", WARN, f"could not read the provider catalog: {exc}",
        ))

    if not rows:
        if demo:
            rows.append(_check(
                "providers", WARN,
                "no provider key found; demo mode is on so replies are mocked",
                "Add GROQ_API_KEY to .env for real replies.",
            ))
        else:
            rows.append(_check(
                "providers", WARN,
                "no provider key found; bots will not reply",
                "Add GROQ_API_KEY to .env, connect a provider in Command Center, "
                "or set SWARM_DEMO=1 to boot without a key.",
            ))
    return rows


# ------------------------------------------------------------ filesystem --

def _dir_writable(directory: Path) -> tuple[bool, str]:
    """Can we actually create a file in this directory? os.access lies on some
    setups (a read-only mount, a Windows ACL), so the probe writes a real file
    rather than asking."""
    if not directory.exists():
        return False, f"{directory} does not exist"
    if not directory.is_dir():
        return False, f"{directory} is not a directory"
    probe = directory / f".swarm-doctor-{os.getpid()}.tmp"
    try:
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
    except OSError as exc:
        return False, str(exc)
    return True, "writable"


def _file_writable(path: Path) -> tuple[bool, str]:
    """Writability of a *file* path: its directory must be writable, and the
    file itself must not be read-only if it already exists."""
    directory = path.parent if path.parent != path else path
    ok, detail = _dir_writable(directory)
    if not ok:
        return False, detail
    if path.exists():
        try:
            with open(path, "ab"):
                pass
        except OSError as exc:
            return False, f"exists but is not writable: {exc}"
        return True, "existing file, writable"
    return True, "will be created"


def check_db_path() -> dict[str, Any]:
    from .db import DB_PATH

    ok, detail = _file_writable(Path(DB_PATH))
    if ok:
        return _check("db_path", OK, f"{DB_PATH} ({detail})", path=str(DB_PATH))
    return _check(
        "db_path", FAIL, f"{DB_PATH}: {detail}",
        "Point SWARM_DB_PATH at a directory the backend user can write.",
        path=str(DB_PATH),
    )


def check_sandbox() -> dict[str, Any]:
    from .agent import SANDBOX_DIR

    root = Path(SANDBOX_DIR)
    detail = _describe_sandbox()
    try:
        root.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        return _check(
            "sandbox", FAIL, f"{root}: {exc}",
            "Set SWARM_SANDBOX_DIR to a writable path, or create it manually.",
            path=str(root),
        )
    ok, why = _dir_writable(root)
    if not ok:
        return _check(
            "sandbox", FAIL, f"{root}: {why}",
            "Set SWARM_SANDBOX_DIR to a writable path.", path=str(root),
        )
    return _check("sandbox", OK, f"{root} ({detail})", path=str(root))


def _describe_sandbox() -> str:
    """Where the sandbox came from, which is the question operators ask."""
    if (os.environ.get("SWARM_SANDBOX_DIR") or "").strip():
        return "from SWARM_SANDBOX_DIR"
    if os.name == "nt":
        return "default (TEMP/swarm-sandbox)"
    return "default (/tmp/swarm-sandbox)"


# ------------------------------------------------------------- exposure --

def check_exposure() -> list[dict[str, Any]]:
    """Settings that are fine locally and dangerous wider.

    Each of these is a legitimate choice on a laptop and a footgun on a LAN,
    so they are warnings, not failures. A doctor that blocks `SWARM_DEMO=1`
    would just teach people to ignore it.
    """
    from .tools import system as system_mod

    settings = get_settings()
    rows: list[dict[str, Any]] = []
    bind = ", ".join(settings.bind_candidates()) or "loopback (uvicorn default)"

    if settings.demo_mode():
        rows.append(_check(
            "demo_mode", WARN,
            "SWARM_DEMO is on: replies are mocked and #general is seeded",
            "Set SWARM_DEMO=0 before using this for real work.",
        ))
    if settings.system_enabled():
        status = WARN
        detail = "host-system tools are ON: bots can run commands on this machine"
        if settings.exposed_on_non_loopback():
            detail += f", and the bind is {bind}"
        rows.append(_check(
            "host_tools", status, detail,
            "Set SWARM_SYSTEM=0 unless you deliberately want host command execution.",
        ))
    else:
        rows.append(_check("host_tools", OK, "host-system tools are off (the default)"))
    if settings.system_unrestricted_ok():
        rows.append(_check(
            "system_root", WARN,
            "SWARM_SYSTEM_UNRESTRICTED is on: the filesystem root is an allowed system root",
            "Set SWARM_SYSTEM_UNRESTRICTED=0 and point SWARM_SYSTEM_ROOT at a directory.",
        ))
    if settings.exposed_on_non_loopback() and settings.system_enabled():
        rows.append(_check(
            "exposed_bind", WARN,
            f"host tools on a non-loopback bind ({bind}): anyone who reaches the port "
            "can run commands on this machine",
            "Bind to 127.0.0.1 and put auth plus TLS in front, or set SWARM_SYSTEM=0.",
        ))
    if not settings.secret_configured():
        rows.append(_check(
            "secret", WARN,
            "SWARM_SECRET is unset: stored provider keys fall back to a public default",
            "Set SWARM_SECRET to a private random string before connecting providers.",
        ))
    else:
        rows.append(_check("secret", OK, "SWARM_SECRET is set"))
    if settings.browser_enabled():
        rows.append(_check(
            "browser_tools", OK,
            "playwright browser tools are on (set SWARM_BROWSER=0 to disable)",
        ))
    else:
        rows.append(_check("browser_tools", OK, "playwright browser tools are off"))
    rows.append(_check("declared_bind", OK, f"declared bind: {bind}"))
    return rows


# ---------------------------------------------------------- connectivity --

def check_connectivity() -> dict[str, Any]:
    """One TCP reachability probe. Skipped by default; never required.

    This is the only check that touches the network, so it is opt-in: on an
    offline laptop the honest answer is "not tested", not "broken".
    """
    host = get_settings().openai_compat_base_url or "https://api.groq.com"
    target = host.split("//", 1)[-1].split("/", 1)[0]
    url = f"https://{target}"
    try:
        request = urllib.request.Request(url, method="HEAD")
        with urllib.request.urlopen(request, timeout=_CONNECTIVITY_TIMEOUT):
            return _check("connectivity", OK, f"{target} reachable")
    except urllib.error.HTTPError as exc:
        # Reachable: the server answered, it just did not like HEAD. That is
        # a successful connectivity probe, not a failure.
        return _check("connectivity", OK, f"{target} reachable (HTTP {exc.code})")
    except urllib.error.URLError as exc:
        return _check(
            "connectivity", WARN,
            f"{target} unreachable: {exc.reason}",
            "Check your network, proxy, or VPN. Bots need egress to the provider.",
        )
    except OSError as exc:
        return _check("connectivity", WARN, f"{target} unreachable: {exc}")


# ---------------------------------------------------------------- report --

def run_doctor(*, connectivity: bool = False) -> dict[str, Any]:
    """The full report. JSON-serialisable, ordered, and safe to paste."""
    settings = get_settings()
    checks: list[dict[str, Any]] = [check_python(), check_dependencies()]
    checks.extend(check_settings())
    checks.extend(check_providers())
    checks.append(check_db_path())
    checks.append(check_sandbox())
    checks.extend(check_exposure())
    if connectivity:
        checks.append(check_connectivity())
    else:
        checks.append(_check(
            "connectivity", SKIP,
            "not tested (pass --connectivity to probe the provider host)",
        ))

    failures = [c for c in checks if c["status"] == FAIL]
    warnings = [c for c in checks if c["status"] == WARN]
    return {
        "ok": not failures,
        "summary": {
            "ok": sum(1 for c in checks if c["status"] == OK),
            "warn": len(warnings),
            "fail": len(failures),
            "skip": sum(1 for c in checks if c["status"] == SKIP),
        },
        "checks": checks,
        "variables": snapshot_variables(),
        "next_steps": _next_steps(failures, warnings),
    }


def _next_steps(failures: list[dict[str, Any]], warnings: list[dict[str, Any]]) -> list[str]:
    """The fixes worth doing first, worst problem first."""
    steps = [c["fix"] for c in failures if c.get("fix")]
    steps.extend(c["fix"] for c in warnings if c.get("fix") and c["check"] in _PRIORITY_WARNINGS)
    return steps


# Warnings worth surfacing in "next_steps" rather than leaving for the reader
# to spot. Everything else is a legitimate local choice and stays in `checks`.
_PRIORITY_WARNINGS = {"providers", "host_tools", "settings"}


def format_report(report: dict[str, Any]) -> str:
    """Human-readable rendering of the same JSON. Lamps, not red/green text."""
    marks = {OK: "ok  ", WARN: "warn", FAIL: "FAIL", SKIP: "skip"}
    out: list[str] = []
    verdict = "usable" if report["ok"] else "not usable"
    s = report["summary"]
    out.append(
        f"swarm doctor: {verdict} — {s['ok']} ok, {s['warn']} warn, "
        f"{s['fail']} fail, {s['skip']} skipped"
    )
    out.append("")
    for row in report["checks"]:
        head = f"  [{marks.get(row['status'], '?')}] {row['check']}: {row['detail']}"
        out.append(head)
        if row.get("fix") and row["status"] in {FAIL, WARN}:
            out.append(f"          → {row['fix']}")
    if report["next_steps"]:
        out.append("")
        out.append("Do this next:")
        out.extend(f"  {i}. {s_}" for i, s_ in enumerate(report["next_steps"], 1))
    return "\n".join(out)


def main(argv: list[str] | None = None) -> int:
    """Entry point for `python -m backend.doctor`."""
    import argparse

    parser = argparse.ArgumentParser(prog="swarm doctor", description=__doc__)
    parser.add_argument(
        "--connectivity", action="store_true",
        help="also probe the provider host (one TCP request; off by default)",
    )
    args = parser.parse_args(argv)
    try:
        validate_settings()
    except SettingsError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    report = run_doctor(connectivity=args.connectivity)
    print(format_report(report))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())