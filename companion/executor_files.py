"""Open a local file through held, non-reparse directory handles."""

import os
import stat
from contextlib import ExitStack, contextmanager
from pathlib import Path


@contextmanager
def open_scoped(path, create=False):
    path = Path(path)
    if not path.is_absolute():
        raise ValueError("ABSOLUTE_PATH_REQUIRED")
    if os.name == "nt":
        with _windows_file(path, create) as stream:
            yield stream
    else:
        with ExitStack() as stack:
            directory = os.open(path.anchor, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            stack.callback(os.close, directory)
            for part in path.parts[1:-1]:
                directory = os.open(
                    part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory
                )
                stack.callback(os.close, directory)
            flags = os.O_RDWR | os.O_CREAT | os.O_EXCL if create else os.O_RDONLY
            descriptor = os.open(path.name, flags | os.O_NOFOLLOW, 0o600, dir_fd=directory)
            stream = stack.enter_context(os.fdopen(descriptor, "r+b" if create else "rb"))
            if not create:
                _regular_file(stream)
            yield stream


def _regular_file(stream):
    info = os.fstat(stream.fileno())
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        raise ValueError("LINK_NOT_ALLOWED")


@contextmanager
def _windows_file(path, create):
    import ctypes
    import msvcrt
    from ctypes import wintypes

    if len(path.drive) != 2 or path.drive[1] != ":":
        raise ValueError("LOCAL_DRIVE_REQUIRED")
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    open_file = kernel.CreateFileW
    open_file.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        ctypes.c_void_p,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.HANDLE,
    ]
    open_file.restype = wintypes.HANDLE
    close = kernel.CloseHandle
    close.argtypes, close.restype = [wintypes.HANDLE], wintypes.BOOL

    class FileInfo(ctypes.Structure):
        _fields_ = [
            ("attributes", wintypes.DWORD),
            ("created", wintypes.FILETIME),
            ("accessed", wintypes.FILETIME),
            ("written", wintypes.FILETIME),
            ("volume", wintypes.DWORD),
            ("size_high", wintypes.DWORD),
            ("size_low", wintypes.DWORD),
            ("links", wintypes.DWORD),
            ("index_high", wintypes.DWORD),
            ("index_low", wintypes.DWORD),
        ]

    get_info = kernel.GetFileInformationByHandle
    get_info.argtypes = [wintypes.HANDLE, ctypes.POINTER(FileInfo)]
    get_info.restype = wintypes.BOOL

    def acquire(target, directory=False):
        # Share only reads: directory rename/reparse changes and target writes are excluded.
        access = 0x80 if directory else (0xC0000000 if create else 0x80000000)
        disposition = 1 if create and not directory else 3  # CREATE_NEW / OPEN_EXISTING
        flags = 0x00200000 | (0x02000000 if directory else 0x80)
        handle = open_file(str(target), access, 1, None, disposition, flags, None)
        if handle == ctypes.c_void_p(-1).value:
            raise ctypes.WinError(ctypes.get_last_error())
        return handle

    def validate(handle, directory):
        info = FileInfo()
        if not get_info(handle, ctypes.byref(info)):
            raise ctypes.WinError(ctypes.get_last_error())
        if info.attributes & 0x400 or bool(info.attributes & 0x10) != directory:
            raise ValueError("LINK_NOT_ALLOWED")
        if not directory and info.links != 1:
            raise ValueError("LINK_NOT_ALLOWED")

    with ExitStack() as stack:
        # Hold every ancestor before opening its child; no validated component can be replaced.
        for directory in (*reversed(path.parent.parents), path.parent):
            handle = acquire(directory, directory=True)
            stack.callback(close, handle)
            validate(handle, directory=True)
        handle = acquire(path)
        try:
            if not create:
                validate(handle, directory=False)
            descriptor = msvcrt.open_osfhandle(
                handle, os.O_BINARY | os.O_NOINHERIT | (os.O_RDWR if create else os.O_RDONLY)
            )
        except BaseException:
            close(handle)
            raise
        # open_osfhandle transfers ownership; the fd closes the original Win32 handle.
        stream = stack.enter_context(os.fdopen(descriptor, "r+b" if create else "rb"))
        yield stream
