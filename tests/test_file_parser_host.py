"""Synthetic Docker transport, real framing/storage and independent-process socket checks."""

import hashlib
import json
import multiprocessing
import socket
import struct
import sys
import tempfile
import threading
import time
from dataclasses import asdict, replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from companion import file_parser
from companion import file_parser_client as client
from companion import file_parser_host as launcher
from scripts import smoke_file_parser

IMAGE = "sha256:" + "a" * 64
LIMITS = replace(file_parser.DEFAULT_LIMITS, max_text_chars=12000)
DATA = b"first\n\nsecond"


def synthetic_processor(data, filename, mime, **kwargs):
    # This substitutes only Docker; it does not establish native container isolation.
    return file_parser.parse_document(data, filename, mime, limits=kwargs["limits"]) | {
        "isolation": "docker_network_none",
    }


class SyntheticHost(launcher.ParserHost):
    def _docker(self, arguments):
        return SimpleNamespace(returncode=0, stdout=b"")


def make_host(tmp_path, **kwargs):
    return SyntheticHost(state_dir=tmp_path / "state", image=IMAGE, limits=LIMITS,
                          docker=str(tmp_path / "docker"), processor=synthetic_processor, **kwargs)


def request(data=DATA, *, request_id="content_" + "b" * 32, **kwargs):
    return client._request(data, "notes.txt", "text/plain", image=IMAGE, limits=LIMITS,
                            request_id=request_id) | kwargs


def exchange(host, header=None, data=DATA, *, trailing=b""):
    caller, server = socket.socketpair()
    worker = threading.Thread(target=host.handle, args=(server,))
    worker.start()
    try:
        with caller:
            caller.settimeout(5)
            try:
                client._send_frame(caller, header if header is not None else request(data), 16384)
                caller.sendall(data + trailing)
                caller.shutdown(socket.SHUT_WR)
                result = client._read_frame(caller, LIMITS.max_result_bytes)
                assert caller.recv(1) == b""
                return result
            except (EOFError, ConnectionError):
                return None
    finally:
        worker.join(5)
        assert not worker.is_alive()


def test_host_consumes_before_parse_and_keeps_only_hashes(tmp_path, monkeypatch):
    monkeypatch.setenv("COMPANION_OWNER_TOKEN", "secret-canary")
    host = make_host(tmp_path)
    calls = []

    def process(data, filename, mime, **kwargs):
        record = json.loads(next((tmp_path / "state").glob("*.json")).read_text())
        assert record["phase"] == "STARTED"
        assert record["container"] == kwargs["container_name"]
        assert kwargs["owner_label"] == host.instance
        assert kwargs["image"] == IMAGE
        assert "COMPANION_OWNER_TOKEN" not in kwargs["docker_env"]
        assert set(kwargs["docker_env"]) == {"PATH", "LANG", "HOME", "DOCKER_CONFIG", "DOCKER_HOST"}
        calls.append(data)
        return synthetic_processor(data, filename, mime, **kwargs)

    host._processor = process
    try:
        result = exchange(host)
        assert result["status"] == "SUCCESS" and result["text"] == "first\nsecond"
        assert calls == [DATA]
        records = list((tmp_path / "state").glob("*.json"))
        saved = records[0].read_text()
        assert "notes.txt" not in saved and "first" not in saved and "text/plain" not in saved
        assert json.loads(saved)["phase"] == "FINISHED"
        assert exchange(host)["error_code"] == "PARSER_REQUEST_ALREADY_CONSUMED"
        assert exchange(host, request(b"different"), b"different")["error_code"] == (
            "PARSER_REQUEST_CONFLICT"
        )
        assert calls == [DATA]
    finally:
        host.close()


@pytest.mark.parametrize("change", [
    {"version": True}, {"image": "parser:latest"}, {"image": "sha256:" + "c" * 64},
    {"path": "/etc/shadow"}, {"bytes": True}, {"bytes": 0}, {"bytes": 2**31},
    {"sha256": "0" * 64}, {"request_id": "../bad"}, {"filename": "../escape.txt"},
    {"mime_type": "application/pdf"}, {"limits": {}},
    {"limits": asdict(LIMITS) | {"wall_seconds": LIMITS.wall_seconds + 1}},
    {"limits": asdict(LIMITS) | {"cpu_seconds": True}},
])
def test_bad_request_never_consumes_or_invokes_parser(tmp_path, change):
    host = make_host(tmp_path)
    host._processor = lambda *args, **kwargs: pytest.fail("invalid request reached parser")
    try:
        assert exchange(host, request() | change) is None
        assert host.records == {}
        assert not list(host.root.glob("*.json"))
    finally:
        host.close()


