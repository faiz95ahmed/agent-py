"""Minimal Debug Adapter Protocol client over TCP.

Wire format: `Content-Length: N\\r\\n\\r\\n<json>` frames.
One reader thread demultiplexes responses (keyed by request seq) and events.
"""
from __future__ import annotations

import json
import queue
import socket
import threading
from typing import Any, Callable


class DAPError(Exception):
    pass


class DAPClient:
    def __init__(self, host: str = "127.0.0.1", port: int = 0):
        self.host = host
        self.port = port
        self.sock: socket.socket | None = None
        self._buf = b""
        self._seq = 0
        self._seq_lock = threading.Lock()
        self._responses: dict[int, "queue.Queue[dict[str, Any]]"] = {}
        self._events: "queue.Queue[dict[str, Any]]" = queue.Queue()
        self._reader: threading.Thread | None = None
        self._closed = threading.Event()
        self._write_lock = threading.Lock()
        self._event_listeners: list[Callable[[dict[str, Any]], None]] = []

    def connect(self, timeout: float = 10.0) -> None:
        deadline_exc: Exception | None = None
        import time
        start = time.monotonic()
        while time.monotonic() - start < timeout:
            try:
                s = socket.create_connection((self.host, self.port), timeout=2.0)
                s.settimeout(None)
                self.sock = s
                self._reader = threading.Thread(target=self._read_loop, daemon=True)
                self._reader.start()
                return
            except (ConnectionRefusedError, OSError) as e:
                deadline_exc = e
                time.sleep(0.1)
        raise DAPError(f"could not connect to {self.host}:{self.port}: {deadline_exc}")

    def on_event(self, fn: Callable[[dict[str, Any]], None]) -> None:
        self._event_listeners.append(fn)

    def close(self) -> None:
        self._closed.set()
        if self.sock is not None:
            try:
                self.sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            try:
                self.sock.close()
            except OSError:
                pass
            self.sock = None

    def _next_seq(self) -> int:
        with self._seq_lock:
            self._seq += 1
            return self._seq

    def _send(self, msg: dict[str, Any]) -> None:
        data = json.dumps(msg).encode("utf-8")
        header = f"Content-Length: {len(data)}\r\n\r\n".encode("ascii")
        with self._write_lock:
            assert self.sock is not None
            self.sock.sendall(header + data)

    def _read_loop(self) -> None:
        try:
            while not self._closed.is_set():
                msg = self._read_frame()
                if msg is None:
                    break
                self._dispatch(msg)
        finally:
            # signal everyone waiting
            terminated = {"type": "event", "event": "terminated", "body": {"reason": "connection_closed"}}
            self._events.put(terminated)
            for q in list(self._responses.values()):
                q.put({"type": "response", "success": False, "message": "connection closed"})

    def _read_frame(self) -> dict[str, Any] | None:
        # read headers
        headers = b""
        while b"\r\n\r\n" not in headers:
            chunk = self._recv_some()
            if not chunk:
                return None
            headers += chunk
        header_part, _, rest = headers.partition(b"\r\n\r\n")
        length = 0
        for line in header_part.split(b"\r\n"):
            if line.lower().startswith(b"content-length:"):
                length = int(line.split(b":", 1)[1].strip())
        body = rest
        while len(body) < length:
            chunk = self._recv_some()
            if not chunk:
                return None
            body += chunk
        # leftover (unlikely — DAP uses fixed framing) stays in self._buf
        if len(body) > length:
            self._buf = body[length:]
            body = body[:length]
        try:
            return json.loads(body.decode("utf-8"))
        except json.JSONDecodeError:
            return None

    def _recv_some(self) -> bytes:
        if self._buf:
            chunk = self._buf
            self._buf = b""
            return chunk
        assert self.sock is not None
        try:
            return self.sock.recv(65536)
        except OSError:
            return b""

    def _dispatch(self, msg: dict[str, Any]) -> None:
        t = msg.get("type")
        if t == "response":
            req_seq = msg.get("request_seq")
            q = self._responses.get(req_seq)
            if q is not None:
                q.put(msg)
        elif t == "event":
            for fn in self._event_listeners:
                try:
                    fn(msg)
                except Exception:
                    pass
            self._events.put(msg)
        # "request" from server (reverse requests) is rare; ignore for v1.

    def send_request(self, command: str, arguments: dict[str, Any] | None = None) -> int:
        """Send a request without waiting. Returns the seq; pair with await_response."""
        seq = self._next_seq()
        q: "queue.Queue[dict[str, Any]]" = queue.Queue()
        self._responses[seq] = q
        msg = {"seq": seq, "type": "request", "command": command}
        if arguments is not None:
            msg["arguments"] = arguments
        self._send(msg)
        return seq

    def await_response(self, seq: int, timeout: float = 30.0) -> dict[str, Any]:
        q = self._responses.get(seq)
        if q is None:
            raise DAPError(f"no pending request with seq={seq}")
        try:
            resp = q.get(timeout=timeout)
        except queue.Empty:
            self._responses.pop(seq, None)
            raise DAPError(f"timed out waiting for response seq={seq}")
        self._responses.pop(seq, None)  # clean up after success
        if not resp.get("success", False):
            raise DAPError(f"{resp.get('command', seq)} failed: {resp.get('message', 'unknown')}")
        return resp.get("body") or {}

    def request(self, command: str, arguments: dict[str, Any] | None = None, timeout: float = 30.0) -> dict[str, Any]:
        seq = self.send_request(command, arguments)
        return self.await_response(seq, timeout=timeout)

    def wait_for_event(self, names: set[str], timeout: float = 60.0) -> dict[str, Any]:
        """Drain events until one with name in `names` arrives. Returns the full event dict."""
        import time
        deadline = time.monotonic() + timeout
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise DAPError(f"timed out waiting for events {names}")
            try:
                ev = self._events.get(timeout=remaining)
            except queue.Empty:
                raise DAPError(f"timed out waiting for events {names}")
            if ev.get("event") in names:
                return ev
