"""Host-system tools: Bots work on this machine, not only the sandbox.

Bound to the current system folder (default: the swarm repo). Not a cloud VM.
The UI can rebind that folder so Bots can work in Home, Desktop, or any other
directory. Set SWARM_SYSTEM=1 to enable. Destructive commands and path
escapes are blocked. Filesystem root is refused unless SWARM_SYSTEM_UNRESTRICTED=1.

Default is OFF. Host-system tools run commands on the machine that runs the
backend, so an opt-in is the only sane default for something self-hosted and
frequently exposed to a LAN or the internet. Callers that want them set
SWARM_SYSTEM=1 explicitly.
"""
from __future__ import annotations

import logging
import os
import re
import subprocess
from pathlib import Path
from typing import Any

from .computer import OUTPUT_CAP, _DANGEROUS

_logger = logging.getLogger("swarm.system")

SHELL_TIMEOUT = 60
READ_CAP = 200_000
WRITE_CAP = 200_000
LIST_CAP = 200
API_PREVIEW_BYTES = 64_000
DISABLED = (
    "(system tools are disabled. Set SWARM_SYSTEM=1 to let Bots work on this machine.)"
)
# Every dotenv spelling carries provider keys, so the whole family is
# protected, not just the three names that happened to be listed. A missing
# `.env.staging` or `.env.development` line here is a leaked GROQ_API_KEY
# in the channel audit (AGENTS.md s4: secrets never leave the machine).
_PROTECTED_DOTENV = re.compile(r"^\.env(?:\.|$)", re.I)
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
_warned = False


def enabled() -> bool:
    # Through the one settings object, so `on` means on for this flag too
    # (AGENTS.md s2). The old list accepted only 1/true/yes/on.
    from ..settings import get_settings

    return get_settings().system_enabled()


def warn_if_exposed(logger: Any = None) -> str | None:
    """Loud warning when host tools are on and the server is not loopback-only.

    Host-system tools execute commands on the host. Serving them over anything
    wider than loopback means anyone who can reach the port can run code on
    that machine. Returns the warning text (for tests), or None when the
    combination is safe or host tools are off.
    """
    if not enabled():
        return None
    from ..settings import get_settings

    # The bind addresses are the settings object's, not a second read of
    # SWARM_HOST / SWARM_BIND here (AGENTS.md s2).
    candidates = set(get_settings().bind_candidates())
    if not candidates or candidates <= {"127.0.0.1", "localhost", "::1"}:
        return None
    root = str(system_root())
    message = (
        "SWARM SECURITY: host-system tools are ENABLED (SWARM_SYSTEM=1) while the "
        f"server is bound to a non-loopback address ({', '.join(sorted(candidates))}). "
        "Anyone who can reach this port can run commands on this machine, bounded "
        f"only by SWARM_SYSTEM_ROOT={root}. Set SWARM_SYSTEM=0, or bind to "
        "127.0.0.1 and put authentication plus TLS in front, unless you accept "
        "that risk."
    )
    (logger or _logger).warning(message)
    return message


def unrestricted() -> bool:
    from ..settings import get_settings

    return get_settings().system_unrestricted_ok()


def reset_runtime() -> None:
    """Clear in-memory root so tests and restarts pick env/meta again."""
    global _active_root, _warned
    _active_root = None
    _warned = False


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
    from ..settings import get_settings

    # Read through the one settings object (AGENTS.md s2): this is the folder
    # every host tool is bound to, so a second parse surface here would let
    # the code and `swarm doctor` disagree about where that is.
    override = (get_settings().system_root or "").strip()
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
    return _sanitized_host_env()


# Env vars whose value must never reach a tool result. Tool output is
# persisted to the channel audit (AGENTS.md s6), so a host command that
# prints its environment (`set`, `env`, `printenv`) would otherwise paste
# provider keys and the seal secret into chat history.
_SECRET_NAME_HINTS = ("secret", "key", "token", "password", "credentials")


def _sanitized_host_env() -> dict[str, str]:
    env = os.environ.copy()
    for name in list(env.keys()):
        lowered = name.lower()
        if name == "SWARM_SECRET" or any(hint in lowered for hint in _SECRET_NAME_HINTS):
            env.pop(name, None)
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
    # The schema says string. A number, a list or null is a schema violation,
    # and treating it as a path raises an AttributeError that propagates out
    # of the tool and ends the turn in "[agent error: ...]" (AGENTS.md s6).
    # Refuse instead, so the caller gets the normal invalid-path message.
    if not isinstance(rel, str):
        return None
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
    from ..settings import get_settings

    name = target.name.lower()
    if name in _PROTECTED_NAMES or _PROTECTED_DOTENV.match(name):
        return True
    # The DB path is compared against the one settings object's value, not a
    # second read of SWARM_DB_PATH (AGENTS.md s2).
    db_path = (get_settings().db_path or "").strip()
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
    global _warned
    if not _warned:
        # Startup passes through here; warn once per process, not per call.
        _warned = True
        warn_if_exposed()
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
    if _protected_write(target):
        # The same names system_write refuses: .env files carry provider
        # keys and the DB path carries chat history — reading them would leak
        # secrets into the tool result and the channel audit (AGENTS.md s4).
        return f"(blocked — {target.name} is protected)"
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
    # The command schema says string. A number or a list is a schema violation:
    # refusing it here is a normal tool result, while letting it through raised
    # an AttributeError that ended the turn in "[agent error: ...]".
    if not isinstance(command, str):
        return "(need a command string)"
    if not isinstance(cwd, str):
        cwd = "" if cwd is None else str(cwd)
    cmd = (command or "").strip()
    if not cmd:
        return "(need a command)"
    from . import guard as guard_mod

    if _DANGEROUS.search(cmd):
        return "(blocked — that command is too destructive for the host system)"
    match = guard_mod.is_dangerous(cmd)
    if match:
        return guard_mod.refusal_reason(cmd, match)
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