@pytest.mark.parametrize("data,trailing", [(DATA[:-1], b""), (DATA, b"another request")])
def test_truncated_or_extra_original_never_launches(tmp_path, data, trailing):
    host = make_host(tmp_path)
    try:
        assert exchange(host, request(), data, trailing=trailing) is None
        assert not host.records
    finally:
        host.close()


def test_bad_frame_size_and_duplicate_keys_rejected_before_launch(tmp_path):
    host = make_host(tmp_path)
    try:
        for payload in [struct.pack("!I", 8193), b'\x00\x00\x00\x0d{"a":1,"a":1}']:
            caller, server = socket.socketpair()
            worker = threading.Thread(target=host.handle, args=(server,))
            worker.start()
            with caller:
                caller.sendall(payload)
                caller.shutdown(socket.SHUT_WR)
                assert caller.recv(1) == b""
            worker.join(2)
            assert not worker.is_alive()
        assert not host.records
    finally:
        host.close()


def test_restarts_never_reparse_finished_or_interrupted_request(tmp_path):
    host = make_host(tmp_path)
    assert exchange(host)["status"] == "SUCCESS"
    record_path = next(host.root.glob("*.json"))
    record = json.loads(record_path.read_text())
    host.close()
    # An interrupted launch leaves exactly the durable STARTED consumption marker.
    launcher._write(record_path, record | {"phase": "STARTED", "result_sha256": None})
    host = make_host(tmp_path)
    try:
        assert json.loads(record_path.read_text())["phase"] == "UNKNOWN"
        host._processor = lambda *args, **kwargs: pytest.fail("restarted request was reparsed")
        assert exchange(host)["error_code"] == "PARSER_REQUEST_ALREADY_CONSUMED"
    finally:
        host.close()


def test_history_capacity_is_not_an_eviction_policy(tmp_path):
    host = make_host(tmp_path, max_history=1)
    try:
        assert exchange(host)["status"] == "SUCCESS"
        assert exchange(host, request(request_id="content_new"))["error_code"] == (
            "PARSER_STATE_CAPACITY_EXCEEDED"
        )
        assert exchange(host)["error_code"] == "PARSER_REQUEST_ALREADY_CONSUMED"
        assert len(host.records) == 1
    finally:
        host.close()


def test_single_instance_lock_and_corrupt_state_fail_closed(tmp_path):
    host = make_host(tmp_path)
    try:
        with pytest.raises(OSError):
            make_host(tmp_path)
        assert exchange(host)["status"] == "SUCCESS"
        path = next(host.root.glob("*.json"))
    finally:
        host.close()
    path.write_text("{incomplete", encoding="utf-8")
    with pytest.raises(ValueError):
        make_host(tmp_path)


def test_startup_cleanup_only_uses_label_and_known_consumed_names(tmp_path, monkeypatch):
    host = make_host(tmp_path)
    assert exchange(host)["status"] == "SUCCESS"
    record = next(iter(host.records.values()))
    instance = host.instance
    host.close()
    calls = []

    def docker(self, arguments):
        calls.append(arguments)
        return SimpleNamespace(returncode=0, stdout=(record["container"] + "\n").encode()
                               if arguments[0] == "ps" else b"")

    monkeypatch.setattr(SyntheticHost, "_docker", docker)
    host = make_host(tmp_path)
    try:
        assert host.available
        assert calls == [["ps", "-a", "--filter", f"label={launcher.LABEL}={instance}",
                          "--format", "{{.Names}}"], ["rm", "--force", record["container"]]]
    finally:
        host.close()
    calls.clear()
    record["container"] = "companion-file-" + "f" * 32
    host = make_host(tmp_path)
    try:
        assert not host.available
        assert len(calls) == 1  # A label match alone does not authorize removal.
        assert exchange(host, request(request_id="new_request"))["error_code"] == (
            "PARSER_CLEANUP_UNCONFIRMED"
        )
    finally:
        host.close()


