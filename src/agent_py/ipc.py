"""Unix-socket JSON request/response between CLI and daemon.

Wire format: one line of JSON per message, newline-terminated.
"""
from __future__ import annotations

import json
import os
import socket
import threading
from pathlib import Path
from typing import Any, Callable


def send_request(sock_path: Path, request: dict[str, Any], timeout: float = 120.0) -> dict[str, Any]:
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.settimeout(timeout)
    s.connect(str(sock_path))
    try:
        s.sendall((json.dumps(request) + "\n").encode("utf-8"))
        buf = b""
        while b"\n" not in buf:
            chunk = s.recv(65536)
            if not chunk:
                break
            buf += chunk
        line, _, _ = buf.partition(b"\n")
        if not line:
            raise RuntimeError("daemon closed connection without response")
        return json.loads(line.decode("utf-8"))
    finally:
        try:
            s.close()
        except OSError:
            pass


class IPCServer:
    """Accepts one client at a time; dispatches each request to a handler callback."""

    def __init__(self, sock_path: Path, handler: Callable[[dict[str, Any]], dict[str, Any]]):
        self.sock_path = sock_path
        self.handler = handler
        self._sock: socket.socket | None = None
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()

    def start(self) -> None:
        if self.sock_path.exists():
            try:
                self.sock_path.unlink()
            except OSError:
                pass
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.bind(str(self.sock_path))
        os.chmod(str(self.sock_path), 0o600)
        s.listen(4)
        s.settimeout(0.5)
        self._sock = s
        self._thread = threading.Thread(target=self._serve, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._sock is not None:
            try:
                self._sock.close()
            except OSError:
                pass
        try:
            self.sock_path.unlink()
        except OSError:
            pass

    def _serve(self) -> None:
        assert self._sock is not None
        while not self._stop.is_set():
            try:
                conn, _ = self._sock.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            try:
                self._handle_conn(conn)
            finally:
                try:
                    conn.close()
                except OSError:
                    pass

    def _handle_conn(self, conn: socket.socket) -> None:
        conn.settimeout(None)
        buf = b""
        while b"\n" not in buf:
            try:
                chunk = conn.recv(65536)
            except OSError:
                return
            if not chunk:
                return
            buf += chunk
        line, _, _ = buf.partition(b"\n")
        try:
            req = json.loads(line.decode("utf-8"))
        except json.JSONDecodeError:
            resp = {"error": "invalid json"}
        else:
            try:
                resp = self.handler(req)
            except Exception as e:
                resp = {"error": f"{type(e).__name__}: {e}"}
        try:
            conn.sendall((json.dumps(resp) + "\n").encode("utf-8"))
        except OSError:
            pass
