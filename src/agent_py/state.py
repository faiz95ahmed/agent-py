"""On-disk state for agent-py: breakpoints and session metadata."""
from __future__ import annotations

import hashlib
import json
import os
import tempfile
from pathlib import Path
from typing import Any

STATE_DIRNAME = ".agent-py"
BREAKPOINTS_FILE = "breakpoints.json"
SESSION_FILE = "session.json"
LOG_FILE = "daemon.log"


def state_dir(cwd: Path | None = None) -> Path:
    base = Path(cwd) if cwd else Path.cwd()
    d = base / STATE_DIRNAME
    d.mkdir(parents=True, exist_ok=True)
    return d


def breakpoints_path(cwd: Path | None = None) -> Path:
    return state_dir(cwd) / BREAKPOINTS_FILE


def session_path(cwd: Path | None = None) -> Path:
    return state_dir(cwd) / SESSION_FILE


def socket_path(cwd: Path | None = None) -> Path:
    # AF_UNIX paths are capped at ~104 bytes on macOS, so route the socket
    # through the system tempdir keyed by a hash of the cwd (which can be long).
    base = Path(cwd).resolve() if cwd else Path.cwd().resolve()
    h = hashlib.sha1(str(base).encode("utf-8")).hexdigest()[:12]
    return Path(tempfile.gettempdir()) / f"agent-py-{h}.sock"


def log_path(cwd: Path | None = None) -> Path:
    return state_dir(cwd) / LOG_FILE


def load_breakpoints(cwd: Path | None = None) -> list[dict[str, Any]]:
    p = breakpoints_path(cwd)
    if not p.exists():
        return []
    return json.loads(p.read_text())


def save_breakpoints(bps: list[dict[str, Any]], cwd: Path | None = None) -> None:
    breakpoints_path(cwd).write_text(json.dumps(bps, indent=2))


def add_breakpoint(file: str, line: int, condition: str | None = None, cwd: Path | None = None) -> list[dict[str, Any]]:
    bps = load_breakpoints(cwd)
    abs_file = str(Path(file).resolve())
    bps = [b for b in bps if not (b["file"] == abs_file and b["line"] == line)]
    entry: dict[str, Any] = {"file": abs_file, "line": line}
    if condition:
        entry["condition"] = condition
    bps.append(entry)
    save_breakpoints(bps, cwd)
    return bps


def remove_breakpoint(file: str, line: int, cwd: Path | None = None) -> list[dict[str, Any]]:
    bps = load_breakpoints(cwd)
    abs_file = str(Path(file).resolve())
    bps = [b for b in bps if not (b["file"] == abs_file and b["line"] == line)]
    save_breakpoints(bps, cwd)
    return bps


def breakpoints_by_file(bps: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    out: dict[str, list[dict[str, Any]]] = {}
    for b in bps:
        out.setdefault(b["file"], []).append(b)
    return out


def load_session(cwd: Path | None = None) -> dict[str, Any] | None:
    p = session_path(cwd)
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text())
    except json.JSONDecodeError:
        return None


def save_session(data: dict[str, Any], cwd: Path | None = None) -> None:
    session_path(cwd).write_text(json.dumps(data, indent=2))


def clear_session(cwd: Path | None = None) -> None:
    for p in (session_path(cwd), log_path(cwd), socket_path(cwd)):
        if p.exists():
            try:
                p.unlink()
            except OSError:
                pass


def pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True
