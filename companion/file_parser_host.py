"""Dedicated Linux launcher. Only its private socket is exposed to the Core worker.

The state directory contains consumption hashes, never file bytes or extracted text.
It must survive restarts; reaching its capacity fails closed instead of forgetting IDs.
"""

import argparse
import hashlib
import json
import os
import re
import signal
import socket
import stat
import subprocess
import sys
import threading
import time
import uuid
from dataclasses import asdict
from pathlib import Path

from companion import file_parser
from companion.file_parser_client import (
    HEADER_BYTES,
    REQUEST_KEYS,
    _json,
    _read_exact,
    _read_frame,
    _request,
    _send_frame,
    _unavailable,
    _validate_response,
)

LABEL = "com.companion.parser-host"
RECORD_KEYS = {"version", "instance", "request_key", "fingerprint", "file_sha256",
               "container", "phase", "result_sha256"}


def _sync_directory(path):
    if os.name != "nt":
        descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)


def _write(path, value, *, exclusive=False):
    target = path if exclusive else path.with_suffix("." + uuid.uuid4().hex + ".tmp")
    descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(_json(value))
            stream.flush()
            os.fsync(stream.fileno())
        if not exclusive:
            os.replace(target, path)
        _sync_directory(path.parent)
    finally:
        if not exclusive:
            target.unlink(missing_ok=True)


def _read(path):
    if path.is_symlink():
        raise ValueError("PARSER_STATE_INVALID")
    with path.open("rb") as stream:
        data = stream.read(4097)
    if len(data) > 4096:
        raise ValueError("PARSER_STATE_INVALID")
    result = json.loads(data)
    if _json(result) != data:
        raise ValueError("PARSER_STATE_INVALID")
    return result


class _Deadline:
    def __init__(self, connection, seconds):
        self.connection, self.until = connection, time.monotonic() + seconds

    def recv(self, count):
        remaining = self.until - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("PARSER_REQUEST_TIMEOUT")
        self.connection.settimeout(remaining)
        return self.connection.recv(count)


