"""Backend-core lane regression tests (worker BE).

Deterministic and offline: temp SQLite via the shared `client` fixture,
no network, no real provider keys. Each test pins one proven defect in
backend/main.py, backend/db.py, backend/security.py, backend/doctor.py or
backend/v2.py with file:line and the violated contract.
"""
from __future__ import annotations

import asyncio

import pytest

import backend.db as db_mod
from backend import v2 as v2_mod
from backend.settings import reset_settings_cache


def _run(coro):
    return asyncio.run(coro)


def _clear_rate():
    from backend import main as main_mod

    main_mod._last_write.clear()


# F1 backend/main.py:1185 GET /api/agent-templates missing auth.
# AGENTS.md section 3: every /api/* data read requires bearer except
# GET /api/status, GET /health, POST /api/register.
def test_agent_templates_requires_auth(client):
    res = client.get("/api/agent-templates")
    assert res.status_code == 401, res.text


# F2 backend/db.py search_workspace missing LIKE escaping.
# Invariant from db._like_escape + test_backend_hardening for search_memory:
# literal %, _ must not widen the match. search_workspace built
# f"%{needle}%" with no ESCAPE, so '100%' matched '100X coverage'.
def test_search_workspace_treats_wildcards_literally(client, auth):
    _clear_rate()
    client.post(
        "/api/channels/general/messages",
        json={"author": "uzeb", "body": "100% coverage"},
        headers=auth,
    )
    _clear_rate()
    client.post(
        "/api/channels/general/messages",
        json={"author": "uzeb", "body": "100X coverage"},
        headers=auth,
    )
    hits = _run(db_mod.search_workspace("100%", viewer="uzeb", limit=10))
    bodies = [h["body"] for h in hits]
    assert "100% coverage" in bodies
    assert "100X coverage" not in bodies
    hits2 = _run(db_mod.search_workspace("100_", viewer="uzeb", limit=10))
    bodies2 = [h["body"] for h in hits2]
    assert "100X coverage" not in bodies2


# F3 backend/db.py:1229 delete_message only deleted direct replies.
# With foreign_keys ON, deleting a parent with a grandchild raised
# IntegrityError (500 via API) and deleted nothing.
def test_delete_message_with_grandchild_deletes_thread(client, auth):
    _clear_rate()
    parent = client.post(
        "/api/channels/general/messages",
        json={"author": "uzeb", "body": "root"},
        headers=auth,
    ).json()
    _clear_rate()
    child = client.post(
        "/api/channels/general/messages",
        json={"author": "uzeb", "body": "child", "parent_id": parent["id"]},
        headers=auth,
    ).json()
    _clear_rate()
    grandchild = client.post(
        "/api/channels/general/messages",
        json={"author": "uzeb", "body": "grandchild", "parent_id": child["id"]},
        headers=auth,
    ).json()
    _clear_rate()
    res = client.delete(f"/api/messages/{parent['id']}", headers=auth)
    assert res.status_code == 200, res.text
    removed = set(res.json()["ids"])
    assert {parent["id"], child["id"], grandchild["id"]} <= removed
    assert _run(db_mod.get_message(grandchild["id"])) is None


# F4 backend/db.py delete_channel left channel_people orphans;
# backend/main.py api_delete_channel only refused kind=="dm".
# Invariant: deleting a channel must not leave orphan membership rows,
# and people DMs are DMs (refused by the API layer per db docstring).
def test_delete_channel_cleans_people_membership():
    async def seed():
        await db_mod.init_db()
        await db_mod.create_user("alice", "tok-alice-1")
        await db_mod.create_user("bob", "tok-bob-1")
        ch = await db_mod.ensure_people_dm("alice", "bob")
        assert ch["kind"] == "people"
        await db_mod.delete_channel(ch["id"])
        async with db_mod.open_db() as conn:
            cur = await conn.execute("SELECT * FROM channel_people WHERE channel_id=?", (ch["id"],))
            leftovers = await cur.fetchall()
        return leftovers

    assert _run(seed()) == []


def test_people_dm_cannot_be_deleted_via_api(client, auth):
    # People DMs are DMs: DELETE must 400 like a bot 1:1, not 200.
    # Setup: register bob, open alice->bob DM, then try to delete it.
    _clear_rate()
    other = client.post("/api/register", json={"handle": "bob"})
    assert other.status_code == 200, other.text
    _clear_rate()
    dm = client.post("/api/dms", json={"handle": "bob"}, headers=auth)
    assert dm.status_code == 200, dm.text
    assert dm.json()["kind"] == "people"
    ch_id = dm.json()["id"]
    _clear_rate()
    res = client.delete(f"/api/channels/{ch_id}", headers=auth)
    assert res.status_code == 400, res.text


