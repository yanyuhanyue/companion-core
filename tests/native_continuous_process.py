"""Actual native daemon; parent supplies real loopback peers and a stop signal."""

import asyncio
import json
import sqlite3
import sys
from pathlib import Path

from companion_onebot_bridge import app
from companion_onebot_bridge.config import BridgeConfig


async def main():
    raw = await asyncio.to_thread(Path(sys.argv[1]).read_text, encoding="utf-8")
    cfg = BridgeConfig(**json.loads(raw))
    if not all(url.startswith(prefix) for url, prefix in (
        (cfg.core_url, "http://127.0.0.1:"), (cfg.onebot_http_url, "http://127.0.0.1:"),
        (cfg.onebot_ws_url, "ws://127.0.0.1:"),
    )):
        raise ValueError("SYNTHETIC_LOOPBACK_REQUIRED")
    daemon = asyncio.create_task(app.run(cfg))
    stopped = asyncio.create_task(asyncio.to_thread(sys.stdin.readline))
    try:
        done, _ = await asyncio.wait([daemon, stopped], return_when=asyncio.FIRST_COMPLETED)
        if daemon in done:
            await daemon
        assert await stopped == "stop\n"
    finally:
        daemon.cancel()
        await asyncio.gather(daemon, return_exceptions=True)
    with sqlite3.connect(Path(cfg.data_dir) / "spool.sqlite3") as db:
        result = {"captures": db.execute("SELECT COUNT(*) FROM window_inputs").fetchone()[0],
                  "attempts": dict(db.execute(
                      "SELECT state,COUNT(*) FROM window_attempts GROUP BY state"))}
        print(json.dumps(result), flush=True)


if __name__ == "__main__":
    asyncio.run(main())
