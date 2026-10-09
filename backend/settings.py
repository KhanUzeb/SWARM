"""One validated settings object for every SWARM_* environment variable.

Why this module exists
----------------------
The backend used to read its `SWARM_*` configuration with five different
parsing idioms spread over ten files. The damage was not style: the same
spelling meant different things in different places. `SWARM_SYSTEM` accepted
`on`, `SWARM_DEMO` did not, so an operator who learned "on works" got a silent
no-op from the other flag with no error anywhere. A typo in a numeric setting
silently became a default. Nothing was ever validated at startup.

This module is the single parse surface:

* :func:`truthy` is the one boolean parser, and every boolean `SWARM_*` flag
  goes through it.
* :class:`Settings` reads the environment exactly once per change and holds
  the raw values.
* :meth:`Settings.problems` returns every variable whose value is not
  accepted, each one naming the variable and listing what would be accepted.
* :func:`validate_settings` is called from the FastAPI lifespan and raises
  :class:`SettingsError` on any problem, so a bad value stops the boot with a
  message that names the variable instead of a warning nobody reads.

Two rules kept the migration honest:

1. **Widen, never narrow.** A value that worked before still works. `SWARM_DEMO`
   additionally accepts `on`, because "these two flags disagree" was the bug.
2. **Strict only where the old code was already strict.** Four variables were
   already set-membership tests against a closed vocabulary, so a value outside
   it was unambiguously a typo and may now stop the boot. Everything else
   keeps its documented lenient fallback, because for those the fallback *is*
   the safety property: `SWARM_RETENTION_DAYS=abc` must mean "keep
   everything", never "delete everything".

`SWARM_BROWSER` is deliberately NOT on the boolean parser. It is inverted
(`unset` means enabled) and its historical contract is "anything not in
{0,false,no,off} means on", so `SWARM_BROWSER=enabled` is accepted today and
must stay accepted. See `backend/tools/browser.py`.
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from typing import Any, Iterator

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

__all__ = [
    "Settings",
    "SettingsError",
    "SettingProblem",
    "get_settings",
    "validate_settings",
    "truthy",
    "settings_snapshot",
    "read_bool",
    "read_number",
    "reset_settings_cache",
]


# The vocabulary every boolean SWARM_* flag accepts, and its negation. Both
# halves are explicit so `SWARM_SYSTEM=enabled` is rejected with a message
# that says what to type instead of being treated as false.
TRUTHY_VALUES: frozenset[str] = frozenset({"1", "true", "t", "yes", "y", "on"})
FALSY_VALUES: frozenset[str] = frozenset({"0", "false", "f", "no", "n", "off"})

_BOOL_HELP = "use 1/0, true/false, yes/no, on/off"

# Variables whose old code was a closed set-membership test. Only these may
# fail a boot; see the module docstring.
STRICT_BOOL_FIELDS: tuple[str, ...] = ("system", "system_unrestricted", "demo")
STRICT_ENUM_FIELDS: dict[str, tuple[str, ...]] = {"computer_provider": ("local", "none", "fake")}

# Defaults that are part of the shipped contract, not opinions.
DEFAULT_BUSY_TIMEOUT_MS = 5000
DEFAULT_GC_MAX_AGE_HOURS = 72.0
DEFAULT_GC_MAX_MB = 512.0

_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1", "0:0:0:0:0:0:0:1"})

# Never printed, never logged, never returned by `settings_snapshot`. Listed
# here only so the redaction rule has a name. Both halves of the generic
# OpenAI-compatible connection belong here: the API key is a credential, so
# `swarm doctor` must report only whether it is set.
SECRET_FIELDS: frozenset[str] = frozenset({"secret", "openai_compat_api_key"})


def truthy(raw: str | None, default: bool = False) -> bool:
    """The one boolean parser. `1 true t yes y on` are true, the rest false.

    `raw` is stripped, case-folded and matched against both vocabularies, so
    the same spelling means the same thing for every flag. An empty or unset
    value is `default`, which is how an unset flag keeps its documented
    default rather than being coerced by the flag's polarity.
    """
    if raw is None:
        return default
    text = str(raw).strip().lower()
    if not text:
        return default
    if text in TRUTHY_VALUES:
        return True
    if text in FALSY_VALUES:
        return False
    return default


def read_bool(name: str, default: bool = False) -> bool:
    """`SWARM_*` boolean straight from the environment, via the one parser."""
    return truthy(os.environ.get(name), default)


def read_number(
    name: str, default: float | None, *, kind: type[float] | type[int] = float
) -> float | int | None:
    """Lenient numeric read with the documented fallback.

    Used for the settings whose fallback is a safety property rather than a
    convenience (retention, busy timeout, GC caps). Never raises: a typo means
    "use the default", and the default is the safe reading.
    """
    raw = (os.environ.get(name) or "").strip()
    if not raw:
        return default
    try:
        value = kind(float(raw))
    except (TypeError, ValueError, OverflowError):
        # "1e400" parses as inf and int(inf) raises OverflowError, so an
        # out-of-range literal is as unusable as a typo: same fallback.
        return default
    return value


@dataclass(frozen=True)
class SettingProblem:
    """One variable whose value is not accepted, named and explained."""

    variable: str
    value: str
    message: str

    def as_dict(self) -> dict[str, str]:
        return {"variable": self.variable, "value": self.value, "message": self.message}


class SettingsError(RuntimeError):
    """Raised at startup when any SWARM_* variable holds an unusable value."""

    def __init__(self, problems: list[SettingProblem]) -> None:
        self.problems = problems
        names = ", ".join(p.variable for p in problems)
        lines = [
            f"invalid configuration: {len(problems)} variable(s) cannot be used: {names}"
        ]
        for p in problems:
            lines.append(f"  {p.variable}={p.value!r} — {p.message}")
        super().__init__("\n".join(lines))


class Settings(BaseSettings):
    """Raw `SWARM_*` values plus the one parser for each of them.

    Fields are `str | None` on purpose: this object records what the
    environment said, and :meth:`problems` decides whether that is usable.
    Deciding at read time would make a typo invisible, and the whole point is
    that a self-hoster learns about it at boot rather than at 3am.
    """

    model_config = SettingsConfigDict(
        env_prefix="SWARM_",
        case_sensitive=False,
        extra="ignore",
        env_nested_delimiter=None,
    )

    # --- booleans (strict) -------------------------------------------------
    system: str | None = Field(default=None, description="host-system tools opt-in")
    system_unrestricted: str | None = Field(default=None, description="allow filesystem root as system root")
    demo: str | None = Field(default=None, description="deterministic mock replies + seeded thread")

    # --- enums (strict) ----------------------------------------------------
    computer_provider: str | None = Field(default=None, description="local|none|fake")

    # --- booleans (inverted, not strict) -----------------------------------
    browser: str | None = Field(default=None, description="playwright browser-use tools; unset means on")

    # --- free strings ------------------------------------------------------
    host: str | None = Field(default=None, description="bind address, informational only")
    bind: str | None = Field(default=None, description="bind address alias, informational only")
    system_root: str | None = Field(default=None, description="directory host tools may touch")
    sandbox_dir: str | None = Field(default=None, description="shared team computer home")
    db_path: str | None = Field(default=None, description="SQLite file")
    allowed_origins: str | None = Field(default=None, description="comma-separated browser origins")
    agent_model: str | None = Field(default=None, description="overrides every agent's model column")
    openai_compat_base_url: str | None = Field(default=None, description="custom OpenAI-compatible base URL")
    openai_compat_api_key: str | None = Field(default=None, description="custom OpenAI-compatible API key")
    oauth_redirect_uri: str | None = Field(default=None, description="provider OAuth callback URL")
    secret: str | None = Field(default=None, description="seal key for stored provider keys")
    revision: str | None = Field(default=None, description="build revision for /health, informational only")

    # --- numerics (lenient, with documented fallbacks) ---------------------
    sqlite_busy_timeout_ms: str | None = None
    retention_days: str | None = None
    gc_max_age_hours: str | None = None
    gc_max_mb: str | None = None

    # ---------------------------------------------------------- validation --

    def problems(self) -> list[SettingProblem]:
        """Every unusable value, worst first. Never raises."""
        found: list[SettingProblem] = []
        for name in STRICT_BOOL_FIELDS:
            var = f"SWARM_{name.upper()}"
            raw = (getattr(self, name) or "").strip()
            if not raw:
                continue
            if raw.lower() not in TRUTHY_VALUES | FALSY_VALUES:
                found.append(
                    SettingProblem(var, raw, f"not a boolean — {_BOOL_HELP}")
                )
        for name, allowed in STRICT_ENUM_FIELDS.items():
            var = f"SWARM_{name.upper()}"
            raw = (getattr(self, name) or "").strip()
            if not raw:
                continue
            if raw.lower() not in allowed:
                found.append(
                    SettingProblem(
                        var, raw,
                        f"unknown value — use one of: {', '.join(allowed)}",
                    )
                )
        return found

    # ------------------------------------------------------ parsed access --

    def system_enabled(self) -> bool:
        """Host-system tools. Unset means off: the shipped default."""
        return truthy(self.system, False)

    def system_unrestricted_ok(self) -> bool:
        """Allow the filesystem root as a system root. Unset means off."""
        return truthy(self.system_unrestricted, False)

    def demo_mode(self) -> bool:
        """Deterministic mock replies. Unset means off."""
        return truthy(self.demo, False)

    def browser_enabled(self) -> bool:
        """Playwright tools. Unset means ON, matching the inverted contract.

        The old code read `(os.environ.get(...) or "1")` and disabled only on
        0/false/no/off, so an unset var meant enabled. `default=True` is that
        same literal, and because truthy() falls back to it for any value it
        does not recognise, `SWARM_BROWSER=enabled` stays enabled.
        """
        return truthy(self.browser, True)

    def computer(self) -> str:
        """`local` | `none` | `fake`, falling back to `local`.

        The fallback is the widest option on purpose: a provider this build
        cannot parse should not quietly disable the computer host, and
        `problems()` reports it at boot.
        """
        raw = (self.computer_provider or "").strip().lower()
        if raw in STRICT_ENUM_FIELDS["computer_provider"]:
            return raw
        return "local"

    def busy_timeout_ms(self) -> int:
        """SWARM_SQLITE_BUSY_TIMEOUT_MS, never negative, default 5000."""
        value = read_number("SWARM_SQLITE_BUSY_TIMEOUT_MS", float(DEFAULT_BUSY_TIMEOUT_MS), kind=int)
        return max(0, int(value))

    def effective_retention_days(self) -> int | None:
        """SWARM_RETENTION_DAYS as a positive int, or None when unusable.

        Unset, empty, non-numeric, zero and negative all mean "keep
        everything": a typo must never read as "delete my transcript". Routed
        through read_number so an out-of-range literal like 1e400 gets the same
        fallback as any other unusable value instead of raising.
        """
        days = read_number("SWARM_RETENTION_DAYS", None, kind=int)
        if days is None:
            return None
        return days if days > 0 else None

    def effective_gc_max_age_hours(self) -> float:
        """SWARM_GC_MAX_AGE_HOURS, or the 72h default. Never negative.

        A non-positive age deletes every file in the sandbox, so it is treated
        as unusable rather than obeyed.
        """
        value = float(read_number("SWARM_GC_MAX_AGE_HOURS", DEFAULT_GC_MAX_AGE_HOURS))
        return value if value > 0 else DEFAULT_GC_MAX_AGE_HOURS

    def effective_gc_max_mb(self) -> float:
        """SWARM_GC_MAX_MB, or the 512MB default. Never negative.

        This is a deletion bound, not a preference: sweep_oversize deletes
        until the total fits, so a negative cap makes every file in the sandbox
        qualify and the sweeper would empty it. An unusable value therefore
        keeps the default instead of being obeyed.
        """
        value = float(read_number("SWARM_GC_MAX_MB", DEFAULT_GC_MAX_MB))
        return value if value > 0 else DEFAULT_GC_MAX_MB

    def origin_list(self) -> list[str]:
        """SWARM_ALLOWED_ORIGINS split and cleaned, bare `*` dropped.

        A bare `*` never reaches CORSMiddleware: it is paired with
        `allow_credentials=True`, and `origin_allowed` treats it as a literal
        that matches nothing, so keeping it would leave one layer open and the
        other shut.
        """
        raw = (self.allowed_origins or "").strip()
        if not raw:
            return []
        return [o.strip() for o in raw.split(",") if o.strip() and o.strip() != "*"]

    def bind_candidates(self) -> list[str]:
        """Non-empty bind addresses the operator declared, lowercased."""
        return [c for c in ((self.host or "").strip().lower(), (self.bind or "").strip().lower()) if c]

    def exposed_on_non_loopback(self) -> bool:
        """True when the operator declared a bind wider than loopback.

        Unset is loopback-only because that is uvicorn's own default.
        """
        candidates = self.bind_candidates()
        if not candidates:
            return False
        return any(c not in _LOOPBACK_HOSTS for c in candidates)

    def secret_configured(self) -> bool:
        """True when SWARM_SECRET has a value. The value is never returned."""
        return bool((self.secret or "").strip())

    def openai_compat_key_configured(self) -> bool:
        """True when SWARM_OPENAI_COMPAT_API_KEY has a value.

        The generic BYO endpoint's key, owned here like every other
        `SWARM_*` variable. Only the answer is exposed; the value stays
        in this object.
        """
        return bool((self.openai_compat_api_key or "").strip().strip('"').strip("'"))


def oauth_client_id(provider_id: str) -> str:
    """Per-provider OAuth client id via the one settings object.

    Provider ids are open-ended (google, github, ...), so they cannot be
    enumerated as Settings fields. The read still lives here so no other
    module does a fresh os.environ.get for a SWARM_* flag.
    """
    return (os.environ.get(f"SWARM_{provider_id.upper()}_OAUTH_CLIENT_ID") or "").strip()


def oauth_client_secret(provider_id: str) -> str:
    """Per-provider OAuth client secret, same central-read rule as above."""
    return (os.environ.get(f"SWARM_{provider_id.upper()}_OAUTH_CLIENT_SECRET") or "").strip()


# Every variable this module owns, for cache signatures and snapshots.
MANAGED_ENV_VARS: tuple[str, ...] = tuple(
    ["SWARM_" + name.upper() for name in Settings.model_fields]
)


_cache_signature: tuple[tuple[str, str | None], ...] | None = None
_cached: Settings | None = None


def _signature() -> tuple[tuple[str, str | None], ...]:
    return tuple((name, os.environ.get(name)) for name in MANAGED_ENV_VARS)


def reset_settings_cache() -> None:
    """Forget the memoised parse. Tests and `swarm doctor` use this."""
    global _cache_signature, _cached
    _cache_signature, _cached = None, None


def get_settings() -> Settings:
    """The one settings object, re-read only when a managed variable changes.

    Re-reading on every access would be wasteful; caching once at import
    would be wrong, because an operator (and most of the test suite) changes
    `os.environ` after the modules are loaded. The signature is a handful of
    dict lookups, so "cached but always correct" is cheap.
    """
    global _cache_signature, _cached
    signature = _signature()
    if signature != _cache_signature or _cached is None:
        reset_settings_cache()
        _cache_signature = signature
        _cached = Settings()
    return _cached


def validate_settings() -> Settings:
    """Return the settings, or raise `SettingsError` naming every bad variable.

    Called from the FastAPI lifespan so a typo stops the boot. Not called on
    every request: the error is an operator mistake, not a runtime condition,
    and paying for it on the hot path would only hide it.
    """
    settings = get_settings()
    problems = settings.problems()
    if problems:
        raise SettingsError(problems)
    return settings


def iter_env(names: tuple[str, ...]) -> Iterator[tuple[str, str | None]]:
    """(variable, raw value) pairs in declaration order, for reports."""
    for name in names:
        yield name, os.environ.get(name)


def _redact(name: str, value: str | None) -> dict[str, Any]:
    if name.split("_", 1)[-1].lower() in SECRET_FIELDS or name == "SWARM_SECRET":
        return {"variable": name, "set": bool((value or "").strip()), "value": None}
    return {"variable": name, "set": bool((value or "").strip()), "value": value}


def settings_snapshot() -> dict[str, Any]:
    """Every managed variable, for `swarm doctor` and bug reports.

    Secret-shaped variables report only whether they are set. A snapshot that
    could print a key would end up pasted into a public issue.
    """
    settings = get_settings()
    return {
        "problems": [p.as_dict() for p in settings.problems()],
        "variables": [_redact(name, os.environ.get(name)) for name in MANAGED_ENV_VARS],
        "effective": {
            "demo_mode": settings.demo_mode(),
            "system_tools": settings.system_enabled(),
            "system_unrestricted": settings.system_unrestricted_ok(),
            "browser_tools": settings.browser_enabled(),
            "computer_provider": settings.computer(),
            "busy_timeout_ms": settings.busy_timeout_ms(),
            "retention_days": settings.effective_retention_days(),
            "gc_max_age_hours": settings.effective_gc_max_age_hours(),
            "gc_max_mb": settings.effective_gc_max_mb(),
            "allowed_origins": settings.origin_list(),
            "bind": settings.bind_candidates(),
            "exposed_on_non_loopback": settings.exposed_on_non_loopback(),
            "secret_configured": settings.secret_configured(),
        },
        "python": {
            "version": sys.version.split()[0],
            "executable": sys.executable,
        },
    }