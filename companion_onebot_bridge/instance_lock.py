"""The same OS-held exclusive lock for native operation and transport retirement."""

import os
from pathlib import Path


def acquire(path):
    path = Path(path)
    if path.is_symlink():
        raise ValueError("SYMLINK_REFUSED")
    lock = path.open("a+b")
    try:
        if os.name != "nt":
            os.chmod(path, 0o600)
        if path.stat().st_size == 0:
            lock.write(b"0")
            lock.flush()
        lock.seek(0)
        if os.name == "nt":
            import msvcrt

            msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        return lock
    except BaseException:
        lock.close()
        raise
