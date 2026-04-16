from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest


@pytest.fixture
def tmp_cwd(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, request) -> Path:
    monkeypatch.chdir(tmp_path)
    yield tmp_path
    log = tmp_path / ".agent-py" / "daemon.log"
    log_text = log.read_text() if log.exists() else ""
    session = tmp_path / ".agent-py" / "session.json"
    session_text = session.read_text() if session.exists() else ""
    try:
        subprocess.run(
            [sys.executable, "-m", "agent_py.cli", "kill"],
            cwd=tmp_path,
            timeout=10,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except Exception:
        pass
    if request.session.testsfailed and (log_text or session_text):
        print(f"\n---- session.json ----\n{session_text}\n---- daemon.log ----\n{log_text[-4000:]}")


@pytest.fixture
def sample_script(tmp_cwd: Path) -> Path:
    src = Path(__file__).parent / "fixtures" / "sample.py"
    dst = tmp_cwd / "sample.py"
    shutil.copy(src, dst)
    return dst


def run_cli(*args: str, cwd: Path | None = None, timeout: float = 60.0) -> dict:
    import json
    proc = subprocess.run(
        [sys.executable, "-m", "agent_py.cli", *args],
        cwd=str(cwd) if cwd else None,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    if proc.returncode != 0 and not proc.stdout.strip():
        raise AssertionError(f"cli {args} failed: rc={proc.returncode} stderr={proc.stderr}")
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError:
        raise AssertionError(f"cli {args} produced non-json: {proc.stdout!r} (stderr={proc.stderr!r})")


@pytest.fixture
def cli(tmp_cwd):
    def _run(*args: str, timeout: float = 60.0) -> dict:
        return run_cli(*args, cwd=tmp_cwd, timeout=timeout)
    return _run
