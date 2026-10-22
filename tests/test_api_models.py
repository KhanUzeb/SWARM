"""The approvals slice of the API, modelled — and proven backward compatible.

Two things are under test here.

The **models**: `ApprovalOut` / `ApprovalResolvedOut` describe every row the
approvals routes return, so a column or a type change has to be acknowledged
in code instead of silently reaching the UI.

The **compatibility**: the approvals inbox (frontend/src/work/approvalsModel.ts
and components/ApprovalsInbox.tsx) reads these rows all day. A modelled
response is only worth having if the wire format did not move, so these tests
pin the exact key set and JSON types the frontend already depends on, and pin
the two added fields as additive.
"""

from __future__ import annotations

import pytest

from backend import db as db_mod
from backend import main as main_mod
from backend.models import ApprovalOut, ApprovalResolvedOut


@pytest.fixture
def approver(tmp_path, monkeypatch):
    """A client on its own token, plus a minted approval to act on.

    Deliberately not conftest's `token`/`auth`: that fixture boots a second app
    on a second event loop, which tears down noisily. One client, one loop.
    """
    from fastapi.testclient import TestClient

    db_path = tmp_path / "swarm.db"
    monkeypatch.setenv("SWARM_DB_PATH", str(db_path))
    db_mod.DB_PATH = db_path
    main_mod._last_write.clear()
    main_mod._register_attempts.clear()
    main_mod._password_failures.clear()
    main_mod.hub._rooms.clear()
    main_mod.hub._presence.clear()

    with TestClient(main_mod.app, client=("127.0.0.1", 50000)) as client:
        res = client.post("/api/register", json={"handle": "uzeb"})
        assert res.status_code == 200
        headers = {"Authorization": f"Bearer {res.json()['token']}"}
        client.portal.call(db_mod.init_db)

        async def make(action="send outreach", detail="email Dana", agent="swarm", channel="dm-swarm"):
            return await db_mod.create_approval(agent, channel, action, detail)

        yield client, headers, make

    main_mod.hub._rooms.clear()
    main_mod.hub._presence.clear()
    main_mod._last_write.clear()


def _wait(client, seconds):
    """Outlive the 500ms write limit so a second write is not throttled."""
    import time

    time.sleep(seconds)


# ------------------------------------------------------------ list shape --

def test_list_returns_the_modelled_row_shape(approver):
    client, headers, make = approver
    row = client.portal.call(make)
    res = client.get("/api/approvals", headers=headers)
    assert res.status_code == 200
    body = res.json()
    assert isinstance(body, list)
    match = next(a for a in body if a["id"] == row["id"])
    assert ApprovalOut(**match).model_dump() == match
    # Wire keys are exactly what the UI reads — no subset, no superset.
    assert set(match) == {
        "id", "agent_name", "channel_id", "action", "detail",
        "status", "created_at", "resolved_at",
    }
    assert isinstance(match["id"], int)
    assert isinstance(match["created_at"], float)
    assert match["resolved_at"] is None


def test_an_empty_list_is_an_empty_json_array(approver):
    client, headers, make = approver
    row = client.portal.call(make)
    _wait(client, main_mod.RATE_LIMIT_SECONDS + 0.05)
    client.post(f"/api/approvals/{row['id']}/resolve", json={"status": "approved"}, headers=headers)
    resolved = next(
        a for a in client.get("/api/approvals?status=approved", headers=headers).json()
        if a["id"] == row["id"]
    )
    assert isinstance(resolved["resolved_at"], float)
    assert resolved["resolved_at"] >= resolved["created_at"]


def test_an_empty_list_is_an_empty_json_array(approver):
    """Not null, not {}. ApprovalsInbox maps over this without a guard."""
    client, headers, _make = approver
    res = client.get("/api/approvals", headers=headers)
    assert res.status_code == 200
    assert res.json() == []


# --------------------------------------------------------- resolve shape --

def test_resolve_keeps_the_row_and_adds_the_decision(approver):
    client, headers, make = approver
    row = client.portal.call(make)
    res = client.post(
        f"/api/approvals/{row['id']}/resolve",
        json={"status": "approved"},
        headers=headers,
    )
    assert res.status_code == 200
    body = res.json()
    assert set(body) == {
        "id", "agent_name", "channel_id", "action", "detail", "status",
        "created_at", "resolved_at", "decision", "resolved_by",
    }
    assert ApprovalResolvedOut(**body).model_dump() == body
    assert body["decision"] == "approved"
    assert body["resolved_by"] == "uzeb"


# ------------------------------------------------------- unchanged errors --

def test_the_error_shape_is_still_ok_false_and_detail(approver):
    """Modelling responses must not move the error contract the UI reads.

    App.jsx does `res.data?.detail || "Approval failed"` on every approval
    call, so `detail` has to survive on the failure paths too.
    """
    client, headers, _make = approver
    for path, payload, expected in (
        ("/api/approvals/999999/resolve", {"status": "approved"}, 404),
        ("/api/approvals/999999/resolve", {"status": "maybe"}, 422),
    ):
        res = client.post(path, json=payload, headers=headers)
        assert res.status_code == expected, (path, payload, res.status_code)
        body = res.json()
        assert body["ok"] is False
        assert "detail" in body


def test_resolving_twice_is_a_409_with_the_same_shape(approver):
    client, headers, make = approver
    row = client.portal.call(make)
    first = client.post(
        f"/api/approvals/{row['id']}/resolve",
        json={"status": "approved"},
        headers=headers,
    )
    assert first.status_code == 200
    _wait(client, main_mod.RATE_LIMIT_SECONDS + 0.05)
    second = client.post(
        f"/api/approvals/{row['id']}/resolve",
        json={"status": "denied"},
        headers=headers,
    )
    assert second.status_code == 409
    assert second.json()["ok"] is False


def test_auth_is_still_required(approver):
    client, _headers, make = approver
    client.portal.call(make)
    assert client.get("/api/approvals").status_code == 401


def test_the_filter_still_narrows_the_list(approver):
    client, headers, make = approver
    pending = client.portal.call(make, "post the release note")
    _wait(client, main_mod.RATE_LIMIT_SECONDS + 0.05)
    client.post(
        f"/api/approvals/{pending['id']}/resolve",
        json={"status": "approved"},
        headers=headers,
    )
    _wait(client, main_mod.RATE_LIMIT_SECONDS + 0.05)
    still_pending = client.portal.call(make, "another outward action")
    ids = [a["id"] for a in client.get("/api/approvals?status=pending", headers=headers).json()]
    assert still_pending["id"] in ids
    assert pending["id"] not in ids


def test_a_rejected_body_gets_the_same_error_shape():
    """The 422 was the last /api response without `ok: false`.

    One error shape means a client branches on `ok` once instead of guessing
    per endpoint, which is what the audits asked for.
    """
    from backend.models import ApiError

    assert ApiError(ok=False, detail="x").ok is False
    assert ApiError(detail="x").ok is False