"""One request per private Unix socket; no Docker socket, credentials, or host parsing."""

import hashlib
import json
import re
import socket
import struct
from dataclasses import asdict
from pathlib import Path, PurePosixPath

from companion import file_parser

HEADER_BYTES = 8192
REQUEST_KEYS = {"version", "request_id", "image", "filename", "mime_type", "bytes", "sha256",
                "limits"}


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
                      allow_nan=False).encode("utf-8")


def _read_exact(connection, count):
    result = bytearray()
    while len(result) < count:
        chunk = connection.recv(min(count - len(result), 65536))
        if not chunk:
            raise EOFError("PARSER_SOCKET_EOF")
        result.extend(chunk)
    return bytes(result)


def _read_frame(connection, maximum):
    length = struct.unpack("!I", _read_exact(connection, 4))[0]
    if not 0 < length <= maximum:
        raise ValueError("PARSER_FRAME_LIMIT")
    payload = _read_exact(connection, length)
    value = json.loads(payload)
    if _json(value) != payload:
        raise ValueError("PARSER_NONCANONICAL_JSON")
    return value


def _send_frame(connection, value, maximum):
    payload = _json(value)
    if not 0 < len(payload) <= maximum:
        raise ValueError("PARSER_FRAME_LIMIT")
    connection.sendall(struct.pack("!I", len(payload)) + payload)


def _request(data, filename, mime_type, *, image, limits, request_id):
    if not isinstance(request_id, str) or not re.fullmatch(r"[a-z][a-z0-9_-]{0,127}", request_id):
        raise ValueError("INVALID_PARSER_REQUEST_ID")
    file_parser._identify(data, filename, mime_type, limits)
    if not isinstance(image, str) or not re.fullmatch(
        r"(?:[a-z0-9][a-z0-9./:_-]*@)?sha256:[0-9a-f]{64}", image,
    ):
        raise ValueError("PINNED_PARSER_IMAGE_REQUIRED")
    return {"version": 1, "request_id": request_id, "image": image, "filename": filename,
            "mime_type": mime_type, "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest(),
            "limits": asdict(limits)}


def _unavailable(data, filename, mime_type, code):
    return file_parser._result(data, filename, mime_type) | {
        "status": "UNAVAILABLE", "error_code": code,
    }


def _validate_response(result, data, filename, mime_type, limits):
    if not isinstance(result, dict) or result.get("isolation") not in {
        "not_asserted", "docker_network_none",
    }:
        raise ValueError("PARSER_PROTOCOL_INVALID")
    if result.get("status") in {"SUCCESS", "PARTIAL"} and (
        result["isolation"] != "docker_network_none"
    ):
        raise ValueError("PARSER_PROTOCOL_INVALID")
    file_parser._validate_result(result | {"isolation": "not_asserted"},
                                 file_parser._result(data, filename, mime_type), limits)


def parse_via_socket(data: bytes, filename: str, mime_type: str = "", *, socket_path: str | Path,
                     image: str, limits=file_parser.DEFAULT_LIMITS, request_id: str,
                     effect_check=None):
    """No reconnect: losing a response cannot establish whether a job ran or stopped."""
    try:
        header = _request(data, filename, mime_type, image=image, limits=limits,
                          request_id=request_id)
    except file_parser._Failure as error:
        return file_parser._result(data, filename, mime_type) | {"error_code": error.code}
    path = str(socket_path)
    if (not PurePosixPath(path).is_absolute() or str(PurePosixPath(path)) != path
        or path.startswith("//") or ".." in PurePosixPath(path).parts
        or re.search(r"[\x00-\x1f\x7f]", path) or len(path.encode()) > 107):
        raise ValueError("INVALID_PARSER_SOCKET_PATH")
    if not hasattr(socket, "AF_UNIX"):
        return _unavailable(data, filename, mime_type, "PARSER_SOCKET_UNAVAILABLE")
    connected = False
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
            connection.settimeout(limits.wall_seconds + 15)
            if effect_check:
                effect_check()
            connection.connect(path)
            connected = True
            if effect_check:
                effect_check()
            _send_frame(connection, header, HEADER_BYTES)
            if effect_check:
                effect_check()
            connection.sendall(data)
            # EOF delimits the request and lets the server reject trailing data before launch.
            connection.shutdown(socket.SHUT_WR)
            result = _read_frame(connection, limits.max_result_bytes)
            if connection.recv(1):
                raise ValueError("PARSER_TRAILING_RESPONSE")
            _validate_response(result, data, filename, mime_type, limits)
            return result
    except (OSError, EOFError):
        return _unavailable(data, filename, mime_type, "PARSER_CLEANUP_UNCONFIRMED" if connected
                            else "PARSER_SOCKET_UNAVAILABLE")
    except (ValueError, TypeError, KeyError, AttributeError, RecursionError):
        return _unavailable(data, filename, mime_type, "PARSER_PROTOCOL_INVALID")
