"""CLI: one IPC round-trip per invocation (except `launch`, `connect`, `status`, `kill`)."""
from __future__ import annotations

import argparse
import json
import os
import signal
import sys
import time
from pathlib import Path
from typing import Any

from . import state as st
from . import launch as lnc
from . import daemon as dmn
from .ipc import send_request


def _print(obj: Any) -> None:
    json.dump(obj, sys.stdout, indent=2)
    sys.stdout.write("\n")


def _parse_file_line(arg: str) -> tuple[str, int]:
    if ":" not in arg:
        raise SystemExit(f"expected FILE:LINE, got {arg!r}")
    file, _, line_s = arg.rpartition(":")
    try:
        return file, int(line_s)
    except ValueError:
        raise SystemExit(f"invalid line number in {arg!r}")


def _daemon_alive(cwd: Path) -> bool:
    sess = st.load_session(cwd)
    if not sess:
        return False
    pid = sess.get("daemon_pid", 0)
    return bool(pid) and st.pid_alive(pid) and st.socket_path(cwd).exists()


def _ipc(cwd: Path, req: dict[str, Any], timeout: float = 300.0) -> dict[str, Any]:
    if not _daemon_alive(cwd):
        return {"error": "no daemon running (run `agent-py connect` first)"}
    return send_request(st.socket_path(cwd), req, timeout=timeout)


# --- subcommands ---
def cmd_break(ns: argparse.Namespace) -> int:
    file, line = _parse_file_line(ns.loc)
    bps = st.add_breakpoint(file, line, ns.condition, Path.cwd())
    if _daemon_alive(Path.cwd()):
        _ipc(Path.cwd(), {"cmd": "refresh-breakpoints"})
    _print({"ok": True, "breakpoints": bps})
    return 0


def cmd_unbreak(ns: argparse.Namespace) -> int:
    file, line = _parse_file_line(ns.loc)
    bps = st.remove_breakpoint(file, line, Path.cwd())
    if _daemon_alive(Path.cwd()):
        _ipc(Path.cwd(), {"cmd": "refresh-breakpoints"})
    _print({"ok": True, "breakpoints": bps})
    return 0


def cmd_breakpoints(ns: argparse.Namespace) -> int:
    _print({"ok": True, "breakpoints": st.load_breakpoints(Path.cwd())})
    return 0


def cmd_launch(ns: argparse.Namespace) -> int:
    cwd = Path.cwd()
    script = str(Path(ns.script).resolve())
    if not Path(script).exists():
        _print({"error": f"script not found: {script}"})
        return 1
    pid, port = lnc.launch_debuggee(script, ns.args, cwd=cwd, log_path=st.log_path(cwd))
    sess = {
        "status": "launched",
        "debuggee_pid": pid,
        "dap_port": port,
        "script": script,
        "args": ns.args,
    }
    st.save_session(sess, cwd)
    _print({"ok": True, "pid": pid, "port": port})
    return 0


def cmd_connect(ns: argparse.Namespace) -> int:
    cwd = Path.cwd()
    sess = st.load_session(cwd)
    if not sess or "dap_port" not in sess:
        _print({"error": "no launched debuggee (run `agent-py launch ...` first)"})
        return 1
    if not st.pid_alive(sess.get("debuggee_pid", 0)):
        _print({"error": "debuggee is not running"})
        return 1
    if _daemon_alive(cwd):
        _print({"error": "daemon already running; use `agent-py status` or `agent-py kill`"})
        return 1
    # Do not probe the port — debugpy's adapter treats a TCP probe as a one-shot
    # client session and tears down after it closes. DAPClient.connect() retries.
    dmn.spawn_daemon(cwd, sess["dap_port"], sess["debuggee_pid"], ns.break_on)
    # wait until daemon creates socket + first stop (or termination)
    deadline = time.monotonic() + 30.0
    while time.monotonic() < deadline:
        s = st.load_session(cwd) or {}
        if s.get("status") in ("paused", "terminated"):
            break
        time.sleep(0.1)
    s = st.load_session(cwd) or {}
    _print({"ok": True, "status": s.get("status"), "pause": s.get("pause")})
    return 0


def cmd_continue(ns: argparse.Namespace) -> int:
    _print(_ipc(Path.cwd(), {"cmd": "continue"}))
    return 0


