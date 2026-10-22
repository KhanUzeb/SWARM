"""Registration is the only unauthenticated write, so it gets its own tests.

Each test pins one door and the proof it requires. The point is that a
stranger on the network cannot mint the first administrator, rotate a member's
token, enumerate admins, or brute-force an admin password.
"""
from __future__ import annotations

import asyncio

import backend.db as db
import backend.main as main


def _clear_rate():
    main._last_write.clear()
    main._register_attempts.clear()


def _clear_password_lock():
    main._password_failures.clear()


def test_remote_first_admin_is_refused(remote_client):
    res = remote_client.post("/api/register", json={"handle": "eve"})
    assert res.status_code == 403
    assert "initialized" in res.json()["detail"].lower()
    assert asyncio.run(db.user_count()) == 0


def test_setup_token_allows_remote_first_admin(remote_client, monkeypatch):
    monkeypatch.setenv("SWARM_SETUP_TOKEN", "setup-7f3a9c")
    res = remote_client.post(
        "/api/register",
        json={"handle": "eve", "setup_token": "setup-7f3a9c"},
    )
    assert res.status_code == 200, res.text
    assert res.json()["role"] == "admin"


def test_setup_token_is_not_optional_for_remote_bootstrap(remote_client, monkeypatch):
    monkeypatch.setenv("SWARM_SETUP_TOKEN", "setup-7f3a9c")
    missing = remote_client.post("/api/register", json={"handle": "eve"})
    assert missing.status_code == 403
    wrong = remote_client.post(
        "/api/register",
        json={"handle": "eve", "setup_token": "not-the-token"},
    )
    assert wrong.status_code == 403
    assert asyncio.run(db.user_count()) == 0


def test_a_configured_setup_token_does_not_lock_out_the_local_login_screen(
    local_client, monkeypatch,
):
    """The browser sends handle+password only.

    SWARM_SETUP_TOKEN proves identity to a *remote* client (see the setting's
    description). Requiring it from loopback as well made the first-run
    screen unable to create the admin it was showing, so a local request is
    still allowed to omit it while a remote one is not.
    """
    monkeypatch.setenv("SWARM_SETUP_TOKEN", "setup-local-1")
    res = local_client.post(
        "/api/register", json={"handle": "uzeb", "password": "correct-horse"},
    )
    assert res.status_code == 200, res.text
    assert res.json()["role"] == "admin"


def test_a_configured_setup_token_still_gates_remote_setup(remote_client, monkeypatch):
    monkeypatch.setenv("SWARM_SETUP_TOKEN", "setup-local-1")
    refused = remote_client.post("/api/register", json={"handle": "eve"})
    assert refused.status_code == 403
    allowed = remote_client.post(
        "/api/register",
        json={"handle": "eve", "setup_token": "setup-local-1"},
    )
    assert allowed.status_code == 200, allowed.text
    assert allowed.json()["role"] == "admin"


def test_loopback_with_forwarding_header_is_treated_as_proxy(local_client):
    """A reverse proxy must not make a remote request look local."""
    res = local_client.post(
        "/api/register",
        json={"handle": "eve"},
        headers={"X-Forwarded-For": "203.0.113.9"},
    )
    assert res.status_code == 403
    assert asyncio.run(db.user_count()) == 0


def test_concurrent_first_registration_yields_one_admin(client):
    async def race():
        return await asyncio.gather(
            *[db.create_first_user(f"user{i}", f"token-{i}") for i in range(8)],
            return_exceptions=True,
        )

    results = asyncio.run(race())
    created = [r for r in results if isinstance(r, dict)]
    assert len(created) == 1, "exactly one bootstrap request may win"
    assert created[0]["role"] == "admin"
    # Losing requests either return None or hit the write lock; neither may
    # create a second administrator.
    assert all(
        not isinstance(r, Exception) or "locked" in str(r).lower()
        for r in results
    )
    assert asyncio.run(db.user_count()) == 1


def test_remote_member_cannot_rotate_a_token(client):
    admin = client.post("/api/register", json={"handle": "uzeb"})
    assert admin.status_code == 200
    member = client.post("/api/register", json={"handle": "maya"})
    assert member.status_code == 200

    rotated = client.post(
        "/api/register",
        json={"handle": "maya"},
        headers={"X-Forwarded-For": "203.0.113.9"},
    )
    assert rotated.status_code == 403
    assert rotated.json()["detail"] == "registration is closed"


def test_remote_member_needs_the_invite_code(client, monkeypatch):
    assert client.post("/api/register", json={"handle": "uzeb"}).status_code == 200
    monkeypatch.setenv("SWARM_INVITE_CODE", "invite-42")
    headers = {"X-Forwarded-For": "203.0.113.9"}

    assert client.post(
        "/api/register", json={"handle": "newbie"}, headers=headers,
    ).status_code == 403
    assert client.post(
        "/api/register",
        json={"handle": "newbie", "invite": "wrong"},
        headers=headers,
    ).status_code == 403
    joined = client.post(
        "/api/register",
        json={"handle": "newbie", "invite": "invite-42"},
        headers=headers,
    )
    assert joined.status_code == 200, joined.text
    assert joined.json()["role"] == "member"


def test_admin_password_brute_force_is_locked_out(local_client):
    _clear_rate()
    _clear_password_lock()
    first = local_client.post(
        "/api/register",
        json={"handle": "uzeb", "password": "correct-horse"},
    )
    assert first.status_code == 200

    for _ in range(5):
        _clear_rate()
        wrong = local_client.post(
            "/api/register",
            json={"handle": "uzeb", "password": "wrong-password"},
        )
        assert wrong.status_code == 403

    _clear_rate()
    locked = local_client.post(
        "/api/register",
        json={"handle": "uzeb", "password": "correct-horse"},
    )
    assert locked.status_code == 429


def test_admin_password_has_a_real_minimum(local_client):
    res = local_client.post(
        "/api/register", json={"handle": "uzeb", "password": "short"},
    )
    assert res.status_code == 400
    assert "8" in res.json()["detail"]


def test_anonymous_status_does_not_name_admins(client):
    assert client.post("/api/register", json={"handle": "uzeb"}).status_code == 200
    anonymous = client.get("/api/status").json()
    assert "admins" not in anonymous
    authed = client.get(
        "/api/status",
        headers={"Authorization": f"Bearer {client.post('/api/register', json={'handle': 'uzeb'}).json()['token']}"},
    )
    # The response model is the gate, not a UI convention.
    assert authed.status_code == 200
    assert authed.json()["admins"] == ["uzeb"]
