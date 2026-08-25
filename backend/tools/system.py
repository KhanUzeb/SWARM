"""Host-system tools: Bots work on this machine, not only the sandbox.

Bound to the current system folder (default: the swarm repo). Not a cloud VM.
The UI can rebind that folder so Bots can work in Home, Desktop, or any other
directory. Set SWARM_SYSTEM=0 to disable. Destructive commands and path
escapes are blocked. Filesystem root is refused unless SWARM_SYSTEM_UNRESTRICTED=1.
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
_META_KEY = "system_root"
_WIN_SHELL_JUNCTIONS = {
    "application data", "cookies", "local settings", "my documents",
    "my music", "my pictures", "my videos", "nethood", "printhood",
    "recent", "sendto", "start menu", "templates",
}
_FILE_ATTRIBUTE_HIDDEN = 0x2
_FILE_ATTRIBUTE_SYSTEM = 0x4
_FILE_ATTRIBUTE_REPARSE = 0x400

_active_root: Path | None = None


def enabled() -> bool:
    raw = (os.environ.get("SWARM_SYSTEM") or "1").strip().lower()
    return raw not in ("0", "false", "no", "off")


def unrestricted() -> bool:
    raw = (os.environ.get("SWARM_SYSTEM_UNRESTRICTED") or "0").strip().lower()
    return raw in ("1", "true", "yes", "on")


def reset_runtime() -> None:
    """Clear in-memory root so tests and restarts pick env/meta again."""
    global _active_root
    _active_root = None


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


def _validate_root(candidate: Path) -> Path | None:
    try:
        resolved = candidate.expanduser().resolve()
    except OSError:
        return None
    if not resolved.is_dir():
        return None
    if _is_fs_root(resolved) and not unrestricted():
        return None
    return resolved


def _env_or_repo_root() -> Path:
    override = (os.environ.get("SWARM_SYSTEM_ROOT") or "").strip()
    candidate = Path(override).expanduser() if override else _repo_root()
    try:
        resolved = candidate.resolve()
    except OSError:
        resolved = _repo_root()
    if _is_fs_root(resolved) and not unrestricted():
        return _repo_root()
    return resolved


def system_root() -> Path:
    if _active_root is not None:
        return _active_root
    return _env_or_repo_root()


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


def places() -> list[dict[str, str]]:
    home = Path.home()
    candidates = [
        ("project", "Project", _repo_root()),
        ("home", "Home", home),
        ("desktop", "Desktop", home / "Desktop"),
        ("onedrive-desktop", "OneDrive Desktop", home / "OneDrive" / "Desktop"),
        ("documents", "Documents", home / "Documents"),
        ("downloads", "Downloads", home / "Downloads"),
    ]
    seen: set[str] = set()
    out: list[dict[str, str]] = []
    for pid, label, path in candidates:
        resolved = _validate_root(path)
        if resolved is None:
            continue
        key = str(resolved).casefold()
        if key in seen:
            continue
        seen.add(key)
        out.append({"id": pid, "label": label, "path": str(resolved)})
    return out


def _crumbs(target: Path) -> list[dict[str, str]]:
    root = system_root()
    label = root.name or str(root)
    items = [{"label": label, "path": "", "absolute": str(root)}]
    try:
        rel = target.resolve().relative_to(root)
    except ValueError:
        return items
    acc = ""
    for part in rel.parts:
        acc = f"{acc}/{part}" if acc else part
        items.append({
            "label": part,
            "path": acc,
            "absolute": str((root / acc).resolve()),
        })
    return items


def _can_go_up(root: Path) -> bool:
    parent = root.parent
    if parent == root:
        return bool(unrestricted())
    if _is_fs_root(parent) and not unrestricted():
        return False
    return True


def _win_attrs(entry: os.DirEntry) -> int:
    try:
        st = entry.stat(follow_symlinks=False)
        return int(getattr(st, "st_file_attributes", 0) or 0)
    except OSError:
        return 0


def _is_reparse(entry: os.DirEntry) -> bool:
    if entry.is_symlink():
        return True
    return bool(_win_attrs(entry) & _FILE_ATTRIBUTE_REPARSE)


def _skip_dirent(listed: Path, entry: os.DirEntry) -> bool:
    name = entry.name
    if name in (".", ".."):
        return True
    attrs = _win_attrs(entry)
    reparse = _is_reparse(entry)
    hidden_system = bool(attrs & _FILE_ATTRIBUTE_HIDDEN) and bool(attrs & _FILE_ATTRIBUTE_SYSTEM)
    if reparse and hidden_system:
        return True
    if name.lower() in _WIN_SHELL_JUNCTIONS:
        return True
    try:
        dest = Path(entry.path).resolve()
        dest.relative_to(listed.resolve())
    except (OSError, ValueError):
        return True
    return False


def _child_rel(listed: Path, name: str) -> str:
    base = rel_to_root(listed)
    if not base:
        return name
    return f"{base}/{name}"


def _dir_entries(listed: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    found = list(os.scandir(listed))
    found.sort(key=lambda e: (not e.is_dir(follow_symlinks=False), e.name.lower()))
    for entry in found:
        if len(rows) >= LIST_CAP:
            break
        if _skip_dirent(listed, entry):
            continue
        try:
            is_dir = entry.is_dir(follow_symlinks=False)
            size = 0 if is_dir else int(entry.stat(follow_symlinks=False).st_size)
        except OSError:
            continue
        rows.append({
            "name": entry.name,
            "path": _child_rel(listed, entry.name),
            "kind": "dir" if is_dir else "file",
            "size": size,
        })
    return rows


async def hydrate_root() -> Path:
    """Apply a persisted UI root if one is stored; otherwise env/repo."""
    global _active_root
    if _active_root is not None:
        return _active_root
    from .. import db
    stored = await db.get_workspace_meta(_META_KEY)
    if stored:
        resolved = _validate_root(Path(stored))
        if resolved is not None:
            _active_root = resolved
            return resolved
    return system_root()


async def set_root(path: str) -> dict[str, Any]:
    global _active_root
    raw = (path or "").strip()
    if not raw or "\x00" in raw:
        return {"ok": False, "error": "invalid path"}
    resolved = _validate_root(Path(raw))
    if resolved is None:
        if _is_fs_root(Path(raw).expanduser()) and not unrestricted():
            return {"ok": False, "error": "filesystem root needs SWARM_SYSTEM_UNRESTRICTED=1"}
        return {"ok": False, "error": "not a directory"}
    _active_root = resolved
    from .. import db
    await db.set_workspace_meta(_META_KEY, str(resolved))
    return {"ok": True, "root": str(resolved)}


def status() -> dict[str, Any]:
    root = system_root()
    parent = root.parent
    return {
        "enabled": enabled(),
        "root": str(root),
        "unrestricted": unrestricted(),
        "writable": os.access(root, os.W_OK) if root.exists() else False,
        "places": places(),
        "can_go_up": _can_go_up(root),
        "parent_abs": str(parent) if _can_go_up(root) else None,
        "project": str(_repo_root()),
        "home": str(Path.home()),
    }


def listing(path: str = "") -> dict[str, Any]:
    info = status()
    info["path"] = ""
    info["parent"] = None
    info["absolute"] = info["root"]
    info["crumbs"] = _crumbs(system_root())
    info["entries"] = []
    info["note"] = (
        "Bots use system_run / system_ls / system_read / system_write in this folder. "
        "Pick Home, Desktop, or type a path to work somewhere else. "
        "computer_run stays in the sandbox. Set SWARM_SYSTEM=0 to disable."
    )
    if not enabled():
        info["note"] = DISABLED
        return info
    target = safe_path(path)
    if target is None:
        info["error"] = "invalid path"
        return info
    info["absolute"] = str(target)
    info["crumbs"] = _crumbs(target)
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
    try:
        info["entries"] = _dir_entries(target)
    except OSError as exc:
        info["error"] = str(exc)
        return info
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
    except OSError as extra:
        return f"(read error: {extra})"
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
    except OSError as extra:
        return f"(write error: {extra})"
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
    except Exception as extra:  # noqa: BLE001
        return f"(system error: {extra})"
