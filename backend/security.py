"""Browser-facing API guards: origin allowlist and optional session auth."""
from __future__ import annotations

import os
from typing import Callable

from fastapi import Header, HTTPException, Request
from starlette.responses import JSONResponse

from . import db

SWARM_CLIENT_HEADER = "web"


def allowed_origins() -> set[str]:
    raw = (os.environ.get("SWARM_ALLOWED_ORIGINS") or "").strip()
    if raw:
        return {o.strip() for o in raw.split(",") if o.strip()}
    return {
        "http://localhost:8000",
        "http://127.0.0.1:8000",
        "http://localhost:5173",
        "http://127.0.0.1:5173",
    }


def origin_allowed(origin: str | None) -> bool:
    if not origin:
        return True
    return origin.rstrip("/") in {o.rstrip("/") for o in allowed_origins()}


def parse_token(raw: str) -> tuple[str, str] | None:
    if ":" not in raw:
        return None
    handle, _, token = raw.partition(":")
    if not handle or not token:
        return None
    return handle, token


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


def install_api_guard(app) -> None:
    """Reject browser calls from unknown origins or without the swarm client marker."""

    @app.middleware("http")
    async def api_guard(request: Request, call_next: Callable):  # type: ignore[type-arg]
        path = request.url.path
        if not path.startswith("/api/"):
            return await call_next(request)

        origin = request.headers.get("origin")
        if origin and not origin_allowed(origin):
            return JSONResponse({"detail": "origin not allowed"}, status_code=403)

        # Browser cross-origin calls must identify as the swarm web app.
        if origin and request.headers.get("x-swarm-client") != SWARM_CLIENT_HEADER:
            return JSONResponse({"detail": "forbidden client"}, status_code=403)

        return await call_next(request)
