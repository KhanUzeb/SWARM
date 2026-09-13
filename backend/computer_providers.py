"""Provider-neutral computer abstraction (Rakazo-inspired).

Rakazo runs bot computers behind a provider-neutral interface
(``SANDBOX_PROVIDER=docker|e2b|daytona|box|none``) with shared Team
Computers plus isolated Private computers. Swarm runs on one host, so
this module provides the same *shape* without pretending to be a cloud
runtime:

- ``SWARM_COMPUTER_PROVIDER=local|none|fake`` selects the backend.
  ``local`` is today's sandbox + host-system tools. ``none`` boots the
  product without a computer host. ``fake`` is a deterministic emulator
  for tests only.
- Team home = the shared sandbox every bot sees (``SWARM_SANDBOX_DIR``).
- Private home = an isolated per-bot subdirectory
  (``<sandbox>/private/<agent>``), created on demand. Bots can keep
  drafts there before publishing to the shared workspace.

The API owns orchestration and provider translation; the frontend only
expresses intent (scope + path) and renders state.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

VALID_PROVIDERS = ("local", "none", "fake")

_AGENT_SEGMENT = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_-]{0,63}$")


def get_provider() -> str:
    """Return the configured computer provider (default ``local``)."""
    raw = (os.environ.get("SWARM_COMPUTER_PROVIDER") or "local").strip().lower()
    if raw in VALID_PROVIDERS:
        return raw
    return "local"


def provider_configured() -> bool:
    """False only when the operator booted without a computer host."""
    return get_provider() != "none"


def is_fake() -> bool:
    return get_provider() == "fake"


def team_home() -> Path:
    """Shared Team Computer home (today's sandbox directory)."""
    from . import agent

    root = Path(agent.SANDBOX_DIR)
    root.mkdir(parents=True, exist_ok=True)
    return root.resolve()


def private_home(agent_name: str) -> Path | None:
    """Isolated Private home for one bot, or None for a bad handle."""
    name = (agent_name or "").strip()
    if not _AGENT_SEGMENT.match(name):
        return None
    home = team_home() / "private" / name
    home.mkdir(parents=True, exist_ok=True)
    return home.resolve()


def resolve_home(scope: str = "team", agent_name: str = "") -> Path:
    """Resolve ``team`` (shared) or ``private`` (per-bot) workspace root."""
    if (scope or "").strip().lower() == "private" and agent_name:
        home = private_home(agent_name)
        if home is not None:
            return home
    return team_home()


def status() -> dict:
    """Provider-neutral status payload (additive; never raises)."""
    provider = get_provider()
    try:
        home = team_home() if provider != "none" else None
    except Exception:  # noqa: BLE001 - status must never 500
        home = None
    return {
        "provider": provider,
        "configured": provider != "none",
        "team_home": str(home) if home else None,
        "private_homes": "private/<agent> under the team home",
        "writable": bool(home and os.access(home, os.W_OK)),
        "note": (
            "local: shared sandbox + per-bot private homes on this host. "
            "none: product boots without a computer host. "
            "fake: deterministic emulator for tests."
        ),
    }