# F5 backend/db.py update_ai_provider_oauth assumed columns that only
# backend/v2.py init_db created (auth_method, refresh_secret, expires_at).
# Invariant per section 7: one additive migration path in ensure_schema().
# db.init_db alone must be sufficient; otherwise OAuth connect 500s with
# "no column named auth_method". Uses an isolated file so the app
# lifespan (which runs v2.init_db) cannot mask the missing migration.
def test_oauth_columns_exist_after_db_init_only(tmp_path, monkeypatch):
    import pathlib as _pl

    path = tmp_path / "oauth-only.db"
    monkeypatch.setattr(db_mod, "DB_PATH", path)

    async def cols():
        await db_mod.init_db()
        async with db_mod.open_db() as conn:
            cur = await conn.execute("PRAGMA table_info(ai_providers)")
            return {row[1] for row in await cur.fetchall()}

    got = _run(cols())
    assert {"auth_method", "refresh_secret", "expires_at"} <= got


def test_oauth_update_works_without_v2_init(tmp_path, monkeypatch):
    path = tmp_path / "oauth-only2.db"
    monkeypatch.setattr(db_mod, "DB_PATH", path)

    async def attempt():
        await db_mod.init_db()
        return await db_mod.update_ai_provider_oauth(
            "google", "sealed-access", "sealed-refresh", 1234.0, model="m"
        )

    row = _run(attempt())
    assert row["provider_id"] == "google"
    assert "secret" not in row
    assert row["key_hint"]


# F6 backend/v2.py used aiosqlite.connect(db.DB_PATH) directly for every
# query, bypassing db.open_db pragmas (WAL/foreign_keys/busy_timeout).
# Invariant from db.open_db docstring. Pinned here via source: all v2
# database access must go through db.open_db.
def test_v2_uses_open_db_for_pragmas():
    import pathlib as _pl

    src = _pl.Path("backend/v2.py").read_text()
    assert "aiosqlite.connect(db.DB_PATH)" not in src
    assert "db.open_db()" in src or "open_db()" in src


# F7 error shape: ApiError is {"ok": False, "detail": ...}.
# security.install_api_guard returned {"detail":...} without ok,
# main._unhandled returned {"ok":False,"message":...} (wrong key).
def test_origin_guard_errors_carry_ok_false(client, auth):
    res = client.get(
        "/api/channels",
        headers={**auth, "Origin": "https://evil.example", "X-Swarm-Client": "web"},
    )
    assert res.status_code == 403
    body = res.json()
    assert body["ok"] is False
    assert "detail" in body


# security.py:143 returns {"detail": ...} without "ok". To reach THAT branch
# the origin must pass origin_allowed first, so the request has to name the
# host it was sent to: TestClient sends Host: testserver, and same-host is
# not cross-origin. An Origin outside the allowlist stops at the earlier
# "origin not allowed" branch, which is what the previous version of this
# test actually asserted (it proved nothing about the client-marker branch).
def test_missing_client_header_errors_carry_ok_false(client, auth):
    res = client.get(
        "/api/channels",
        headers={**auth, "Origin": "http://testserver"},
    )
    assert res.status_code == 403, res.text
    body = res.json()
    assert body["ok"] is False
    assert body["detail"] == "forbidden client"


def test_unhandled_errors_use_detail_not_message():
    import asyncio as _aio

    from fastapi import Request

    from backend import main as main_mod

    async def call():
        scope = {"type": "http", "method": "GET", "path": "/", "headers": []}
        req = Request(scope)
        resp = await main_mod._unhandled_exception_json(req, RuntimeError("boom"))
        import json as _json

        payload = _json.loads(resp.body.decode())
        return resp.status_code, payload

    status, payload = _aio.run(call())
    assert status == 500
    assert payload["ok"] is False
    assert "detail" in payload
    assert "message" not in payload


