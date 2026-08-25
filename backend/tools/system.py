"""Host-system tools: Bots work on this machine, not only the sandbox.

Bound to SWARM_SYSTEM_ROOT (default: the swarm repo). Not a cloud VM.
Set SWARM_SYSTEM=0 to disable. Destructive commands and path escapes are blocked.
SWARM_SYSTEM_ROOT=/ is refused unless SWARM_SYSTEM_UNRESTRICTED=1.
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Any

from .computer import OUTPUT_CAP, _DANGEROUS

SHELL_TIMEOUT = 60
READ_CAP = 200_000
WRITE_CAP = 200_000
LIST_CAP = 200
API_PREVIEW_BYTES = 64_000
DISABLED = (
    "(system tools are disabled. Set SWARM_SYSTEM=1 to let Bots work on this machine.)"
)
_PROTECTED_NAMES = {".env", ".env.local", ".env.production", "swarm.db"}


def enabled() -> bool:
    raw = (os.environ.get("SWARM_SYSTEM") or "1").strip().lower()
    return raw not in ("0", "false", "no", "off")


def unrestricted() -> bool:
    raw = (os.environ.get("SWARM_SYSTEM_UNRESTRICTED") or "0").strip().lower()
    return raw in ("1", "true", "yes", "on")


def _repo_root() -> Path:
    here = Path(__file__).resolve().parent.parent.parent
    if (here / "backend").is_dir():
        return here
    return Path.cwd().resolve()


def _is_fs_root(path: Path) -> bool:
    try:
        resolved = path.resolve()
    except OSError:
        return False
    return resolved.parent == resolved


def system_root() -> Path:
    override = (os.environ.get("SWARM_SYSTEM_ROOT") or "").strip()
    candidate = Path(override).expanduser() if override else _repo_root()
    try:
        resolved = candidate.resolve()
    except OSError:
        resolved = _repo_root()
    if _is_fs_root(resolved) and not unrestricted():
        return _repo_root()
    return resolved


def _host_env() -> dict[str, str]:
    env = os.environ.copy()
    env["SWARM_SYSTEM_ROOT"] = str(system_root())
    return env


def rel_to_root(path: Path) -> str:
    root = system_root()
    try:
        rel = path.resolve().relative_to(root)
    except ValueError:
        return ""
    return rel.as_posix()


def safe_path(rel: str | None) -> Path | None:
    root = system_root()
    raw = (rel or "").strip()
    if "\x00" in raw:
        return None
    if not raw or raw in (".", "./"):
        return root
    candidate = Path(raw)
    try:
        target = candidate.resolve() if candidate.is_absolute() else (root / raw).resolve()
        target.relative_to(root)
    except (OSError, ValueError):
        return None
    return target


def _protected_write(target: Path) -> bool:
    if target.name.lower() in _PROTECTED_NAMES:
        return True
    db_path = (os.environ.get("SWARM_DB_PATH") or "").strip()
    if not db_path:
        return False
    try:
        return target.resolve() == Path(db_path).expanduser().resolve()
    except OSError:
        return False


def status() -> dict[str, Any]:
    root = system_root()
    return {
        "enabled": enabled(),
        "root": str(root),
        "unrestricted": unrestricted(),
        "writable": os.access(root, os.W_OK) if root.exists() else False,
    }


def listing(path: str = "") -> dict[str, Any]:
    info = status()
    info["path"] = ""
    info["parent"] = None
    info["entries"] = []
    info["note"] = (
        "Bots use system_run / system_ls / system_read / system_write on this machine. "
        "computer_run stays in the sandbox. Set SWARM_SYSTEM=0 to disable."
    )
    if not enabled():
        info["note"] = DISABLED
        return info
    target = safe_path(path)
    if target is None:
        info["error"] = "invalid path"
        return info
    if not target.exists():
        info["error"] = "not found"
        info["path"] = rel_to_root(target) if target != system_root() else (path or "")
        return info
    if target.is_file():
        info["path"] = rel_to_root(target)
        parent = target.parent
        info["parent"] = rel_to_root(parent) if parent != system_root() else ""
        info["kind"] = "file"
        info["size"] = target.stat().st_size
        return info
    info["path"] = rel_to_root(target)
    parent = target.parent
    if target != system_root():
        try:
            parent.relative_to(system_root())
            info["parent"] = rel_to_root(parent)
        except ValueError:
            info["parent"] = ""
    entries: list[dict[str, Any]] = []
    try:
        children = sorted(target.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower()))
    except OSError as exc:
        info["error"] = str(exc)
        return info
    for child in children:
        if len(entries) >= LIST_CAP:
            break
        try:
            kind = "dir" if child.is_dir() else "file"
            size = 0 if kind == "dir" else child.stat().st_size
        except OSError:
            continue
        entries.append({
            "name": child.name,
            "path": rel_to_root(child),
            "kind": kind,
            "size": size,
        })
    info["entries"] = entries
    info["kind"] = "dir"
    return info


def system_ls(path: str = "") -> str:
    if not enabled():
        return DISABLED
    info = listing(path)
    if info.get("error") == "invalid path":
        return f"(invalid path — stay under the system root {system_root()})"
    if info.get("error") == "not found":
        return f"(not found: {path or '.'})"
    if info.get("error"):
        return f"(ls error: {info['error']})"
    if info.get("kind") == "file":
        return f"{info['path']}  file  {info.get('size', 0)}B"
    lines = [f"system root: {info['root']}", f"path: {info['path'] or '.'}"]
    dirs = [e for e in info["entries"] if e["kind"] == "dir"]
    files = [e for e in info["entries"] if e["kind"] == "file"]
    if dirs:
        lines.append("dirs:")
        lines.extend(f"  {e['name']}/" for e in dirs)
    if files:
        lines.append("files:")
        lines.extend(f"  {e['name']}  {e['size']}B" for e in files)
    if not dirs and not files:
        lines.append("(empty directory)")
    return "\n".join(lines)


def system_read(path: str) -> str:
    if not enabled():
        return DISABLED
    target = safe_path(path)
    if target is None:
        return f"(invalid path — stay under the system root {system_root()})"
    if not target.exists() or not target.is_file():
        return f"(not found: {path})"
    try:
        raw = target.read_bytes()
    except OSError as exc:
        return f"(read error: {exc})"
    if b"\x00" in raw[:1024]:
        return f"(binary file, {len(raw)} bytes — not shown)"
    text = raw.decode("utf-8", errors="replace")
    if len(text) > READ_CAP:
        text = text[:READ_CAP] + "\n…[truncated]"
    rel = rel_to_root(target) or path
    return f"{rel} ({len(raw)} bytes)\n{text}"


def system_write(path: str, content: str) -> str:
    if not enabled():
        return DISABLED
    target = safe_path(path)
    if target is None:
        return f"(invalid path — stay under the system root {system_root()})"
    if _protected_write(target):
        return f"(blocked — {target.name} is protected)"
    text = content if isinstance(content, str) else str(content)
    if len(text) > WRITE_CAP:
        return f"(file too large — cap is {WRITE_CAP} characters)"
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
    except OSError as exc:
        return f"(write error: {exc})"
    rel = rel_to_root(target) or path
    return f"wrote {rel} ({len(text)} chars)"


def system_run(command: str, cwd: str = "") -> str:
    if not enabled():
        return DISABLED
    cmd = (command or "").strip()
    if not cmd:
        return "(need a command)"
    if _DANGEROUS.search(cmd):
        return "(blocked — that command is too destructive for the host system)"
    root = system_root()
    work = root
    if (cwd or "").strip():
        work = safe_path(cwd)
        if work is None:
            return f"(invalid cwd — stay under the system root {root})"
        if work.is_file():
            work = work.parent
        if not work.exists():
            return f"(cwd not found: {cwd})"
    try:
        result = subprocess.run(
            cmd,
            shell=True,
            cwd=str(work),
            capture_output=True,
            text=True,
            timeout=SHELL_TIMEOUT,
            env=_host_env(),
        )
        output = (result.stdout or "") + (result.stderr or "")
        body = output[:OUTPUT_CAP] or "(no output)"
        if result.returncode:
            return f"(exit {result.returncode})\n{body}"
        return body
    except subprocess.TimeoutExpired:
        return f"(timed out after {SHELL_TIMEOUT}s)"
    except Exception as exc:  # noqa: BLE001
        return f"(system error: {exc})"
