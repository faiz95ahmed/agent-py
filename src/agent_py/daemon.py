"""Daemon: holds DAP socket to debugpy, serves CLI requests over Unix socket.

States: attaching → running → paused ↔ running → terminated.
"""
from __future__ import annotations

import os
import sys
import threading
import time
from pathlib import Path
from typing import Any

from . import state as st
from . import format as fmt
from .dap import DAPClient, DAPError
from .ipc import IPCServer


EXC_FILTERS = {
    "uncaught": ["uncaught"],
    "raised": ["raised", "uncaught"],
    "none": [],
}


class Daemon:
    def __init__(self, cwd: Path, dap_port: int, debuggee_pid: int, break_on: str):
        self.cwd = cwd
        self.dap_port = dap_port
        self.debuggee_pid = debuggee_pid
        self.break_on = break_on
        self.client = DAPClient("127.0.0.1", dap_port)
        self.status = "attaching"
        self.current_thread: int | None = None
        self.current_frame: int | None = None  # DAP frameId
        self.last_pause: dict[str, Any] = {}
        self.lock = threading.Lock()
        self.ipc: IPCServer | None = None
        self._shutdown = threading.Event()

    # --- lifecycle ---
    def run(self) -> None:
        self._write_session()
        self.client.on_event(self._on_event)
        self.client.connect(timeout=15.0)
        try:
            self._handshake()
        except DAPError as e:
            self._set_status("terminated", error=str(e))
            return

        self.ipc = IPCServer(st.socket_path(self.cwd), self._handle_request)
        self.ipc.start()

        # wait for first stop (or termination)
        try:
            ev = self.client.wait_for_event({"stopped", "terminated", "exited"}, timeout=300.0)
            self._absorb_event(ev)
        except DAPError as e:
            self._set_status("terminated", error=str(e))

        # idle — IPC thread serves requests; we wait until shutdown.
        while not self._shutdown.is_set():
            if self.status == "terminated":
                # linger briefly so `status` can read final state
                if self._shutdown.wait(timeout=30.0):
                    break
                break
            time.sleep(0.2)

        if self.ipc:
            self.ipc.stop()
        self.client.close()

    def _handshake(self) -> None:
        self.client.request("initialize", {
            "clientID": "agent-py",
            "clientName": "agent-py",
            "adapterID": "python",
            "pathFormat": "path",
            "linesStartAt1": True,
            "columnsStartAt1": True,
            "locale": "en-US",
            "supportsVariableType": True,
            "supportsRunInTerminalRequest": False,
        })
        # attach's response is deferred until after configurationDone — send
        # without awaiting, then do setBreakpoints/configurationDone, then await.
        attach_seq = self.client.send_request("attach", {"connect": {"host": "127.0.0.1", "port": self.dap_port}})
        # Wait for the `initialized` event before sending configuration requests.
        self.client.wait_for_event({"initialized"}, timeout=15.0)
        self._apply_breakpoints()
        self.client.request("setExceptionBreakpoints", {"filters": EXC_FILTERS.get(self.break_on, ["uncaught"])})
        self.client.request("configurationDone", {})
        self.client.await_response(attach_seq, timeout=15.0)
        self._set_status("running")

    def _apply_breakpoints(self) -> None:
        bps = st.load_breakpoints(self.cwd)
        by_file = st.breakpoints_by_file(bps)
        # Also clear breakpoints for files that no longer have any.
        for file, entries in by_file.items():
            self.client.request("setBreakpoints", {
                "source": {"path": file},
                "breakpoints": [
                    {"line": b["line"], **({"condition": b["condition"]} if b.get("condition") else {})}
                    for b in entries
                ],
            })

    # --- events ---
    def _on_event(self, ev: dict[str, Any]) -> None:
        name = ev.get("event")
        if name == "terminated" or name == "exited":
            with self.lock:
                self.status = "terminated"
                self._write_session()

    def _absorb_event(self, ev: dict[str, Any]) -> None:
        name = ev.get("event")
        body = ev.get("body") or {}
        if name == "stopped":
            self.current_thread = body.get("threadId")
            self._capture_pause(reason=body.get("reason", "unknown"), description=body.get("description"), text=body.get("text"))
            self._set_status("paused")
        elif name in ("terminated", "exited"):
            self._set_status("terminated")

    def _capture_pause(self, reason: str, description: str | None = None, text: str | None = None) -> None:
        if self.current_thread is None:
            return
        frames = self.client.request("stackTrace", {"threadId": self.current_thread, "startFrame": 0, "levels": 50}).get("stackFrames", [])
        if not frames:
            return
        self.current_frame = frames[0].get("id")
        top = frames[0]
        file = (top.get("source") or {}).get("path", "")
        line = top.get("line", 0)
        self.last_pause = {
            "reason": reason,
            "description": description,
            "text": text,
            "file": file,
            "line": line,
            "function": top.get("name"),
            "source": fmt.source_context(file, line) if file else [],
            "stack": [
                {
                    "index": i,
                    "id": f.get("id"),
                    "function": f.get("name"),
                    "file": (f.get("source") or {}).get("path", ""),
                    "line": f.get("line"),
                }
                for i, f in enumerate(frames)
            ],
        }

    # --- status / session.json ---
    def _set_status(self, status: str, **extra: Any) -> None:
        with self.lock:
            self.status = status
            self._write_session(**extra)

    def _write_session(self, **extra: Any) -> None:
        data: dict[str, Any] = {
            "status": self.status,
            "dap_port": self.dap_port,
            "debuggee_pid": self.debuggee_pid,
            "daemon_pid": os.getpid(),
            "break_on": self.break_on,
        }
        if self.last_pause:
            data["pause"] = self.last_pause
        data.update(extra)
        st.save_session(data, self.cwd)

    # --- IPC handler ---
    def _handle_request(self, req: dict[str, Any]) -> dict[str, Any]:
        cmd = req.get("cmd")
        try:
            if cmd == "status":
                return self._reply_status()
            if cmd == "kill":
                return self._do_kill()
            if cmd == "refresh-breakpoints":
                self._apply_breakpoints()
                return {"ok": True}
            # paused-state commands
            if cmd in ("continue", "step-over", "step-into", "step-out"):
                return self._do_step(cmd)
            if cmd == "evaluate":
                return self._do_eval(req.get("expr", ""))
            if cmd == "listvars":
                return self._do_listvars(page=int(req.get("page", 1)))
            if cmd == "frame":
                return self._do_frame(int(req.get("index", 0)))
            if cmd == "variable":
                return self._do_variable(int(req.get("ref", 0)), page=int(req.get("page", 1)))
            return {"error": f"unknown command: {cmd}"}
        except DAPError as e:
            return {"error": f"dap: {e}"}

    def _reply_status(self) -> dict[str, Any]:
        with self.lock:
            session = st.load_session(self.cwd) or {}
            return {"ok": True, "session": session}

    def _do_kill(self) -> dict[str, Any]:
        try:
            self.client.request("disconnect", {"terminateDebuggee": True}, timeout=3.0)
        except DAPError:
            pass
        # make sure the debuggee is dead
        try:
            os.kill(self.debuggee_pid, 9)
        except OSError:
            pass
        self._set_status("terminated")
        self._shutdown.set()
        return {"ok": True}

    def _require_paused(self) -> dict[str, Any] | None:
        if self.status != "paused":
            return {"error": f"not paused (status={self.status})"}
        return None

    def _do_step(self, cmd: str) -> dict[str, Any]:
        err = self._require_paused()
        if err:
            return err
        assert self.current_thread is not None
        mapping = {
            "continue": ("continue", {"threadId": self.current_thread}),
            "step-over": ("next", {"threadId": self.current_thread}),
            "step-into": ("stepIn", {"threadId": self.current_thread}),
            "step-out": ("stepOut", {"threadId": self.current_thread}),
        }
        dap_cmd, dap_args = mapping[cmd]
        self._set_status("running")
        self.client.request(dap_cmd, dap_args)
        ev = self.client.wait_for_event({"stopped", "terminated", "exited"}, timeout=300.0)
        self._absorb_event(ev)
        return self._pause_payload()

    def _do_eval(self, expr: str) -> dict[str, Any]:
        err = self._require_paused()
        if err:
            return err
        body = self.client.request("evaluate", {"expression": expr, "frameId": self.current_frame, "context": "repl"})
        out = {
            "result": fmt.truncate(str(body.get("result", ""))),
            "type": body.get("type", ""),
            "ref": body.get("variablesReference", 0) or 0,
        }
        return {"ok": True, "eval": out}

    def _do_listvars(self, page: int) -> dict[str, Any]:
        err = self._require_paused()
        if err:
            return err
        if self.current_frame is None:
            return {"error": "no active frame"}
        scopes = self.client.request("scopes", {"frameId": self.current_frame}).get("scopes", [])
        out = []
        for scope in scopes:
            ref = scope.get("variablesReference", 0)
            if not ref:
                continue
            variables = self.client.request("variables", {"variablesReference": ref}).get("variables", [])
            variables = fmt.filter_dunders(variables)
            formatted = [fmt.format_variable(v) for v in variables]
            paged = fmt.paginate(formatted, page=page)
            out.append({
                "scope": scope.get("name"),
                "expensive": bool(scope.get("expensive")),
                **paged,
            })
        return {"ok": True, "pause": self.last_pause, "scopes": out}

    def _do_frame(self, index: int) -> dict[str, Any]:
        err = self._require_paused()
        if err:
            return err
        stack = self.last_pause.get("stack", [])
        if index < 0 or index >= len(stack):
            return {"error": f"frame index out of range (0..{len(stack)-1})"}
        self.current_frame = stack[index]["id"]
        file = stack[index].get("file", "")
        line = stack[index].get("line", 0)
        self.last_pause["file"] = file
        self.last_pause["line"] = line
        self.last_pause["function"] = stack[index].get("function")
        self.last_pause["source"] = fmt.source_context(file, line) if file else []
        self._write_session()
        return {"ok": True, "pause": self.last_pause}

    def _do_variable(self, ref: int, page: int) -> dict[str, Any]:
        err = self._require_paused()
        if err:
            return err
        if ref <= 0:
            return {"error": "invalid ref"}
        variables = self.client.request("variables", {"variablesReference": ref}).get("variables", [])
        variables = fmt.filter_dunders(variables)
        formatted = [fmt.format_variable(v) for v in variables]
        paged = fmt.paginate(formatted, page=page)
        return {"ok": True, "ref": ref, **paged}

    def _pause_payload(self) -> dict[str, Any]:
        if self.status == "paused":
            return {"ok": True, "status": "paused", "pause": self.last_pause}
        return {"ok": True, "status": self.status}