# F8 WS bypass: install_api_guard skips non-/api/ paths, and neither
# ws_channel nor ws_v2_run checked Origin. Evil browser origin must not
# get chat service: after the fix the socket closes instead of echoing.
def test_ws_rejects_forbidden_origin(client, token):
    from starlette.websockets import WebSocketDisconnect

    _clear_rate()
    try:
        with client.websocket_connect(
            "/ws/general", headers={"Origin": "https://evil.example"}
        ) as ws:
            ws.send_json({"token": token, "last_seen_id": None})
            # If the origin is (incorrectly) allowed, this chat gets service
            # and the next receive returns our own message. If fixed, the
            # server has already closed and send/receive raises disconnect.
            try:
                ws.send_json({"body": "evil hello"})
                first = ws.receive_json()
            except WebSocketDisconnect as exc:
                assert exc.code in (4001, 4003, 4403, 1008)
                return
            # Allowed path: we got channel service for an evil origin -> bug.
            assert first.get("type") == "error", first
    except WebSocketDisconnect as exc:
        # Fixed path: close during handshake (4003) surfaces here.
        assert exc.code in (4001, 4003, 4403, 1008)
        return


# Doctor shape: run_doctor must always contain "providers" (stable shape
# like "connectivity"), even when per-provider rows exist.
def test_doctor_report_always_contains_providers(monkeypatch, tmp_path):
    import backend.db as dbm
    from backend import doctor as doctor_mod
    from backend import settings as settings_mod

    for name in settings_mod.MANAGED_ENV_VARS:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("SWARM_SANDBOX_DIR", str(tmp_path / "sandbox"))
    monkeypatch.setenv("SWARM_DB_PATH", str(tmp_path / "swarm.db"))
    monkeypatch.setenv("PYTHON_DOTENV_DISABLED", "1")
    monkeypatch.setenv("GROQ_API_KEY", "gsk_test_not_a_real_key")
    for extra in ("HF_TOKEN", "OPENAI_API_KEY", "ANTHROPIC_API_KEY"):
        monkeypatch.delenv(extra, raising=False)
    original = dbm.DB_PATH
    dbm.DB_PATH = tmp_path / "provider-store.db"
    import asyncio as _aio

    _aio.run(dbm.init_db())
    reset_settings_cache()
    try:
        names = {r["check"] for r in doctor_mod.run_doctor()["checks"]}
        assert "providers" in names
        assert "provider:groq" in names
    finally:
        dbm.DB_PATH = original
        reset_settings_cache()


# ---------------------------------------------------------------- settings --
# N1: AGENTS.md s4 names SWARM_OPENAI_COMPAT_API_KEY as the generic BYO
# endpoint's key, and s2 says every SWARM_* variable is read through
# backend/settings.py. The field was missing, so the catalog's only
# SWARM_-prefixed env fallback fell through main._key_set's raw-env branch
# (and doctor.check_providers read it with os.environ directly): one flag
# bypassing the one settings object, and absent from `swarm doctor`'s
# variable list and cache signature.
def test_custom_provider_key_is_owned_by_the_settings_object(monkeypatch):
    from backend.settings import (
        MANAGED_ENV_VARS, get_settings, reset_settings_cache, settings_snapshot,
    )

    monkeypatch.setenv("SWARM_OPENAI_COMPAT_API_KEY", "sk-compat-not-a-real-key")
    reset_settings_cache()
    try:
        assert "SWARM_OPENAI_COMPAT_API_KEY" in MANAGED_ENV_VARS
        assert (get_settings().openai_compat_api_key or "").strip() == "sk-compat-not-a-real-key"
        assert get_settings().openai_compat_key_configured() is True
    finally:
        reset_settings_cache()


# N2: the same variable, once owned by Settings, reaches
# settings_snapshot()["variables"], which `swarm doctor` prints verbatim.
# A snapshot that can print a key ends up pasted into a public issue, so
# the compat key must report set/not-set only.
def test_snapshot_never_prints_the_custom_provider_key(monkeypatch):
    import json

    from backend.settings import reset_settings_cache, settings_snapshot

    monkeypatch.setenv("SWARM_OPENAI_COMPAT_API_KEY", "sk-compat-secret-value")
    reset_settings_cache()
    try:
        snapshot = settings_snapshot()
        assert "sk-compat-secret-value" not in json.dumps(snapshot)
        row = next(v for v in snapshot["variables"]
                   if v["variable"] == "SWARM_OPENAI_COMPAT_API_KEY")
        assert row["set"] is True
        assert row["value"] is None
    finally:
        reset_settings_cache()


