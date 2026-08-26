"""Load Bot profile.md files from the repo `profiles/` directory.

Seeded Bots use `profiles/<name>.md`. Job templates use `profiles/jobs/<id>.md`.
Profiles are injected at reply time so you can edit the markdown without a DB migrate.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
PROFILES_DIR = ROOT / "profiles"
JOBS_DIR = PROFILES_DIR / "jobs"
PROFILE_CAP = 8000


def _repo_relative(path: Path) -> str:
    """Return a stable, platform-independent path for API responses."""
    return path.relative_to(ROOT).as_posix()


def _read(path: Path) -> str | None:
    if not path.is_file():
        return None
    try:
        text = path.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    if not text:
        return None
    if len(text) > PROFILE_CAP:
        return text[:PROFILE_CAP] + "\n…[truncated]"
    return text


def load_named(name: str) -> str | None:
    slug = (name or "").strip().lower()
    if not slug or "/" in slug or "\\" in slug or ".." in slug:
        return None
    return _read(PROFILES_DIR / f"{slug}.md")


def load_job_profile(job_id: str) -> str | None:
    slug = (job_id or "").strip().lower()
    if not slug or "/" in slug or "\\" in slug or ".." in slug:
        return None
    return _read(JOBS_DIR / f"{slug}.md")


def job_id_for_title(job: str | None) -> str | None:
    title = (job or "").strip().lower()
    if not title:
        return None
    from .jobs import JOB_TEMPLATES
    for row in JOB_TEMPLATES:
        if str(row.get("job") or "").strip().lower() == title:
            return str(row.get("id") or "")
        if str(row.get("id") or "").strip().lower() == title:
            return str(row.get("id") or "")
    return None


def load_agent_profile(name: str, job: str | None = None) -> str | None:
    named = load_named(name)
    if named:
        return named
    jid = job_id_for_title(job)
    if jid:
        return load_job_profile(jid)
    return None


def profile_path(name: str, job: str | None = None) -> str | None:
    slug = (name or "").strip().lower()
    if slug:
        path = PROFILES_DIR / f"{slug}.md"
        if path.is_file():
            return _repo_relative(path)
    jid = job_id_for_title(job)
    if jid:
        path = JOBS_DIR / f"{jid}.md"
        if path.is_file():
            return _repo_relative(path)
    return None


def list_profiles() -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    if PROFILES_DIR.is_dir():
        for path in sorted(PROFILES_DIR.glob("*.md")):
            out.append({
                "id": path.stem,
                "kind": "bot",
            "path": _repo_relative(path),
                "body": _read(path) or "",
            })
    if JOBS_DIR.is_dir():
        for path in sorted(JOBS_DIR.glob("*.md")):
            out.append({
                "id": path.stem,
                "kind": "job",
                "path": _repo_relative(path),
                "body": _read(path) or "",
            })
    return out