def cmd_step(ns: argparse.Namespace) -> int:
    mapping = {"over": "step-over", "into": "step-into", "out": "step-out"}
    _print(_ipc(Path.cwd(), {"cmd": mapping[ns.mode]}))
    return 0


def cmd_eval(ns: argparse.Namespace) -> int:
    _print(_ipc(Path.cwd(), {"cmd": "evaluate", "expr": ns.expr}))
    return 0


def cmd_listvars(ns: argparse.Namespace) -> int:
    _print(_ipc(Path.cwd(), {"cmd": "listvars", "page": ns.page}))
    return 0


def cmd_frame(ns: argparse.Namespace) -> int:
    _print(_ipc(Path.cwd(), {"cmd": "frame", "index": ns.index}))
    return 0


def cmd_variable(ns: argparse.Namespace) -> int:
    _print(_ipc(Path.cwd(), {"cmd": "variable", "ref": ns.ref, "page": ns.page}))
    return 0


def cmd_status(ns: argparse.Namespace) -> int:
    cwd = Path.cwd()
    sess = st.load_session(cwd) or {}
    alive = _daemon_alive(cwd)
    _print({"ok": True, "daemon_alive": alive, "session": sess})
    return 0


def cmd_kill(ns: argparse.Namespace) -> int:
    cwd = Path.cwd()
    sess = st.load_session(cwd) or {}
    if _daemon_alive(cwd):
        try:
            send_request(st.socket_path(cwd), {"cmd": "kill"}, timeout=5.0)
        except OSError:
            pass
    daemon_pid = sess.get("daemon_pid", 0)
    debuggee_pid = sess.get("debuggee_pid", 0)
    for pid in (daemon_pid, debuggee_pid):
        if pid and st.pid_alive(pid):
            try:
                os.kill(pid, signal.SIGTERM)
            except OSError:
                pass
    # give processes a moment
    time.sleep(0.2)
    for pid in (daemon_pid, debuggee_pid):
        if pid and st.pid_alive(pid):
            try:
                os.kill(pid, signal.SIGKILL)
            except OSError:
                pass
    st.clear_session(cwd)
    _print({"ok": True})
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="agent-py")
    sub = p.add_subparsers(dest="cmd", required=True)

    b = sub.add_parser("break", help="add a breakpoint")
    b.add_argument("loc", help="FILE:LINE")
    b.add_argument("--condition", default=None)
    b.set_defaults(func=cmd_break)

    ub = sub.add_parser("unbreak", help="remove a breakpoint")
    ub.add_argument("loc", help="FILE:LINE")
    ub.set_defaults(func=cmd_unbreak)

    sub.add_parser("breakpoints", help="list breakpoints").set_defaults(func=cmd_breakpoints)

    la = sub.add_parser("launch", help="spawn debuggee under debugpy")
    la.add_argument("script")
    la.add_argument("args", nargs=argparse.REMAINDER)
    la.set_defaults(func=cmd_launch)

    co = sub.add_parser("connect", help="spawn daemon and attach")
    co.add_argument("--break-on", choices=["uncaught", "raised", "none"], default="uncaught")
    co.set_defaults(func=cmd_connect)

    sub.add_parser("continue", help="resume execution").set_defaults(func=cmd_continue)

    sp = sub.add_parser("step", help="step over/into/out")
    sp.add_argument("mode", choices=["over", "into", "out"])
    sp.set_defaults(func=cmd_step)

    ev = sub.add_parser("eval", help="evaluate expression in current frame")
    ev.add_argument("expr")
    ev.set_defaults(func=cmd_eval)

    ex = sub.add_parser("listvars", help="list variables in the current frame")
    ex.add_argument("--page", type=int, default=1)
    ex.set_defaults(func=cmd_listvars)

    fr = sub.add_parser("frame", help="switch active frame")
    fr.add_argument("index", type=int)
    fr.set_defaults(func=cmd_frame)

    vr = sub.add_parser("variable", help="expand a composite by ref")
    vr.add_argument("ref", type=int)
    vr.add_argument("--page", type=int, default=1)
    vr.set_defaults(func=cmd_variable)

    sub.add_parser("status", help="show daemon/debuggee state").set_defaults(func=cmd_status)
    sub.add_parser("kill", help="terminate daemon + debuggee, clear session").set_defaults(func=cmd_kill)

    ns = p.parse_args(argv)
    return int(ns.func(ns) or 0)


if __name__ == "__main__":
    sys.exit(main())
