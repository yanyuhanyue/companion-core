"""Real Reading worker and account-source grant with a synthetic public transport."""

import asyncio
import json
import sys
from datetime import datetime
from pathlib import Path

import httpcore
import httpx

from source_reader import worker_v1
from source_reader.heybox import HeyboxReader
from source_reader.heybox_source import NativeHeyboxSource


async def main(value):
    raw = bytes.fromhex(value["body"])
    wire = (b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: "
            + str(len(raw)).encode() + b"\r\nConnection: close\r\n\r\n" + raw)
    pool = httpcore.AsyncConnectionPool(network_backend=httpcore.AsyncMockBackend([wire]))
    def clock():
        return datetime.fromisoformat(value["now"])
    async with NativeHeyboxSource(value["account"], pool, clock) as source, httpx.AsyncClient(
        base_url=value["base"], trust_env=False,
        headers={"Authorization": "Bearer " + value["token"]},
    ) as client:
        reader = HeyboxReader(None, source=source, clock=lambda: value["now"])
        assert await worker_v1.tick(client, reader, "heybox", source="heybox", clock=clock)


if __name__ == "__main__":
    asyncio.run(main(json.loads(Path(sys.argv[1]).read_text("utf-8"))))
