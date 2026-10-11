"""Browser-facing API guards: origin allowlist and optional session auth."""
from __future__ import annotations

import ipaddress
from typing import Callable
from urllib.parse import urlsplit

from fastapi import Depends, Header, HTTPException, Request
from starlette.responses import JSONResponse

from . import db
from .settings import get_settings

SWARM_CLIENT_HEADER = "web"

DEFAULT_ALLOWED_ORIGINS = frozenset({
    "http://localhost:8000",
    "http://127.0.0.1:8000",
    "http://localhost:5173",
    "http://127.0.0.1:5173",
})


def allowed_origins() -> set[str]:
    """Configured origins, or the localhost defaults when unset.

    Parsed by backend.settings so this guard and CORSMiddleware read the same
    list from the same parse. A bare "*" is dropped on the way out: it is
    paired with allow_credentials=True, which would reflect any origin onto
    credentialed responses, while origin_allowed() treats "*" as a literal
    string that never matches — so keeping it would leave one layer open and
    the other shut.
    """
    configured = get_settings().origin_list()
    if configured:
        return set(configured)
    if (get_settings().allowed_origins or "").strip():
        # Only bare "*" was set, so the operator named a wildcard and nothing
        # else. Same-host still works via origin_allowed(); cross-origin does not.
        return set()
    return set(DEFAULT_ALLOWED_ORIGINS)


def origin_allowed(origin: str | None, request_host: str | None = None) -> bool:
    """Allow configured origins, plus the host the request was actually sent to.

    Same-host is not cross-origin: the browser is reporting that the page came
    from the same server now answering it. A self-hoster on http://my-box.lan:8000
    otherwise has to invent an env var for their own machine's name, which is
    the most common reason a local install looks broken. Anything else still
    has to be in SWARM_ALLOWED_ORIGINS.
    """
    if not origin:
        return True
    normalized = origin.rstrip("/")
    if normalized in {o.rstrip("/") for o in allowed_origins()}:
        return True
    if not request_host:
        return False
    try:
        origin_host = urlsplit(normalized).netloc
    except ValueError:
        return False
    if not origin_host:
        return False
    # Compare host:port exactly. localhost and 127.0.0.1 are different origins
    # to a browser, so they do not match each other here.
    return origin_host.lower() == request_host.lower()


def parse_token(raw: object) -> tuple[str, str] | None:
    """Split a composite `<handle>:<raw>` token, or return None.

    `raw` is untrusted JSON, not a header: the WebSocket handshake feeds
    whatever the client sent straight in. `":" not in raw` raises TypeError
    on a number, a list or a map, so the type is checked first and a
    non-string is simply "not a token" — the caller closes with 4001
    instead of the exception escaping the WS handler (AGENTS.md s3).
    """
    if not isinstance(raw, str) or ":" not in raw:
        return None
    handle, _, token = raw.partition(":")
    if not handle or not token:
        return None
    return handle, token


def _peer_is_loopback(host: str) -> bool:
    """Whole loopback range, not just the four spellings in a literal set."""
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        # "localhost" never resolves to itself in a peer address, but uvicorn
        # behind a name-based proxy can report it; keep the string fallback.
        return host == "localhost"


def is_loopback(request: Request) -> bool:
    """True for a browser on this machine (uvicorn on 127.0.0.1 / localhost)."""
    host = (request.client.host if request.client else "") or ""
    if not _peer_is_loopback(host):
        return False
    # Defect: a same-host reverse proxy makes every internet client look like
    # 127.0.0.1, which handed the loopback-only register privilege (reclaim
    # any handle, including a passwordless admin) to the whole internet. A
    # forwarding header naming a non-loopback client revokes that trust; it can
    # never grant it, because the peer still has to be loopback.
    forwarded = request.headers.get("x-forwarded-for") or ""
    for entry in forwarded.split(","):
        candidate = entry.strip()
        if candidate and not _peer_is_loopback(candidate):
            return False
    return True


async def optional_auth(authorization: str | None = Header(default=None)) -> str | None:
    # Defect: the scheme was matched case-sensitively, so a client sending the
    # RFC 9110-legal "bearer <token>" was silently treated as anonymous.
    value = (authorization or "").strip()
    scheme, _, rest = value.partition(" ")
    if scheme.lower() != "bearer":
        return None
    parsed = parse_token(rest.strip())
    if parsed is None:
        return None
    handle, token = parsed
    if not await db.verify_token(handle, token):
        return None
    return handle


async def require_auth(authorization: str | None = Header(default=None)) -> str:
    handle = await optional_auth(authorization)
    if handle is None:
        # Defect: a 401 with no WWW-Authenticate challenge is not a 401 a
        # client can act on (RFC 9110 15.5.2); the exception handler was
        # dropping the header with every other one on the exception.
        raise HTTPException(
            401,
            "missing or invalid bearer token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return handle


async def require_admin(handle: str = Depends(require_auth)) -> str:
    role = await db.get_user_role(handle)
    if role != "admin":
        raise HTTPException(403, "admin only")
    return handle


def install_api_guard(app) -> None:
    """Reject browser calls from unknown origins or without the swarm client marker."""

    @app.middleware("http")
    async def api_guard(request: Request, call_next: Callable):  # type: ignore[type-arg]
        path = request.url.path
        if not path.startswith("/api/"):
            return await call_next(request)

        origin = request.headers.get("origin")
        request_host = request.headers.get("host")
        if origin and not origin_allowed(origin, request_host):
            # Say what to do, or the next two hours go into guessing. The guard
            # stays strict - only the error message changes.
            return JSONResponse(
                {
                    "ok": False,
                    "detail": (
                        f"origin not allowed: {origin}. Add it to "
                        "SWARM_ALLOWED_ORIGINS (comma-separated) and restart, e.g. "
                        "SWARM_ALLOWED_ORIGINS=http://localhost:8000,"
                        "http://127.0.0.1:8000,http://localhost:5173,"
                        "http://127.0.0.1:5173"
                    ),
                    "origin": origin,
                    "allowed": sorted(allowed_origins()),
                },
                status_code=403,
            )

        # Browser cross-origin calls must identify as the swarm web app.
        if origin and request.headers.get("x-swarm-client") != SWARM_CLIENT_HEADER:
            return JSONResponse({"ok": False, "detail": "forbidden client"}, status_code=403)

        return await call_next(request)
