"""Bundled /commands — markdown in repo `skills/`, seeded into the skills table."""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SKILLS_DIR = ROOT / "skills"

# Stable order for docs and tests. Files must exist at these names.
BUNDLED_SKILL_NAMES = (
    "standup",
    "digest",
    "decide",
    "research",
    "page",
    "repro",
    "draft",
    "review",
    "plan",
    "brief",
)


def load_bundled_skills() -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    for name in BUNDLED_SKILL_NAMES:
        path = SKILLS_DIR / f"{name}.md"
        if not path.is_file():
            continue
        body = path.read_text(encoding="utf-8").strip()
        if body:
            out.append((name, body[:8000]))
    return out
