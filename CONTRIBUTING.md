# Contributing to Swarm

Thanks for helping. Swarm is a self-hosted workspace where AI agents work as
named teammates in team chat.

## Quick start

```powershell
uv venv .venv
.\.venv\Scripts\Activate.ps1
uv pip install -r requirements.txt
uv pip install -r requirements-dev.txt
cd frontend && bun install && bun run build && cd ..
Copy-Item .env.example .env
python -m uvicorn backend.main:app --reload
```

No API key? Use demo mode: `$env:SWARM_DEMO="1"`.

## How to contribute

1. Open an issue first for anything larger than a typo so design gets
   reviewed before code.
2. Fork, branch from `main`, keep the change focused (one concern per PR).
3. Add or update tests under `tests/` for backend changes.
4. Run the checks below and paste the results in the PR description.

## Checks

```powershell
.\.venv\Scripts\python.exe -m pytest -q
cd frontend && bun run build
```

Frontend hot reload: `bun run dev` in `frontend/` while the backend runs.

## Rules for coding agents and humans

Read [AGENTS.md](AGENTS.md) before touching code. Highlights:

- This is a public repo: never commit secrets, `.env` files, tokens, or
  personal data. Review `git status` and the staged diff before committing.
- Backend owns orchestration, auth, validation, retries, and recovery.
  Frontends express intent and render state.
- Keep provider integrations behind the provider-neutral interfaces in
  `backend/ai_support/` and `backend/computer_providers.py`. No new
  provider-specific env vars when the generic connection can express it.
- Keep tests deterministic and offline by default (Groq is mocked).
- `SPEC.md` is the contract: if code and `SPEC.md` disagree, file it as a
  bug against whichever is easier to fix correctly.

## PR checklist

- [ ] Why / what changed / how it was tested (see PR template)
- [ ] `pytest -q` green
- [ ] `bun run build` green (if frontend touched)
- [ ] No secrets, local paths, or machine-specific data in the diff
- [ ] Docs updated (`README.md`, `docs/`, or `SPEC.md` as appropriate)