class ParserHost:
    def __init__(self, *, state_dir, image, limits=file_parser.DEFAULT_LIMITS,
                 docker="/usr/bin/docker", docker_host="unix:///var/run/docker.sock",
                 max_concurrency=1, read_timeout=15, max_history=10000, processor=None):
        # No request can choose a command, environment, path, image, or resource ceiling.
        file_parser._container_argv(docker, image, "companion-file-" + "0" * 32,
                                     "test.txt", "", limits)
        if (not isinstance(docker_host, str) or not docker_host.startswith("unix:///")
            or "\x00" in docker_host or ".." in Path(docker_host[7:]).parts
            or not Path(docker).is_absolute() or type(max_concurrency) is not int
            or not 1 <= max_concurrency <= 8 or type(read_timeout) is not int
            or not 1 <= read_timeout <= 60 or type(max_history) is not int
            or not 1 <= max_history <= 100000):
            raise ValueError("INVALID_PARSER_HOST_CONFIG")
        self.root = Path(state_dir)
        if not self.root.is_absolute() or self.root.is_symlink():
            raise ValueError("INVALID_PARSER_STATE_DIRECTORY")
        self.root.mkdir(parents=True, mode=0o700, exist_ok=True)
        if os.name != "nt" and (self.root.stat().st_uid != os.geteuid()
                                or stat.S_IMODE(self.root.stat().st_mode) != 0o700):
            raise ValueError("PRIVATE_PARSER_STATE_REQUIRED")
        self._lock = self.root.joinpath("host.lock").open("a+b")
        try:
            if os.name == "nt":
                import msvcrt
                self._lock.write(b"0")
                self._lock.flush()
                self._lock.seek(0)
                msvcrt.locking(self._lock.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self._lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            instance_path = self.root / "instance"
            if not instance_path.exists():
                _write(instance_path, uuid.uuid4().hex, exclusive=True)
            self.instance = _read(instance_path)
            if (not isinstance(self.instance, str)
                or not re.fullmatch("[0-9a-f]{32}", self.instance)):
                raise ValueError("PARSER_STATE_INVALID")
            self.image, self.limits, self.docker = image, limits, docker
            self.read_timeout, self.max_history = read_timeout, max_history
            self._processor = processor or file_parser.parse_isolated
            self._mutex, self._slots = threading.Lock(), threading.BoundedSemaphore(max_concurrency)
            self.env = {"PATH": "/usr/bin:/bin", "LANG": "C.UTF-8", "HOME": str(self.root),
                        "DOCKER_CONFIG": str(self.root / "docker-config"),
                        "DOCKER_HOST": docker_host}
            Path(self.env["DOCKER_CONFIG"]).mkdir(mode=0o700, exist_ok=True)
            self.records = {}
            for path in self.root.glob("*.json"):
                record = _read(path)
                self._validate_record(record, path)
                self.records[record["request_key"]] = record
                if len(self.records) > max_history:
                    raise ValueError("PARSER_STATE_CAPACITY_EXCEEDED")
            self.available = self._recover()
        except BaseException:
            self._lock.close()
            raise

    def close(self):
        self._lock.close()

    def _validate_record(self, record, path):
        if (not isinstance(record, dict) or set(record) != RECORD_KEYS
            or type(record["version"]) is not int or record["version"] != 1
            or record["instance"] != self.instance
            or record["phase"] not in {"STARTED", "FINISHED", "UNKNOWN"}
            or any(not isinstance(record[key], str) or not re.fullmatch("[0-9a-f]{64}", record[key])
                   for key in ("request_key", "fingerprint", "file_sha256"))
            or record["result_sha256"] is not None and not re.fullmatch(
                "[0-9a-f]{64}", str(record["result_sha256"]))
            or not re.fullmatch(r"companion-file-[0-9a-f]{32}", str(record["container"]))
            or path.name != record["request_key"] + ".json"):
            raise ValueError("PARSER_STATE_INVALID")

    def _docker(self, arguments):
        return subprocess.run([self.docker, *arguments], shell=False, cwd=self.root, env=self.env,
                              capture_output=True, timeout=5, check=False)

    def _owned_containers(self):
        result = self._docker(["ps", "-a", "--filter", f"label={LABEL}={self.instance}",
                               "--format", "{{.Names}}"])
        if result.returncode or len(result.stdout) > 65536:
            raise OSError("PARSER_CLEANUP_UNCONFIRMED")
        names = result.stdout.decode("ascii").splitlines()
        if any(not re.fullmatch(r"companion-file-[0-9a-f]{32}", name) for name in names):
            raise OSError("PARSER_CLEANUP_UNCONFIRMED")
        return set(names)

    def _recover(self):
        try:
            owned = self._owned_containers()
            known = {record["container"] for record in self.records.values()}
            if owned - known:
                return False
            for name in owned:
                if self._docker(["rm", "--force", name]).returncode:
                    return False
            for key, record in self.records.items():
                if record["phase"] == "STARTED":
                    record = record | {"phase": "UNKNOWN"}
                    _write(self.root / (key + ".json"), record)
                    self.records[key] = record
            return True
        except (OSError, ValueError, subprocess.TimeoutExpired):
            return False

    def _cleanup(self, name):
        try:
            return name not in self._owned_containers() or (
                self._docker(["rm", "--force", name]).returncode == 0
            )
        except (OSError, ValueError, subprocess.TimeoutExpired):
            return False

    def _receive(self, connection):
        stream = _Deadline(connection, self.read_timeout)
        header = _read_frame(stream, HEADER_BYTES)
        if (not isinstance(header, dict) or set(header) != REQUEST_KEYS
            or type(header["version"]) is not int or header["version"] != 1
            or header["image"] != self.image or type(header["bytes"]) is not int
            or not 0 < header["bytes"] <= self.limits.max_file_bytes
            or not isinstance(header["limits"], dict)
            or set(header["limits"]) != set(asdict(self.limits))
            or any(type(value) is not int or not 0 < value <= asdict(self.limits)[key]
                   for key, value in header["limits"].items())):
            raise ValueError("PARSER_REQUEST_INVALID")
        limits = file_parser.Limits(**header["limits"])
        if header["bytes"] > limits.max_file_bytes:
            raise ValueError("PARSER_REQUEST_INVALID")
        data = _read_exact(stream, header["bytes"])
        if stream.recv(1):
            raise ValueError("PARSER_TRAILING_REQUEST")
        expected = _request(data, header["filename"], header["mime_type"], image=self.image,
                            limits=limits, request_id=header["request_id"])
        if header != expected:
            raise ValueError("PARSER_REQUEST_INVALID")
        return header, data, limits

    def _process(self, header, data, limits):
        filename, mime = header["filename"], header["mime_type"]

        def unavailable(code):
            return _unavailable(data, filename, mime, code)

        key = hashlib.sha256(header["request_id"].encode()).hexdigest()
        fingerprint = hashlib.sha256(_json(header)).hexdigest()
        with self._mutex:
            prior = self.records.get(key)
            if prior:
                return unavailable("PARSER_REQUEST_ALREADY_CONSUMED"
                                   if prior["fingerprint"] == fingerprint
                                   else "PARSER_REQUEST_CONFLICT")
            if not self.available:
                return unavailable("PARSER_CLEANUP_UNCONFIRMED")
            if len(self.records) >= self.max_history:
                return unavailable("PARSER_STATE_CAPACITY_EXCEEDED")
            record = {"version": 1, "instance": self.instance, "request_key": key,
                      "fingerprint": fingerprint, "file_sha256": header["sha256"],
                      "container": "companion-file-" + uuid.uuid4().hex,
                      "phase": "STARTED", "result_sha256": None}
            try:
                _write(self.root / (key + ".json"), record, exclusive=True)
            except (OSError, ValueError):
                self.available = False
                return unavailable("PARSER_STATE_UNCONFIRMED")
            self.records[key] = record
        try:
            result = self._processor(data, filename, mime, image=self.image, limits=limits,
                                     workspace=self.root, docker=self.docker, docker_env=self.env,
                                     container_name=record["container"], owner_label=self.instance)
            _validate_response(result, data, filename, mime, limits)
        except Exception:
            result = unavailable("PARSER_PROCESSOR_FAILED")
        cleanup = self._cleanup(record["container"])
        if not cleanup:
            result = unavailable("PARSER_CLEANUP_UNCONFIRMED")
        with self._mutex:
            record = record | {"phase": "FINISHED" if cleanup else "UNKNOWN",
                               "result_sha256": hashlib.sha256(_json(result)).hexdigest()}
            try:
                _write(self.root / (key + ".json"), record)
                self.records[key] = record
            except (OSError, ValueError):
                cleanup = False
                result = unavailable("PARSER_STATE_UNCONFIRMED")
            self.available = self.available and cleanup
        return result

    def handle(self, connection):
        # Bound connections before reading bytes: slow clients cannot create unlimited workers.
        acquired = self._slots.acquire(blocking=False)
        try:
            with connection:
                if not acquired:
                    return
                header, data, limits = self._receive(connection)
                result = self._process(header, data, limits)
                connection.settimeout(self.read_timeout)
                _send_frame(connection, result, limits.max_result_bytes)
        except (OSError, EOFError, ValueError, TypeError, KeyError, AttributeError,
                RecursionError, file_parser._Failure):
            # A lost reader never interrupts the already consumed, bounded parser operation.
            pass
        finally:
            if acquired:
                self._slots.release()


def serve(host, socket_path, *, socket_mode=0o600, socket_group=None, stop=None):
    if sys.platform != "linux" or not hasattr(socket, "AF_UNIX"):
        raise RuntimeError("LINUX_UNIX_SOCKET_REQUIRED")
    path = Path(socket_path)
    if (not path.is_absolute() or len(str(path).encode()) > 107
        or socket_mode not in {0o600, 0o660}
        or socket_mode == 0o660 and (type(socket_group) is not int or socket_group < 0)):
        raise ValueError("INVALID_PARSER_SOCKET_CONFIG")
    path.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
    parent = path.parent.stat()
    if (path.parent.is_symlink() or parent.st_uid != os.geteuid()
        or stat.S_IMODE(parent.st_mode) & 0o022):
        raise ValueError("PRIVATE_PARSER_SOCKET_DIRECTORY_REQUIRED")
    if socket_group is not None:
        os.chown(path.parent, -1, socket_group)
        path.parent.chmod(0o710)
    if path.exists() or path.is_symlink():
        if path.is_symlink() or not stat.S_ISSOCK(path.stat().st_mode):
            raise ValueError("PARSER_SOCKET_PATH_OCCUPIED")
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as probe:
            probe.settimeout(1)
            try:
                probe.connect(str(path))
            except ConnectionRefusedError:
                path.unlink()
            else:
                raise ValueError("PARSER_SOCKET_ALREADY_RUNNING")
    stop, threads = stop or threading.Event(), []
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as listener:
        previous = os.umask(0o077)
        try:
            listener.bind(str(path))
        finally:
            os.umask(previous)
        identity = path.stat().st_ino
        path.chmod(socket_mode)
        if socket_group is not None:
            os.chown(path, -1, socket_group)
        listener.listen(8)
        listener.settimeout(0.5)
        try:
            while not stop.is_set():
                try:
                    connection, _ = listener.accept()
                except TimeoutError:
                    continue
                threads = [thread for thread in threads if thread.is_alive()]
                # The semaphore alone would still permit unbounded short-lived thread creation.
                if len(threads) >= 8:
                    connection.close()
                    continue
                thread = threading.Thread(target=host.handle, args=(connection,), daemon=True)
                threads.append(thread)
                thread.start()
        finally:
            if path.exists() and path.stat().st_ino == identity:
                path.unlink()
            deadline = time.monotonic() + host.limits.wall_seconds + 20
            for thread in threads:
                thread.join(max(0, deadline - time.monotonic()))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--socket", required=True, type=Path)
    parser.add_argument("--state-dir", required=True, type=Path)
    parser.add_argument("--image", required=True)
    parser.add_argument("--limits-json", default="{}")
    parser.add_argument("--docker", default="/usr/bin/docker")
    parser.add_argument("--docker-host", default="unix:///var/run/docker.sock")
    parser.add_argument("--max-concurrency", default=1, type=int)
    parser.add_argument("--read-timeout", default=15, type=int)
    parser.add_argument("--max-history", default=10000, type=int)
    parser.add_argument("--socket-mode", default=0o600, type=lambda value: int(value, 8))
    parser.add_argument("--socket-group", type=int)
    args = parser.parse_args(argv)
    if sys.platform != "linux":
        parser.error("LINUX_UNIX_SOCKET_REQUIRED")
    limits = file_parser.Limits(**json.loads(args.limits_json))
    stop = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    host = ParserHost(state_dir=args.state_dir, image=args.image, limits=limits, docker=args.docker,
                      docker_host=args.docker_host, max_concurrency=args.max_concurrency,
                      read_timeout=args.read_timeout, max_history=args.max_history)
    try:
        serve(host, args.socket, socket_mode=args.socket_mode, socket_group=args.socket_group,
              stop=stop)
    finally:
        host.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
