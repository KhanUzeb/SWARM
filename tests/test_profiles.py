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


def test_job_profile_fallback_and_injection():
    text = load_agent_profile("piper", "Product Performance")
    assert text
    assert "highest-impact" in text
    assert load_job_profile("sales-outbound")
    assert load_agent_profile("no-such-bot", None) is None

    messages = _build_messages(
        "You are swarm.",
        [{"author_kind": "human", "author": "uzeb", "body": "hi"}],
        profile=load_agent_profile("swarm"),
        display_name="Swarm",
        job="Generalist",
    )
    blob = messages[0]["content"]
    assert "Bot profile (profile.md)" in blob
    assert "You are **swarm**" in blob
    assert list_profiles()
