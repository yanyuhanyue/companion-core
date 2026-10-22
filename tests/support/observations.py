"""Loopback operational snapshots with real HTTP and bounded server shutdown."""

import json
import socket
import threading
import time
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from companion import ops_status


def records(path):
    return [json.loads(line) for line in path.read_text("utf-8").splitlines()]


@contextmanager
def status_server(core, *, fail=False, stale=False, mutate=None, delays=(), ipv6=False, tls=None):
    requests = []
    instance = ["a" * 32]

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def do_GET(self):
            requests.append((self.path, self.headers["Authorization"], self.headers["Host"]))
            snapshot = ops_status.snapshot(core.settings)
            if stale:
                snapshot["observed_at"] = (datetime.now(UTC) - timedelta(minutes=10)).isoformat()
            if mutate:
                mutate(snapshot)
            body = json.dumps({"operations": snapshot,
                               "observation": {"api_instance": instance[0]}}).encode()
            self.send_response(503 if fail else 200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            try:
                for index in range(len(delays) + 1):
                    if index:
                        time.sleep(delays[index - 1])
                    start = index * 10
                    part = body[start:start + 10] if index < len(delays) else body[start:]
                    self.wfile.write(part)
                    self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError):
                pass

    class IPv6Server(ThreadingHTTPServer):
        address_family = socket.AF_INET6

    try:
        server = (IPv6Server if ipv6 else ThreadingHTTPServer)(
            ("::1" if ipv6 else "127.0.0.1", 0), Handler,
        )
    except OSError:
        if ipv6:
            pytest.skip("IPv6 loopback is unavailable")
        raise
    if tls:
        server.socket = tls.wrap_socket(server.socket, server_side=True)
    thread = threading.Thread(target=server.serve_forever,
                              kwargs={"poll_interval": 0.01}, daemon=True)
    thread.start()
    try:
        scheme, host = "https" if tls else "http", "[::1]" if ipv6 else "127.0.0.1"
        yield f"{scheme}://{host}:{server.server_port}", requests, instance
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)
