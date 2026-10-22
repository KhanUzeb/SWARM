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


def _host_execution_enabled() -> bool:
    """`computer_run` is host execution, so it follows the host-tool opt-in.

    The sandbox working directory is not OS isolation. Until a real container
    provider exists, refusing here is the only honest default; `SWARM_SYSTEM=1`
    is the documented opt-in and the REST route enforces the same flag.
    """
    from . import system

    if not system.enabled():
        return False
    from .. import computer_providers

    return computer_providers.provider_configured()


def computer_run(command: str) -> str:
    cmd = (command or "").strip()
    if not cmd:
        return "(need a command)"
    if not _host_execution_enabled():
        return (
            "(host command execution is disabled. Set SWARM_SYSTEM=1 to opt in; "
            "the sandbox is a working directory, not a container.)"
        )
    match = guard.is_dangerous(cmd)
    if match:
        return guard.refusal_reason(cmd, match)
    if _DANGEROUS.search(cmd):
        return "(blocked — that command is too destructive for the shared computer)"
    cwd = sandbox_dir()
    popen_kwargs: dict[str, Any] = {}
    if os.name != "nt":
        # A new process group lets the timeout kill the whole tree, not just
        # the shell that spawned it. Windows uses taskkill /T below.
        popen_kwargs["start_new_session"] = True
    try:
        proc = subprocess.Popen(
            cmd,
            shell=True,
            cwd=str(cwd),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            env=_shell_env(),
            **popen_kwargs,
        )
        try:
            output, _ = proc.communicate(timeout=SHELL_TIMEOUT)
        except subprocess.TimeoutExpired:
            if os.name == "nt":
                subprocess.run(
                    ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                    capture_output=True, text=True,
                )
            else:  # pragma: no cover - POSIX-only branch
                import signal

                os.killpg(proc.pid, signal.SIGKILL)
            try:
                # A killed tree still has pipes to drain, but never wait
                # forever for a process that ignored the kill.
                proc.communicate(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
            return f"(timed out after {SHELL_TIMEOUT}s)"
        code = proc.returncode
        body = (output or "")[:OUTPUT_CAP] or "(no output)"
        if code:
            return f"(exit {code})\n{body}"
        return body
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
    from . import system

    if system.is_protected_path(path):
        return "(blocked — that file holds credentials or the database)"
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
