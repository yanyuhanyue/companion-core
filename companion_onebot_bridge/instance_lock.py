"""The same OS-held exclusive lock for native operation and transport retirement."""

import os
import stat
from pathlib import Path


def acquire(path, *, existing_only=False):
    path = Path(path)
    if path.is_symlink():
        raise ValueError("SYMLINK_REFUSED")
    if existing_only:
        info = path.lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_size < 1:
            raise ValueError("EXISTING_NONEMPTY_LOCK_REQUIRED")
    lock = path.open("r+b" if existing_only else "a+b")
    try:
        if existing_only and path.stat().st_size < 1:
            raise ValueError("EXISTING_NONEMPTY_LOCK_REQUIRED")
        if os.name != "nt" and not existing_only:
            os.chmod(path, 0o600)
        if not existing_only and path.stat().st_size == 0:
            lock.write(b"0")
            lock.flush()
        lock.seek(0)
        if os.name == "nt":
            import msvcrt

            msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        if existing_only:
            opened, current = os.fstat(lock.fileno()), path.stat()
            if (not stat.S_ISREG(opened.st_mode) or opened.st_size < 1
                or (opened.st_dev, opened.st_ino) != (info.st_dev, info.st_ino)
                or (opened.st_dev, opened.st_ino) != (current.st_dev, current.st_ino)):
                raise ValueError("EXISTING_NONEMPTY_LOCK_REQUIRED")
        return lock
    except BaseException:
        lock.close()
        raise
