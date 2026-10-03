"""Browser-facing API guards: origin allowlist and optional session auth."""
from __future__ import annotations

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


def parse_token(raw: str) -> tuple[str, str] | None:
    if ":" not in raw:
        return None
    handle, _, token = raw.partition(":")
    if not handle or not token:
        return None
    return handle, token


def is_loopback(request: Request) -> bool:
    """True for a browser on this machine (uvicorn on 127.0.0.1 / localhost)."""
    host = (request.client.host if request.client else "") or ""
    return host in {"127.0.0.1", "::1", "localhost", "::ffff:127.0.0.1"}


async def optional_auth(authorization: str | None = Header(default=None)) -> str | None:
    if not authorization or not authorization.startswith("Bearer "):
        return None
    parsed = parse_token(authorization.removeprefix("Bearer ").strip())
    if parsed is None:
        return None
    handle, token = parsed
    if not await db.verify_token(handle, token):
        return None
    return handle


async def require_auth(authorization: str | None = Header(default=None)) -> str:
    handle = await optional_auth(authorization)
    if handle is None:
        raise HTTPException(401, "missing or invalid bearer token")
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
            return JSONResponse({"detail": "forbidden client"}, status_code=403)

        return await call_next(request)