def test_failed_cleanup_fences_future_requests(tmp_path):
    host = make_host(tmp_path)
    host._cleanup = lambda name: False
    try:
        result = exchange(host)
        assert result["status"] == "UNAVAILABLE"
        assert result["error_code"] == "PARSER_CLEANUP_UNCONFIRMED"
        assert next(iter(host.records.values()))["phase"] == "UNKNOWN"
        assert exchange(host, request(request_id="next_request"))["error_code"] == (
            "PARSER_CLEANUP_UNCONFIRMED"
        )
    finally:
        host.close()


@pytest.mark.parametrize("phase", ["consume", "finish"])
def test_durable_state_failure_never_causes_an_unrecorded_retry(tmp_path, monkeypatch, phase):
    host = make_host(tmp_path)
    write = launcher._write
    calls = []

    def fail(path, value, *, exclusive=False):
        if path.suffix == ".json" and exclusive == (phase == "consume"):
            raise OSError("synthetic storage failure")
        return write(path, value, exclusive=exclusive)

    def process(*args, **kwargs):
        calls.append(1)
        return synthetic_processor(*args, **kwargs)

    monkeypatch.setattr(launcher, "_write", fail)
    host._processor = process
    try:
        assert exchange(host)["error_code"] == "PARSER_STATE_UNCONFIRMED"
        assert len(calls) == (phase == "finish")
        assert exchange(host, request(request_id="another"))["error_code"] == (
            "PARSER_CLEANUP_UNCONFIRMED"
        )
        assert len(calls) == (phase == "finish")
    finally:
        host.close()


def test_invalid_processor_evidence_is_consumed_without_reparse(tmp_path):
    host = make_host(tmp_path)
    host._processor = lambda *args, **kwargs: synthetic_processor(*args, **kwargs) | {
        "file_sha256": "f" * 64,
    }
    try:
        result = exchange(host)
        assert result["status"] == "UNAVAILABLE" and not result["text"]
        assert result["error_code"] == "PARSER_PROCESSOR_FAILED"
        assert exchange(host)["error_code"] == "PARSER_REQUEST_ALREADY_CONSUMED"
    finally:
        host.close()


def test_disconnected_client_does_not_cancel_consumed_operation(tmp_path):
    started, finish, completed = threading.Event(), threading.Event(), threading.Event()
    host = make_host(tmp_path)

    def process(*args, **kwargs):
        started.set()
        assert finish.wait(3)
        completed.set()
        return synthetic_processor(*args, **kwargs)

    host._processor = process
    caller, server = socket.socketpair()
    worker = threading.Thread(target=host.handle, args=(server,))
    worker.start()
    try:
        client._send_frame(caller, request(), 8192)
        caller.sendall(DATA)
        caller.shutdown(socket.SHUT_WR)
        assert started.wait(3)
        caller.close()
        assert not completed.is_set()
        finish.set()
        worker.join(3)
        assert completed.is_set() and not worker.is_alive()
        assert next(iter(host.records.values()))["phase"] == "FINISHED"
        assert exchange(host)["error_code"] == "PARSER_REQUEST_ALREADY_CONSUMED"
    finally:
        caller.close()
        finish.set()
        worker.join(3)
        host.close()


def test_concurrent_duplicate_cannot_start_another_parser(tmp_path):
    host = make_host(tmp_path, max_concurrency=2)
    started, finish = threading.Event(), threading.Event()
    calls, results = [], []

    def process(*args, **kwargs):
        calls.append(1)
        started.set()
        assert finish.wait(3)
        return synthetic_processor(*args, **kwargs)

    host._processor = process
    first = threading.Thread(target=lambda: results.append(exchange(host)))
    first.start()
    try:
        assert started.wait(3)
        assert exchange(host)["error_code"] == "PARSER_REQUEST_ALREADY_CONSUMED"
        finish.set()
        first.join(3)
        assert calls == [1] and results[0]["status"] == "SUCCESS"
    finally:
        finish.set()
        first.join(3)
        host.close()