def run_daemon(cwd: Path, dap_port: int, debuggee_pid: int, break_on: str) -> None:
    d = Daemon(cwd=cwd, dap_port=dap_port, debuggee_pid=debuggee_pid, break_on=break_on)
    d.run()


def spawn_daemon(cwd: Path, dap_port: int, debuggee_pid: int, break_on: str) -> int:
    """Fork a detached daemon process. Returns daemon pid."""
    import subprocess
    log = open(st.log_path(cwd), "ab")
    # Prefer the venv python (sys.prefix) over sys.executable which may
    # resolve through symlinks to the base interpreter, losing site-packages.
    venv_python = Path(sys.prefix) / "bin" / "python3"
    python = str(venv_python) if venv_python.exists() else sys.executable
    cmd = [
        python,
        "-m",
        "agent_py.daemon",
        "--cwd",
        str(cwd),
        "--port",
        str(dap_port),
        "--debuggee-pid",
        str(debuggee_pid),
        "--break-on",
        break_on,
    ]
    # Propagate VIRTUAL_ENV so the subprocess activates the same venv.
    env = os.environ.copy()
    if "VIRTUAL_ENV" not in env and sys.prefix != sys.base_prefix:
        env["VIRTUAL_ENV"] = sys.prefix
    proc = subprocess.Popen(
        cmd,
        stdout=log,
        stderr=log,
        stdin=subprocess.DEVNULL,
        start_new_session=True,
        close_fds=True,
        env=env,
    )
    log.close()
    return proc.pid


def _entrypoint() -> None:
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("--cwd", required=True)
    p.add_argument("--port", type=int, required=True)
    p.add_argument("--debuggee-pid", type=int, required=True)
    p.add_argument("--break-on", default="uncaught")
    ns = p.parse_args()
    run_daemon(Path(ns.cwd), ns.port, ns.debuggee_pid, ns.break_on)


if __name__ == "__main__":
    _entrypoint()
