"""Launch a debuggee under debugpy in --listen --wait-for-client mode."""
from __future__ import annotations

import os
import socket
import subprocess
import sys
from pathlib import Path


def find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def launch_debuggee(script: str, args: list[str], cwd: Path | None = None, log_path: Path | None = None) -> tuple[int, int]:
    """Spawn `python -m debugpy --listen 127.0.0.1:PORT --wait-for-client script args...`.

    Returns (pid, port). The process keeps running in the background.
    """
    port = find_free_port()
    log = open(log_path, "ab") if log_path else subprocess.DEVNULL
    # Prefer the venv python (sys.prefix) over sys.executable which may
    # resolve through symlinks to the base interpreter, losing site-packages.
    venv_python = Path(sys.prefix) / "bin" / "python3"
    python = str(venv_python) if venv_python.exists() else sys.executable
    cmd = [
        python,
        "-Xfrozen_modules=off",
        "-m",
        "debugpy",
        "--listen",
        f"127.0.0.1:{port}",
        "--wait-for-client",
        script,
        *args,
    ]
    # Propagate VIRTUAL_ENV so the subprocess activates the same venv.
    env = os.environ.copy()
    if "VIRTUAL_ENV" not in env and sys.prefix != sys.base_prefix:
        env["VIRTUAL_ENV"] = sys.prefix
    # New session so the daemon/CLI parent doesn't forward signals unexpectedly.
    proc = subprocess.Popen(
        cmd,
        cwd=str(cwd) if cwd else None,
        stdout=log,
        stderr=log,
        stdin=subprocess.DEVNULL,
        start_new_session=True,
        close_fds=True,
        env=env,
    )
    if log is not subprocess.DEVNULL:
        # Popen inherits the fd; close our copy.
        log.close()
    return proc.pid, port


def wait_for_port(port: int, host: str = "127.0.0.1", timeout: float = 10.0) -> bool:
    import time
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with socket.create_connection((host, port), timeout=0.5):
                return True
        except OSError:
            time.sleep(0.1)
    return False