def test_concurrency_is_bounded_before_body_and_total_read_deadline(tmp_path):
    host = make_host(tmp_path, read_timeout=1)
    first, server = socket.socketpair()
    worker = threading.Thread(target=host.handle, args=(server,))
    worker.start()
    try:
        # The first client has not supplied a header; it still occupies the sole slot.
        deadline = time.monotonic() + 1
        while host._slots._value and time.monotonic() < deadline:
            time.sleep(0.005)
        second, another = socket.socketpair()
        with second:
            host.handle(another)
            assert second.recv(1) == b""
        worker.join(2)
        assert not worker.is_alive() and not host.records
    finally:
        first.close()
        worker.join(2)
        host.close()


class _ConnectedSocket:
    """Only AF_UNIX connect is substituted; all framing uses real connected sockets."""
    def __init__(self, connection):
        self.connection = connection

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.connection.close()

    def __getattr__(self, name):
        return getattr(self.connection, name)

    def connect(self, path):
        assert path == "/synthetic/parser.sock"


def _socket_transport(monkeypatch, connection):
    monkeypatch.setattr(client, "socket", SimpleNamespace(
        AF_UNIX=1, SOCK_STREAM=socket.SOCK_STREAM, SHUT_WR=socket.SHUT_WR,
        socket=lambda *args: _ConnectedSocket(connection),
    ))


def _child_handle(connection, state_dir):
    host = SyntheticHost(state_dir=state_dir, image=IMAGE, limits=LIMITS,
                          docker=str(Path(state_dir).parent / "docker"),
                          processor=synthetic_processor)
    try:
        host.handle(connection)
    finally:
        host.close()


def test_client_and_independent_host_process_through_real_framing(tmp_path, monkeypatch):
    caller, server = socket.socketpair()
    process = multiprocessing.get_context("spawn").Process(
        target=_child_handle, args=(server, str(tmp_path / "state")),
    )
    process.start()
    server.close()
    _socket_transport(monkeypatch, caller)
    try:
        result = client.parse_via_socket(DATA, "notes.txt", "text/plain", image=IMAGE,
                                         limits=LIMITS, socket_path="/synthetic/parser.sock",
                                         request_id="content_process")
        assert result["status"] == "SUCCESS"
        assert result["file_sha256"] == hashlib.sha256(DATA).hexdigest()
        assert result["records"][1]["source"] == {"line_start": 3, "line_end": 3}
        process.join(5)
        assert process.exitcode == 0
        assert json.loads(next((tmp_path / "state").glob("*.json")).read_text())["phase"] == (
            "FINISHED"
        )
    finally:
        if process.is_alive():
            process.kill()
        process.join(5)


@pytest.mark.parametrize("change", [
    {"file_sha256": "0" * 64}, {"filename": "another.txt"}, {"isolation": "not_asserted"},
    {"text_sha256": "0" * 64}, {"ocr": "completed"},
])
def test_client_rejects_changed_evidence(tmp_path, monkeypatch, change):
    caller, server = socket.socketpair()
    _socket_transport(monkeypatch, caller)

    def reply():
        with server:
            client._read_frame(server, 8192)
            client._read_exact(server, len(DATA))
            assert server.recv(1) == b""
            result = synthetic_processor(DATA, "notes.txt", "text/plain", limits=LIMITS) | change
            client._send_frame(server, result, LIMITS.max_result_bytes)

    worker = threading.Thread(target=reply)
    worker.start()
    result = client.parse_via_socket(DATA, "notes.txt", "text/plain", image=IMAGE, limits=LIMITS,
                                     socket_path="/synthetic/parser.sock", request_id="changed")
    worker.join(3)
    assert result["status"] == "UNAVAILABLE" and result["error_code"] == "PARSER_PROTOCOL_INVALID"


def test_socket_disconnect_is_unknown_and_never_reconnects(monkeypatch):
    caller, server = socket.socketpair()
    _socket_transport(monkeypatch, caller)
    server.close()
    result = client.parse_via_socket(DATA, "notes.txt", "text/plain", image=IMAGE, limits=LIMITS,
                                     socket_path="/synthetic/parser.sock", request_id="lost")
    assert result["status"] == "UNAVAILABLE"
    assert result["error_code"] == "PARSER_CLEANUP_UNCONFIRMED"
    assert result["isolation"] == "not_asserted" and not result["text"]


