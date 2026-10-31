"""Actual Reader CLI with a mocked provider and real Core HTTP, in a separate process."""

import importlib.abc
import json
import os
import runpy
import sys
from datetime import datetime

import httpcore

from source_reader import bili_tools, bilibili, worker_v1


class Boundary(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.split(".")[0] in {"sqlite3", "fastapi", "pydantic", "alembic"} or (
            fullname.startswith("companion.") and fullname != "companion.source_policy"
        ):
            raise ImportError("FORBIDDEN_READER_DEPENDENCY:" + fullname)


def main():
    sys.meta_path.insert(0, Boundary())
    origin, grant, now, tools = sys.argv[1:]
    packet = json.loads(sys.stdin.read())
    responses = []
    for value in packet["responses"]:
        body = json.dumps(value).encode()
        responses.append(b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: "
                         + str(len(body)).encode() + b"\r\nConnection: close\r\n\r\n" + body)
    calls = []

    class Provider(httpcore.AsyncMockBackend):
        async def connect_tcp(self, *args, **kwargs):
            return httpcore.AsyncMockStream([responses.pop(0)])

    class Account(bili_tools.BilibiliAccountHTTP):
        def __init__(self, account_file):
            super().__init__(account_file, httpcore.AsyncConnectionPool(
                network_backend=Provider([]), max_keepalive_connections=0),
                clock=lambda: datetime.fromisoformat(now))

        async def get(self, url, budget, role):
            result = await super().get(url, budget, role)
            calls.append(url)
            return result

    class Reader(bilibili.BilibiliReader):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs, clock=lambda: now)

    class ToolReader(bili_tools.BilibiliToolReader):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.clock = lambda: now

    bili_tools.BilibiliAccountHTTP = Account
    bili_tools.BilibiliToolReader = ToolReader
    bilibili.BilibiliReader = Reader
    tick = worker_v1.tick

    async def frozen_tick(*args, **kwargs):
        return await tick(*args, **kwargs, clock=lambda: datetime.fromisoformat(now))

    worker_v1.tick = frozen_tick
    sys.argv = ["source_reader", "--source", "bilibili", "--protocol", "reading-v1",
                "--core-url", origin, "--adapter-id", "bili", "--account-file", grant, "--once"]
    if tools == "true":
        sys.argv += ["--bili-tools", "--search-keyword", "不会覆盖Core的推荐流"]
    runpy.run_module("source_reader", run_name="__main__")
    print(json.dumps({"calls": len(calls), "pid": os.getpid()}), flush=True)


if __name__ == "__main__":
    main()