# N3: main._key_set must report the custom endpoint's readiness from the
# settings object, not from a raw env read in main.py. The env var is set
# but the settings object says the key is absent: a caller that bypasses
# Settings answers "configured", one that reads through it must agree with
# the object and say "not configured".
def test_key_set_reads_custom_provider_key_through_settings(monkeypatch):
    from backend import main as main_mod
    from backend.settings import Settings, reset_settings_cache

    monkeypatch.setenv("SWARM_OPENAI_COMPAT_API_KEY", "sk-compat-not-a-real-key")
    reset_settings_cache()
    # Init kwargs outrank env in pydantic-settings, so this object holds a
    # blank key while os.environ still has one.
    monkeypatch.setattr(
        main_mod, "get_settings", lambda: Settings(openai_compat_api_key=None)
    )
    try:
        assert main_mod._key_set("SWARM_OPENAI_COMPAT_API_KEY") is False
        assert main_mod._key_set("SWARM_OPENAI_COMPAT_BASE_URL") is False
    finally:
        reset_settings_cache()


def test_key_set_reads_a_supplied_custom_key_as_present(monkeypatch):
    from backend import main as main_mod
    from backend.settings import Settings, reset_settings_cache

    reset_settings_cache()
    monkeypatch.setattr(
        main_mod,
        "get_settings",
        lambda: Settings(openai_compat_api_key="  sk-compat-not-a-real-key  "),
    )
    try:
        assert main_mod._key_set("SWARM_OPENAI_COMPAT_API_KEY") is True
    finally:
        reset_settings_cache()


# N4: doctor.check_providers reports the generic endpoint through the same
# read. Same trick as N3: the env var is set, the settings object is blank,
# so the custom row must not appear — and no key ever reaches the report.
def test_doctor_reports_custom_provider_without_the_key(monkeypatch, tmp_path):
    import json

    import backend.db as dbm
    from backend import doctor as doctor_mod
    from backend.settings import Settings, reset_settings_cache

    original = dbm.DB_PATH
    dbm.DB_PATH = tmp_path / "provider-store.db"
    monkeypatch.setenv("SWARM_OPENAI_COMPAT_API_KEY", "sk-compat-secret-value")
    monkeypatch.setattr(doctor_mod, "get_settings", lambda: Settings(openai_compat_api_key=None))
    reset_settings_cache()
    try:
        rows = doctor_mod.check_providers()
        assert "provider:custom" not in {r["check"] for r in rows}
        assert "sk-compat-secret-value" not in json.dumps(rows)
    finally:
        dbm.DB_PATH = original
        reset_settings_cache()


def test_doctor_reports_custom_provider_via_the_settings_key(monkeypatch, tmp_path):
    import backend.db as dbm
    from backend import doctor as doctor_mod
    from backend.settings import Settings, reset_settings_cache

    original = dbm.DB_PATH
    dbm.DB_PATH = tmp_path / "provider-store.db"
    monkeypatch.setattr(
        doctor_mod,
        "get_settings",
        lambda: Settings(openai_compat_api_key="sk-compat-not-a-real-key"),
    )
    reset_settings_cache()
    try:
        rows = doctor_mod.check_providers()
        custom = [r for r in rows if r["check"] == "provider:custom"]
        assert custom and custom[0]["status"] == doctor_mod.OK
        assert "SWARM_OPENAI_COMPAT_API_KEY" in custom[0]["detail"]
    finally:
        dbm.DB_PATH = original
        reset_settings_cache()


# Settings: SWARM_OAUTH_REDIRECT_URI blank must fall back to default,
# not produce an empty redirect (reads via Settings, not raw env).
def test_blank_oauth_redirect_falls_back_to_default(monkeypatch):
    monkeypatch.setenv("SWARM_GOOGLE_OAUTH_CLIENT_ID", "cid")
    monkeypatch.setenv("SWARM_OAUTH_REDIRECT_URI", "")
    reset_settings_cache()
    try:
        url = v2_mod.oauth_start(
            "google", "uzeb", {"oauth_authorize_url": "https://x.example/auth"}
        )
        assert "redirect_uri=" in url
        # The default callback must survive an empty setting, url-encoded.
        assert "localhost%3A8000" in url, url
    finally:
        reset_settings_cache()


# ------------------------------------------------------------------- async --
# N5: gc.py's module docstring promises "never blocking a request", but
# _gc_loop called gc_sweep() inline. gc_sweep is a full synchronous
# os.walk of the sandbox plus up to 512 unlinks, so every sweep held the
# event loop and stalled every in-flight request for its whole duration.
# The sweep must run off-loop.
def test_gc_sweep_runs_off_the_event_loop(monkeypatch):
    import threading

    from backend import gc as gc_mod
    from backend import main as main_mod

    seen: dict[str, int] = {}

    def blocking_sweep() -> dict[str, object]:
        seen["thread"] = threading.get_ident()
        return {"deleted": 0}

    monkeypatch.setattr(gc_mod, "gc_sweep", blocking_sweep)
    monkeypatch.setattr(main_mod, "GC_SWEEP_INTERVAL_SECONDS", 0)

    async def drive() -> int:
        task = asyncio.create_task(main_mod._gc_loop())
        for _ in range(200):
            await asyncio.sleep(0.01)
            if seen:
                break
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        return seen.get("thread", -1)

    sweep_thread = _run(drive())
    assert sweep_thread != -1, "the GC sweep never ran"
    assert sweep_thread != threading.get_ident(), (
        "gc_sweep ran on the event-loop thread, so it blocks requests"
    )


