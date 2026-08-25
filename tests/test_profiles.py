"""Bot profile.md loading."""
from __future__ import annotations

from backend.agent import _build_messages
from backend.profiles import list_profiles, load_agent_profile, load_job_profile


def test_seeded_bots_have_profile_files(client, auth):
    agents = {a["name"]: a for a in client.get("/api/agents", headers=auth).json()}
    assert "You are **swarm**" in (agents["swarm"].get("profile") or "")
    assert agents["swarm"]["profile_path"] == "profiles/swarm.md"
    assert "You are **ledger**" in (agents["ledger"].get("profile") or "")
    assert "You are **coder**" in (agents["coder"].get("profile") or "")


def test_job_profiles_listed(client, auth):
    rows = client.get("/api/profiles", headers=auth).json()
    ids = {(r["kind"], r["id"]) for r in rows}
    assert ("bot", "swarm") in ids
    assert ("job", "chief-of-staff") in ids
    assert ("job", "sales-outbound") in ids
    jobs = client.get("/api/jobs", headers=auth).json()
    chief = next(j for j in jobs if j["id"] == "chief-of-staff")
    assert "Chief of staff" in chief["profile"]


def test_job_profile_used_when_no_named_file():
    text = load_agent_profile("piper", "Product Performance")
    assert text
    assert "highest-impact" in text
    assert load_job_profile("sales-outbound")
    assert load_named_missing()


def load_named_missing():
    return load_agent_profile("no-such-bot", None) is None


def test_profile_injected_into_system_messages():
    profile = load_agent_profile("swarm")
    messages = _build_messages(
        "You are swarm.",
        [{"author_kind": "human", "author": "uzeb", "body": "hi"}],
        profile=profile,
        display_name="Swarm",
        job="Generalist",
    )
    blob = messages[0]["content"]
    assert "Bot profile (profile.md)" in blob
    assert "You are **swarm**" in blob
    assert list_profiles()
