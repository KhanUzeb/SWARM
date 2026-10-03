"""Rebinding the host-system root is an admin action.

`POST /api/computer/system/root` decides which directory on the host the
`system_*` tools may touch. It used to require only `require_auth`, so any
logged-in member could repoint every other member's host tools at a directory
of their choosing — a privilege escalation on a route that sits next to a
dozen other admin-only writes.
"""
from __future__ import annotations

import pytest

from conftest import _app_client


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _register(client, handle: str) -> dict:
    res = client.post("/api/register", json={"handle": handle})
    assert res.status_code == 200, res.text
    return res.json()


@pytest.fixture
def member_client(tmp_path, monkeypatch):
    """App booted with host tools ON so the route is reachable at all."""
    monkeypatch.setenv("SWARM_SYSTEM", "1")
    yield from _app_client(tmp_path, monkeypatch)


def test_member_cannot_rebind_system_root(member_client, tmp_path):
    admin = _register(member_client, "uzeb")
    member = _register(member_client, "maya")
    assert member["role"] == "member"

    target = tmp_path / "elsewhere"
    target.mkdir()

    res = member_client.post(
        "/api/computer/system/root",
        headers=_auth(member["token"]),
        json={"path": str(target)},
    )
    assert res.status_code == 403
    assert "admin" in res.json()["detail"].lower()


def test_admin_can_rebind_system_root(member_client, tmp_path):
    admin = _register(member_client, "uzeb")
    target = tmp_path / "elsewhere"
    target.mkdir()

    res = member_client.post(
        "/api/computer/system/root",
        headers=_auth(admin["token"]),
        json={"path": str(target)},
    )
    assert res.status_code == 200


def test_system_root_rejects_non_admin_before_validating_path(member_client):
    """A member must be rejected on role, not on whether the path parses."""
    admin = _register(member_client, "uzeb")
    member = _register(member_client, "maya")

    res = member_client.post(
        "/api/computer/system/root",
        headers=_auth(member["token"]),
        json={"path": "/definitely/not/a/real/path"},
    )
    assert res.status_code == 403