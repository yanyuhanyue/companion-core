"""Process-wide graceful stop for native workers.

SIGTERM/SIGINT only set a flag. Worker loops check it between units: the unit already
running (model call, send, commit) completes, nothing new is claimed, and the process
exits 0 after releasing its lock. Without a handler, a Python PID 1 in a container
ignores SIGTERM until the runtime's SIGKILL, which strands in-flight model attempts.
"""

import signal
import threading

_requested = threading.Event()


def requested() -> bool:
    return _requested.is_set()


def request(signum=None, _frame=None):
    """Signal handler; also callable directly. A second SIGINT keeps Python's default."""
    if signum == getattr(signal, "SIGINT", None) and _requested.is_set():
        raise KeyboardInterrupt
    _requested.set()


def install():
    """Install from the main thread before asyncio.run, which then keeps these handlers."""
    for name in ("SIGTERM", "SIGINT"):
        if hasattr(signal, name):
            signal.signal(getattr(signal, name), request)


def reset():
    """Tests only: the flag is process-wide and never cleared in production."""
    _requested.clear()


class StopEvent:
    """`is_set()` view combining a local stop event with the process-wide request."""

    def __init__(self, local: threading.Event):
        self.local = local

    def is_set(self) -> bool:
        return self.local.is_set() or _requested.is_set()
