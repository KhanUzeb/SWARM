"""Places: saved folders, env folders, and the default guesses.

Places are how the host tools get pointed at any directory on the machine
instead of only the project (AGENTS.md s5). The model lives in
`backend/tools/system.py`; these tests pin the three sources, the merge rule,
and the persistence semantics.
"""
import asyncio
import json

import backend.db as db
import backend.main as main
from backend.tools import system as system_mod


def _run(coro):
    return asyncio.run(coro)


def _admin(client) -> dict:
    res = client.post("/api/register", json={"handle": "uzeb"})
    assert res.status_code == 200
    return res.json()


def _meta(key):
    async def read():
        return await db.get_workspace_meta(key)

    return _run(read())


# ------------------------------------------------------------- the sources --
def test_env_places_are_offered_read_only(client, monkeypatch, tmp_path):
    """SWARM_SYSTEM_PLACES entries appear with kind=env and an auto label."""
    monkeypatch.setenv("SWARM_SYSTEM_PLACES", str(tmp_path))
    rows = system_mod._env_places()
    assert [r["kind"] for r in rows] == ["env"]
    assert rows[0]["path"] == str(tmp_path)
    assert rows[0]["label"] == tmp_path.name


def test_env_places_parse_separators_and_dedupe(client, monkeypatch, tmp_path):
    a = tmp_path / "a"
    b = tmp_path / "b"
    a.mkdir()
    b.mkdir()
    monkeypatch.setenv("SWARM_SYSTEM_PLACES", f"{a};{b},{a}")
    rows = system_mod._env_places()
    assert sorted(r["path"] for r in rows) == sorted([str(a), str(b)])


def test_default_places_are_typed(client):
    rows = system_mod._default_places()
    assert rows
    assert {r["kind"] for r in rows} == {"default"}


def test_settings_split_stays_platform_safe(client):
    """On Windows a drive-letter colon is not a separator (settings parse)."""
    import os

    os.environ.pop("SWARM_SYSTEM_PLACES", None)
    from backend.settings import Settings

    raw = r"C:\Users\me\docs" if system_mod.os.name == "nt" else "/home/me/docs"
    assert Settings(system_places=raw).system_places_list() == [raw]


# ------------------------------------------------------------ saved places --
def test_saved_place_persists_across_a_reload(client, tmp_path):
    folder = tmp_path / "notes"
    folder.mkdir()
    _run(system_mod.save_place(str(folder), "Notes"))
    # A second call reads back from the DB the way a fresh process would.
    async def read_back():
        return await system_mod._saved_places()

    rows = _run(read_back())
    assert [(r["label"], r["kind"]) for r in rows] == [("Notes", "saved")]
    assert _meta(system_mod._META_PLACES_KEY) is not None


def test_saving_the_same_folder_twice_relabels_it(client, tmp_path):
    folder = tmp_path / "notes"
    folder.mkdir()
    _run(system_mod.save_place(str(folder), "First"))
    second = _run(system_mod.save_place(str(folder), "Second"))
    assert second["ok"]

    async def rows():
        return await system_mod.places()

    merged = _run(rows())
    saved = [r for r in merged if r["kind"] == "saved"]
    assert len(saved) == 1
    assert saved[0]["label"] == "Second"


def test_saved_place_survives_a_deleted_folder_only_until_validated(client, tmp_path):
    """A place whose folder vanished drops out of the list, not the list itself."""
    folder = tmp_path / "gone-soon"
    folder.mkdir()
    _run(system_mod.save_place(str(folder), "Gone"))
    folder.rmdir()

    async def rows():
        return await system_mod.places()

    assert not [r for r in _run(rows()) if r.get("label") == "Gone"]


def test_remove_place_works_once(client, tmp_path):
    folder = tmp_path / "temp"
    folder.mkdir()
    saved = _run(system_mod.save_place(str(folder), "Temp"))["place"]
    assert _run(system_mod.remove_place(saved["id"]))["ok"]
    assert _run(system_mod.remove_place(saved["id"])) == {
        "ok": False,
        "error": "no such place",
    }


def test_remove_place_refuses_env_and_default_ids(client, monkeypatch, tmp_path):
    monkeypatch.setenv("SWARM_SYSTEM_PLACES", str(tmp_path))
    rows = _run(system_mod.places())
    kinds = {r["kind"] for r in rows}
    assert "env" in kinds or "default" in kinds
    for row in rows:
        if row["kind"] != "saved":
            assert not _run(system_mod.remove_place(row["id"]))["ok"]


def test_malformed_saved_places_json_is_skipped(client, tmp_path):
    async def poison():
        await db.set_workspace_meta(system_mod._META_PLACES_KEY, "{not json")

    _run(poison())

    async def rows():
        return await system_mod._saved_places()

    assert _run(rows()) == []


def test_saved_list_ignores_non_dict_rows(client, tmp_path):
    folder = tmp_path / "x"
    folder.mkdir()
    payload = json.dumps(["junk", {"nope": True}, {"path": str(folder)}])

    async def poison():
        await db.set_workspace_meta(system_mod._META_PLACES_KEY, payload)

    _run(poison())

    async def rows():
        return await system_mod._saved_places()

    kept = _run(rows())
    assert len(kept) == 1 and kept[0]["path"] == str(folder)


# -------------------------------------------------------------- validation --
def test_add_place_refuses_files_and_missing_paths(client, tmp_path):
    file_path = tmp_path / "a.txt"
    file_path.write_text("x", encoding="utf-8")
    assert system_mod.add_place(str(file_path)).get("ok") is False
    assert system_mod.add_place(str(tmp_path / "missing")).get("ok") is False


def test_add_place_refuses_filesystem_root_without_unrestricted(client, monkeypatch):
    """Binding `/` (or `C:\\`) is refused unless the operator opted in."""
    monkeypatch.delenv("SWARM_SYSTEM_UNRESTRICTED", raising=False)
    root = system_mod.Path("/") if system_mod.os.name != "nt" else system_mod.Path("C:\\")
    result = system_mod.add_place(str(root))
    if system_mod._is_fs_root(root):
        assert result == {
            "ok": False,
            "error": "filesystem root needs SWARM_SYSTEM_UNRESTRICTED=1",
        }


def test_add_place_labels_from_basename_by_default(client, tmp_path):
    folder = tmp_path / "reports"
    folder.mkdir()
    result = system_mod.add_place(str(folder))
    assert result["label"] == "reports"
    assert result["id"] == system_mod._place_id(folder.resolve())


# ------------------------------------------------------------------- merge --
def test_saved_row_wins_over_an_env_spelling_of_the_same_folder(
    client, monkeypatch, tmp_path
):
    folder = tmp_path / "shared"
    folder.mkdir()
    _run(system_mod.save_place(str(folder), "My copy"))
    monkeypatch.setenv("SWARM_SYSTEM_PLACES", str(folder))

    async def rows():
        return await system_mod.places()

    matches = [r for r in _run(rows()) if r["path"] == str(folder.resolve())]
    assert len(matches) == 1
    assert matches[0]["kind"] == "saved"
    assert matches[0]["label"] == "My copy"


def test_resolve_place_finds_every_kind(client, monkeypatch, tmp_path):
    folder = tmp_path / "anywhere"
    folder.mkdir()
    monkeypatch.setenv("SWARM_SYSTEM_PLACES", str(folder))

    async def check():
        rows = await system_mod.places()
        for row in rows:
            resolved = await system_mod.resolve_place(row["id"])
            assert resolved is not None
        return await system_mod.resolve_place("place-does-not-exist")

    assert _run(check()) is None
