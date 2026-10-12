"""Computer-use tools: run commands, open files/URLs, capture a workspace snapshot."""
from __future__ import annotations

import os
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import guard

SHELL_TIMEOUT = 30
OUTPUT_CAP = 8000
_DANGEROUS = re.compile(
    r"(rm\s+-rf\s+/(\s|$)|mkfs\b|dd\s+if=|: \(\) \{|fork\s*bomb|/dev/sd[a-z])",
    re.I,
)


def sandbox_dir() -> Path:
    from .. import agent
    Path(agent.SANDBOX_DIR).mkdir(parents=True, exist_ok=True)
    return Path(agent.SANDBOX_DIR).resolve()


def _shell_env() -> dict[str, str]:
    from .. import agent
    return agent._shell_env()


def computer_run(command: str) -> str:
    cmd = (command or "").strip()
    if not cmd:
        return "(need a command)"
    match = guard.is_dangerous(cmd)
    if match:
        return guard.refusal_reason(cmd, match)
    if _DANGEROUS.search(cmd):
        return "(blocked — that command is too destructive for the shared computer)"
    cwd = sandbox_dir()
    try:
        result = subprocess.run(
            cmd,
            shell=True,
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=SHELL_TIMEOUT,
            env=_shell_env(),
        )
        output = (result.stdout or "") + (result.stderr or "")
        code = result.returncode
        body = output[:OUTPUT_CAP] or "(no output)"
        if code:
            return f"(exit {code})\n{body}"
        return body
    except subprocess.TimeoutExpired:
        return f"(timed out after {SHELL_TIMEOUT}s)"
    except Exception as exc:  # noqa: BLE001
        return f"(computer error: {exc})"


def computer_open(target: str) -> str:
    raw = (target or "").strip()
    if not raw:
        return "(need a path or URL)"
    if raw.startswith(("http://", "https://")):
        from . import browser
        if not browser.enabled():
            return (
                f"(browser use is disabled. Set SWARM_BROWSER=1 and install "
                f"playwright to open {raw}.)"
            )
        return (
            f"To open {raw} in the workspace browser, call browser_navigate "
            f"with url={raw}. computer_open cannot drive Chromium from this thread."
        )
    from .. import agent
    path = agent._safe_workspace_path(raw)
    if path is None:
        return "(invalid path — stay under the workspace root)"
    if not path.exists():
        return f"(not found: {raw})"
    if path.is_dir():
        names = sorted(p.name for p in path.iterdir())[:40]
        listing = "\n".join(names) if names else "(empty directory)"
        return f"opened directory {raw}\n{listing}"
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        return f"(read error: {exc})"
    if len(text) > 32_000:
        text = text[:32_000] + "\n…[truncated]"
    return f"opened {raw} ({path.stat().st_size} bytes)\n{text}"


def computer_screenshot() -> str:
    """Prefer a live browser screenshot; otherwise dump a text snapshot of the sandbox."""
    from . import browser
    shot = browser.screenshot_if_open()
    if shot:
        return shot
    cwd = sandbox_dir()
    # Bounded walk like agent.list_workspace_files: the sandbox can point at
    # any folder now, so sorted(rglob("*")) would stall on a drive root.
    files = []
    visited = 0
    for dirpath, dirnames, filenames in os.walk(cwd):
        dirnames[:] = [d for d in dirnames if d != ".git" and not d.startswith(".")]
        dirnames.sort()
        for name in sorted(filenames):
            visited += 1
            if visited > 2000:
                break
            if name.startswith("."):
                continue
            p = Path(dirpath) / name
            try:
                rel = p.relative_to(cwd).as_posix()
            except ValueError:
                continue
            if rel.startswith("."):
                continue
            try:
                size = p.stat().st_size
            except OSError:
                continue
            if not p.is_file():
                continue
            files.append(f"{rel}  {size}B")
            if len(files) >= 40:
                break
        if len(files) >= 40 or visited > 2000:
            break
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    body = (
        f"swarm computer snapshot @ {stamp}\n"
        f"cwd: {cwd}\n"
        f"files ({len(files)}):\n"
        + ("\n".join(files) if files else "(empty)")
    )
    out = cwd / "screenshots"
    out.mkdir(parents=True, exist_ok=True)
    name = f"computer-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}.txt"
    (out / name).write_text(body, encoding="utf-8")
    return f"saved screenshots/{name}\n{body}"


def status() -> dict[str, Any]:
    from . import browser
    cwd = sandbox_dir()
    return {
        "cwd": str(cwd),
        "writable": os.access(cwd, os.W_OK),
        "browser": browser.status(),
    }