# ------------------------------------------------------- ws handshake shape -
# N6: AGENTS.md s3 — "first frame must be {"token", "last_seen_id"} or the
# server closes with 4001". parse_token() tested `":" not in raw` on whatever
# the client sent, so a non-string token raised TypeError: the WS handler at
# main.py:1966 let it escape instead of closing the socket. An unauthenticated
# client could put a traceback in the log with a one-line frame, and a native
# client sending a numeric token got an abnormal close instead of 4001.
# (A non-dict frame was already rejected by the isinstance() check; those
# cases are kept to hold that line.)
def test_ws_handshake_with_non_string_token_closes_4001(client):
    from starlette.websockets import WebSocketDisconnect

    for bad_token in (12345, ["a:b"], {"nested": 1}, True):
        try:
            with client.websocket_connect("/ws/general") as ws:
                ws.send_json({"token": bad_token, "last_seen_id": None})
                try:
                    ws.receive_json()
                    raise AssertionError("expected the socket to close")
                except WebSocketDisconnect as exc:
                    assert exc.code == 4001, f"token={bad_token!r} closed {exc.code}"
        except WebSocketDisconnect as exc:
            assert exc.code == 4001, f"token={bad_token!r} closed {exc.code}"


def test_ws_handshake_with_a_non_dict_frame_closes_4001(client):
    from starlette.websockets import WebSocketDisconnect

    for frame in ("just-a-string", ["token", "x"], 7, None):
        try:
            with client.websocket_connect("/ws/general") as ws:
                ws.send_json(frame)
                try:
                    ws.receive_json()
                    raise AssertionError("expected the socket to close")
                except WebSocketDisconnect as exc:
                    assert exc.code == 4001, f"frame={frame!r} closed {exc.code}"
        except WebSocketDisconnect as exc:
            assert exc.code == 4001, f"frame={frame!r} closed {exc.code}"


# The run-events socket must answer the same malformed frame the same way.
def test_v2_run_ws_handshake_with_non_string_token_closes_4001(client, token):
    from starlette.websockets import WebSocketDisconnect

    run = None
    _clear_rate()
    created = client.post("/api/v2/workflows", json={"name": "wf"}, headers={"Authorization": f"Bearer {token}"}).json()
    _clear_rate()
    run = client.post(
        "/api/v2/runs",
        json={"objective": "check the handshake", "workflow_id": created["id"]},
        headers={"Authorization": f"Bearer {token}"},
    ).json()
    try:
        with client.websocket_connect(f"/api/v2/ws/runs/{run['id']}") as ws:
            ws.send_json({"token": 12345})
            try:
                ws.receive_json()
                raise AssertionError("expected the socket to close")
            except WebSocketDisconnect as exc:
                assert exc.code == 4001, exc.code
    except WebSocketDisconnect as exc:
        assert exc.code == 4001, exc.code


def test_parse_token_rejects_non_strings():
    from backend.security import parse_token

    assert parse_token("uzeb:raw") == ("uzeb", "raw")
    for bad in (None, 12345, 12.5, True, ["a:b"], {"a": "b"}, "", ":raw", "uzeb:", ":"):
        assert parse_token(bad) is None, bad


# ------------------------------------------------------------- api copy ----
# N7: GET /api/computer told the user "Change that folder from System →
# Places". There is no such navigation path: PANEL_GROUPS (frontend/src/
# components/ComputerPanel.jsx) ships a "Places" destination whose tabs are
# Sandbox / System / Browser, and the root picker is the System tab's place
# chips plus the "Go" address bar. The copy named the destination and its
# tab backwards, sending operators looking for a menu that does not exist.
def test_computer_note_names_the_real_navigation_path(client, auth):
    res = client.get("/api/computer", headers=auth)
    assert res.status_code == 200, res.text
    note = res.json()["note"]
    assert "Places → System" in note, note
    assert "System → Places" not in note, note
