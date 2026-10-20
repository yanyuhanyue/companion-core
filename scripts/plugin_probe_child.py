"""Offline native MCP reproduction only, not a production plugin launcher."""

import asyncio
import atexit
import os
import runpy
import sys

ALLOWED = {"bili_search", "bili_subtitle", "bili_comments", "bili_video_info"}


def deny_network(event, arguments):
    if event in {"socket.connect", "socket.getaddrinfo", "socket.sendto"}:
        raise PermissionError("OFFLINE_NATIVE_PROBE")


if __name__ == "__main__":
    if set(os.environ.get("BILI_MCP_TOOLS", "").split(",")) != ALLOWED:
        raise SystemExit("NONEMPTY_EXACT_ALLOWLIST_REQUIRED")
    # Windows creates a private socketpair for its event loop. Construct it before
    # importing upstream code, then forbid every network connection from that code.
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    def close_loop():
        pending = asyncio.all_tasks(loop)
        if pending:
            loop.run_until_complete(
                asyncio.wait_for(asyncio.gather(*pending, return_exceptions=True), 2)
            )
        loop.close()

    atexit.register(close_loop)
    sys.addaudithook(deny_network)
    module = runpy.run_path(sys.argv[1], run_name="offline_native_probe")
    loop.run_until_complete(module["mcp"].run_stdio_async())
