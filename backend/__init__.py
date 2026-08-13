# swarm backend package
from __future__ import annotations

import os
from pathlib import Path


def _dotenv_disabled() -> bool:
    return os.environ.get("PYTHON_DOTENV_DISABLED", "").strip().lower() in {
        "1", "true", "t", "yes", "y",
    }


def _load_env() -> None:
    """Load GROQ_API_KEY etc. from `.env` without requiring a shell export.

    README / docker-compose use the project-root `.env`. The agent error
    text also mentions `backend/.env`. Empty values (including a copied
    `.env.example`) are skipped so they cannot mask a real key in the
    other file. Non-empty process env still wins.
    """
    if _dotenv_disabled():
        return
    try:
        from dotenv import dotenv_values
    except ImportError:
        return
    root = Path(__file__).resolve().parent.parent
    merged: dict[str, str] = {}
    for path in (Path(__file__).resolve().parent / ".env", root / ".env"):
        if not path.is_file():
            continue
        for key, value in dotenv_values(path, encoding="utf-8-sig").items():
            if not key or value is None:
                continue
            cleaned = str(value).strip().strip('"').strip("'")
            if cleaned:
                merged[key] = cleaned
    for key, value in merged.items():
        if not (os.environ.get(key) or "").strip():
            os.environ[key] = value


_load_env()
