"""The Bridge's original fsynced hash-chain codec, shared by custody versions."""

import hashlib
import os
import stat

from .event_normalizer import decode
from .spool import encode


def digest(value):
    return hashlib.sha256(encode(value).encode()).hexdigest()


def stop_marker(path):
    """Publish the original durable OWNER_STOP marker without taking the process lock."""
    if path.parent.is_symlink() or not path.parent.is_dir():
        raise ValueError("PRIVATE_DATA_DIRECTORY_REQUIRED")
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL
                              | getattr(os, "O_NOFOLLOW", 0), 0o600)
    except FileExistsError:
        if path.is_symlink() or path.read_bytes() != b"OWNER_STOP\n":
            raise ValueError("INVALID_WINDOW_STOP_REQUEST") from None
        return
    with os.fdopen(descriptor, "wb") as handle:
        handle.write(b"OWNER_STOP\n")
        handle.flush()
        os.fsync(handle.fileno())
    if os.name != "nt":
        descriptor = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)


def read(path, *, maximum=32 * 1048576):
    info = path.lstat()
    if (path.is_symlink() or not stat.S_ISREG(info.st_mode) or info.st_size > maximum
            or (os.name != "nt" and info.st_mode & 0o077)):
        raise ValueError("INVALID_WINDOW_JOURNAL")
    records, head = [], "0" * 64
    for line in path.read_text("utf-8").splitlines():
        record = decode(line, 65536)
        if record.get("previous") != head or record.get("seq") != len(records) + 1:
            raise ValueError("INVALID_WINDOW_JOURNAL")
        head = digest(record)
        records.append(record)
    return records, head


def append(path, kind, data, *, at, expected_head, create=False, maximum=32 * 1048576):
    records, previous = ([], "0" * 64) if create else read(path, maximum=maximum)
    if previous != expected_head:
        raise ValueError("WINDOW_JOURNAL_RECONCILIATION_REQUIRED")
    record = {"seq": len(records) + 1, "previous": previous, "at": at,
              "kind": kind, "data": data}
    flags = os.O_WRONLY | (os.O_CREAT | os.O_EXCL if create else os.O_APPEND)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags, 0o600)
    with os.fdopen(descriptor, "ab") as handle:
        handle.write((encode(record) + "\n").encode())
        handle.flush()
        os.fsync(handle.fileno())
    if create and os.name != "nt":
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    return digest(record)