def test_platform_without_unix_socket_reports_unavailable(monkeypatch):
    monkeypatch.setattr(client, "socket", SimpleNamespace())
    result = client.parse_via_socket(DATA, "notes.txt", "text/plain", image=IMAGE, limits=LIMITS,
                                     socket_path="/synthetic/parser.sock", request_id="unsupported")
    assert result["status"] == "UNAVAILABLE"
    assert result["error_code"] == "PARSER_SOCKET_UNAVAILABLE"


def _child_serve(socket_path, state_dir, stop):
    host = SyntheticHost(state_dir=state_dir, image=IMAGE, limits=LIMITS,
                          processor=synthetic_processor)
    try:
        launcher.serve(host, socket_path, stop=stop)
    finally:
        host.close()


@pytest.mark.skipif(sys.platform != "linux" or not hasattr(socket, "AF_UNIX"),
                    reason="Native Unix sockets require Linux; socketpair coverage is separate")
def test_native_unix_socket_two_process_permissions_and_replay():
    root = Path(__file__).resolve().parents[1] / ".local"
    root.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="uds-", dir=root) as directory:
        base = Path(directory)
        endpoint, state = base / "sock" / "parser.sock", base / "state"
        ctx = multiprocessing.get_context("spawn")
        stop = ctx.Event()
        process = ctx.Process(target=_child_serve, args=(str(endpoint), str(state), stop))
        process.start()
        try:
            deadline = time.monotonic() + 5
            while not endpoint.exists() and time.monotonic() < deadline:
                time.sleep(0.01)
            assert endpoint.exists()
            assert endpoint.stat().st_mode & 0o777 == 0o600
            result = client.parse_via_socket(DATA, "notes.txt", "text/plain", image=IMAGE,
                                             limits=LIMITS, socket_path=endpoint,
                                             request_id="native_socket")
            assert result["status"] == "SUCCESS"
            again = client.parse_via_socket(DATA, "notes.txt", "text/plain", image=IMAGE,
                                            limits=LIMITS, socket_path=endpoint,
                                            request_id="native_socket")
            assert again["error_code"] == "PARSER_REQUEST_ALREADY_CONSUMED"
        finally:
            stop.set()
            process.join(5)
            if process.is_alive():
                process.kill()
                process.join(5)
        assert process.exitcode == 0 and not endpoint.exists()


def test_host_launcher_does_not_import_core_services_or_read_secrets():
    import ast
    tree = ast.parse(Path(launcher.__file__).read_text(encoding="utf-8"))
    imports = [node.module for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)]
    assert set(item for item in imports if item and item.startswith("companion")) <= {
        "companion", "companion.file_parser_client",
    }
    assert "os.environ" not in Path(launcher.__file__).read_text(encoding="utf-8")


@pytest.mark.parametrize("daemon_unavailable", [False, True])
def test_native_deadline_smoke_requires_observed_container_exit(tmp_path, monkeypatch,
                                                               daemon_unavailable):
    calls = []
    process = SimpleNamespace(returncode=None)

    def kill():
        calls.append("kill-launcher")
        process.returncode = -9

    process.poll, process.kill = lambda: process.returncode, kill
    process.wait = lambda **kwargs: process.returncode

    def start(command, **kwargs):
        process.container = json.loads(command[-1])[4].split("=", 1)[1]
        return process

    def docker(command, **kwargs):
        calls.append(command[1])
        if command[1] == "ps":
            if daemon_unavailable and process.returncode is not None:
                return SimpleNamespace(returncode=1, stdout=b"")
            running = (process.container + " running\n").encode()
            return SimpleNamespace(returncode=0,
                                   stdout=running if process.returncode is None else b"")
        return SimpleNamespace(returncode=0, stdout=b"")

    monkeypatch.setattr(smoke_file_parser, "sys", SimpleNamespace(platform="linux",
                                                                 executable=sys.executable))
    monkeypatch.setattr(smoke_file_parser.subprocess, "Popen", start)
    monkeypatch.setattr(smoke_file_parser.subprocess, "run", docker)
    if daemon_unavailable:
        with pytest.raises(RuntimeError, match="cannot confirm native container state"):
            smoke_file_parser.verify_launcher_death(IMAGE, tmp_path, "docker")
    else:
        result = smoke_file_parser.verify_launcher_death(IMAGE, tmp_path, "docker")
        assert result["launcher_signal"] == "SIGKILL" and result["container_removed"]
    assert calls == ["ps", "kill-launcher", "ps", "rm"]
