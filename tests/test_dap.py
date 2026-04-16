from __future__ import annotations

import json
import socket
import threading
import time

from agent_py.dap import DAPClient


def _serve_one(port_holder: list[int], script):
    """Start a TCP server on an ephemeral port and run `script(conn)` against the first connection."""
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    port_holder.append(srv.getsockname()[1])

    def run():
        conn, _ = srv.accept()
        try:
            script(conn)
        finally:
            try:
                conn.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            conn.close()
            srv.close()

    t = threading.Thread(target=run, daemon=True)
    t.start()
    return t


def _send_frame(sock, obj):
    data = json.dumps(obj).encode("utf-8")
    sock.sendall(f"Content-Length: {len(data)}\r\n\r\n".encode("ascii") + data)


def _read_frame(sock):
    buf = b""
    while b"\r\n\r\n" not in buf:
        chunk = sock.recv(65536)
        if not chunk:
            return None
        buf += chunk
    header, _, rest = buf.partition(b"\r\n\r\n")
    length = 0
    for line in header.split(b"\r\n"):
        if line.lower().startswith(b"content-length:"):
            length = int(line.split(b":", 1)[1].strip())
    body = rest
    while len(body) < length:
        chunk = sock.recv(65536)
        if not chunk:
            return None
        body += chunk
    return json.loads(body[:length].decode("utf-8"))


def test_request_response_correlation():
    port_holder: list[int] = []

    def script(conn):
        # respond to two requests, out-of-order
        req1 = _read_frame(conn)
        req2 = _read_frame(conn)
        assert req1["command"] == "a"
        assert req2["command"] == "b"
        # respond to req2 first
        _send_frame(conn, {"type": "response", "request_seq": req2["seq"], "success": True, "command": "b", "body": {"which": "b"}})
        _send_frame(conn, {"type": "response", "request_seq": req1["seq"], "success": True, "command": "a", "body": {"which": "a"}})
        time.sleep(0.1)

    _serve_one(port_holder, script)
    while not port_holder:
        time.sleep(0.01)

    client = DAPClient("127.0.0.1", port_holder[0])
    client.connect()
    # issue two requests concurrently
    results: dict[str, dict] = {}

    def call(name):
        results[name] = client.request(name)

    t1 = threading.Thread(target=call, args=("a",))
    t2 = threading.Thread(target=call, args=("b",))
    t1.start(); t2.start()
    t1.join(5); t2.join(5)
    client.close()

    assert results["a"]["which"] == "a"
    assert results["b"]["which"] == "b"


def test_events_queued_and_waited():
    port_holder: list[int] = []

    def script(conn):
        _send_frame(conn, {"type": "event", "event": "initialized"})
        _send_frame(conn, {"type": "event", "event": "output", "body": {"output": "hi"}})
        _send_frame(conn, {"type": "event", "event": "stopped", "body": {"reason": "breakpoint", "threadId": 1}})
        time.sleep(0.1)

    _serve_one(port_holder, script)
    while not port_holder:
        time.sleep(0.01)

    client = DAPClient("127.0.0.1", port_holder[0])
    client.connect()
    ev = client.wait_for_event({"stopped"}, timeout=5.0)
    assert ev["event"] == "stopped"
    assert ev["body"]["reason"] == "breakpoint"
    client.close()
