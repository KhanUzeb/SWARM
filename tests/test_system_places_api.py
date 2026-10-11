"""The Places endpoints: list, add, remove, and bind by id.

The security contract: reading places needs auth, writing them (add, remove)
is admin-only like the root rebind they feed, and env/default entries are
never removable by guessing their ids. The panel must also see saved places
on a plain listing fetch — a place you saved yesterday has to be there
tomorrow without any extra request.
"""
import pytest

import backend.main as main
from conftest import _app_client


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _register(client, handle: str) -> dict:
    res = client.post("/api/register", json={"handle": handle})
    assert res.status_code == 200
    return res.json()


def _unlimit() -> None:
    """Two writes in one test are normal; rate limiting has its own file."""
    main._last_write.clear()


@pytest.fixture
def system_client(tmp_path, monkeypatch):
    """Host tools ON so every places route is reachable."""
    monkeypatch.setenv("SWARM_SYSTEM", "1")
    yield from _app_client(tmp_path, monkeypatch)


def _list_places(client, token: str) -> list[dict]:
    res = client.get("/api/computer/system/places", headers=_auth(token))
    assert res.status_code == 200
    return res.json()["places"]


def test_places_endpoint_needs_auth(system_client):
    assert system_client.get("/api/computer/system/places").status_code in (401, 403)


def test_added_place_shows_up_in_listing_payload(system_client, tmp_path):
    """The panel reads `listing.places`, so a saved place must ride it.

    This is the regression that makes Places useless if it breaks: without
    the merge in the listing route, persisted places are invisible until the
    user adds one in the same session.
    """
    admin = _register(system_client, "uzeb")
    folder = tmp_path / "work-2026"
    folder.mkdir()

    res = system_client.post(
        "/api/computer/system/places",
        headers=_auth(admin["token"]),
        json={"path": str(folder), "label": "Work"},
    )
    assert res.status_code == 200
    saved = [p for p in res.json()["places"] if p["kind"] == "saved"]
    assert [p["label"] for p in saved] == ["Work"]

    listing = system_client.get(
        "/api/computer/system", headers=_auth(admin["token"])
    ).json()
    assert any(
        p["kind"] == "saved" and p["path"] == str(folder)
        for p in listing["places"]
    )


def test_member_cannot_add_a_place(system_client, tmp_path):
    admin = _register(system_client, "uzeb")
    member = _register(system_client, "maya")
    folder = tmp_path / "nope"
    folder.mkdir()

    res = system_client.post(
        "/api/computer/system/places",
        headers=_auth(member["token"]),
        json={"path": str(folder)},
    )
    assert res.status_code == 403


def test_place_add_rejects_non_directory(system_client, tmp_path):
    admin = _register(system_client, "uzeb")
    res = system_client.post(
        "/api/computer/system/places",
        headers=_auth(admin["token"]),
        json={"path": str(tmp_path / "missing")},
    )
    assert res.status_code == 400


def test_root_binds_by_place_id(system_client, tmp_path):
    admin = _register(system_client, "uzeb")
    folder = tmp_path / "target"
    folder.mkdir()
    added = system_client.post(
        "/api/computer/system/places",
        headers=_auth(admin["token"]),
        json={"path": str(folder), "label": "Target"},
    ).json()["places"]
    place = next(p for p in added if p["path"] == str(folder))

    _unlimit()
    res = system_client.post(
        "/api/computer/system/root",
        headers=_auth(admin["token"]),
        json={"place_id": place["id"]},
    )
    assert res.status_code == 200
    assert res.json()["root"] == str(folder)


def test_root_with_raw_path_still_works(system_client, tmp_path):
    """Older clients send only `path`; that spelling must not regress."""
    admin = _register(system_client, "uzeb")
    folder = tmp_path / "legacy"
    folder.mkdir()
    res = system_client.post(
        "/api/computer/system/root",
        headers=_auth(admin["token"]),
        json={"path": str(folder)},
    )
    assert res.status_code == 200
    assert res.json()["root"] == str(folder)


def test_root_with_unknown_place_id_is_404(system_client):
    admin = _register(system_client, "uzeb")
    res = system_client.post(
        "/api/computer/system/root",
        headers=_auth(admin["token"]),
        json={"place_id": "place-nope"},
    )
    assert res.status_code == 404


def test_remove_place_and_remove_again(system_client, tmp_path):
    admin = _register(system_client, "uzeb")
    folder = tmp_path / "temp"
    folder.mkdir()
    added = system_client.post(
        "/api/computer/system/places",
        headers=_auth(admin["token"]),
        json={"path": str(folder), "label": "Temp"},
    ).json()["places"]
    place = next(p for p in added if p["kind"] == "saved")

    _unlimit()
    res = system_client.delete(
        f"/api/computer/system/places/{place['id']}",
        headers=_auth(admin["token"]),
    )
    assert res.status_code == 200
    assert not [p for p in res.json()["places"] if p["kind"] == "saved"]

    _unlimit()
    again = system_client.delete(
        f"/api/computer/system/places/{place['id']}",
        headers=_auth(admin["token"]),
    )
    assert again.status_code == 404


def test_default_place_cannot_be_removed_by_id(system_client):
    admin = _register(system_client, "uzeb")
    places = _list_places(system_client, admin["token"])
    default = next((p for p in places if p["kind"] == "default"), None)
    assert default is not None, "the default guesses should always offer entries"
    res = system_client.delete(
        f"/api/computer/system/places/{default['id']}",
        headers=_auth(admin["token"]),
    )
    assert res.status_code == 404


def test_member_cannot_remove_a_place(system_client, tmp_path):
    admin = _register(system_client, "uzeb")
    member = _register(system_client, "maya")
    folder = tmp_path / "keep"
    folder.mkdir()
    added = system_client.post(
        "/api/computer/system/places",
        headers=_auth(admin["token"]),
        json={"path": str(folder), "label": "Keep"},
    ).json()["places"]
    place = next(p for p in added if p["kind"] == "saved")

    res = system_client.delete(
        f"/api/computer/system/places/{place['id']}",
        headers=_auth(member["token"]),
    )
    assert res.status_code == 403
