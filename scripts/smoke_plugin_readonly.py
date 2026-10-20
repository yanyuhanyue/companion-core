"""Offline production-Reader smoke for a no-network, non-root, read-only container."""

import asyncio
import hashlib
import json
import os
from pathlib import Path

import httpcore

from source_reader.bili_tools import BilibiliToolReader, BilibiliTools
from source_reader.heybox import HeyboxReader
from source_reader.heybox_source import NativeHeyboxSource
from source_reader.network import PublicHTTP, PublicNetwork, ReadBudget, ReadFailure
from source_reader.worker import read_result


class FixtureStream(httpcore.AsyncMockStream):
    def __init__(self, packet, requests):
        super().__init__([packet])
        self.requests = requests

    async def write(self, buffer, **kwargs):
        if buffer:
            self.requests.append(buffer)


class FixtureBackend(httpcore.AsyncNetworkBackend):
    """Only in-memory HTTP bytes; this backend never opens a socket."""

    def __init__(self, packet):
        self.packet, self.requests = packet, []

    async def connect_tcp(self, host, port, **kwargs):
        assert (host, port) == ("1.1.1.1", 443)
        return FixtureStream(self.packet, self.requests)


async def successful_fixture_read(item):
    body = json.dumps({"code": 0, "data": {
        "title": "Synthetic public metadata",
        "desc": "Synthetic fixture description.",
        "cid": 1234,
    }}).encode()
    backend = FixtureBackend(
        f"HTTP/1.1 200 OK\r\nContent-Length: {len(body)}\r\nConnection: close\r\n\r\n".encode()
        + body
    )

    async def resolve(host, port):
        assert (host, port) == ("api.bilibili.com", 443)
        return ["1.1.1.1"]

    pool = httpcore.AsyncConnectionPool(
        network_backend=PublicNetwork(resolve, backend), max_keepalive_connections=0,
    )
    async with PublicHTTP(pool) as public:
        target = {**item, "url": "https://www.bilibili.com/video/BV1xx411c7mD"}
        result = await read_result(BilibiliToolReader(public), target)
        assert result["status"] == "SUCCESS" and result["used_requests"] == 1
        assert result["used_bytes"] == len(body) and len(result["items"]) == 1
        reading = result["items"][0]["reading"]
        assert result["items"][0]["title"] == "Synthetic public metadata"
        assert result["items"][0]["url"] == target["url"]
        assert "Synthetic fixture description." in result["items"][0]["text"]
        assert reading["level"] == "L1" and reading["subtitle_status"] == "budget_limited"
        assert reading["sources"] == [{
            "role": "metadata", "url": "https://api.bilibili.com/x/web-interface/view",
            "sha256": hashlib.sha256(body).hexdigest(), "bytes": len(body),
        }]
        assert len(backend.requests) == 1
        assert backend.requests[0].startswith(b"GET /x/web-interface/view?bvid=BV1xx411c7mD ")
        assert b"host: api.bilibili.com\r\n" in backend.requests[0].lower()
        assert b"cookie:" not in backend.requests[0].lower()
        assert b"authorization:" not in backend.requests[0].lower()

        budget = ReadBudget(4096, 1)
        for url, code in (
            ("https://127.0.0.1/private", "CONTENT_URL_NOT_ALLOWED"),
            ("https://api.bilibili.com/x/web-interface/view", "READER_REQUEST_LIMIT"),
        ):
            if code == "READER_REQUEST_LIMIT":
                budget.used_requests = 1
            try:
                await public.get(url, budget, "metadata")
            except ReadFailure as error:
                assert str(error) == code
            else:
                raise AssertionError("Restricted request reached the fixture backend")
            assert len(backend.requests) == 1

        rejected = await read_result(BilibiliToolReader(public), {**target, "max_bytes": 16})
        assert rejected["status"] == "FAILED" and rejected["items"] == []
        assert rejected["error_code"] == "READER_BYTE_LIMIT"
        assert rejected["used_requests"] == 1 and rejected["used_bytes"] == 0
        assert len(backend.requests) == 2
    return {"fixture_reads": 1, "fixture_requests": 2, "restricted_rejections": 3}


async def main():
    async with PublicHTTP() as public, NativeHeyboxSource() as source:
        tools = BilibiliTools(public)
        for name in ("bili_reply", "bili_send_message", "bili_upload", "bili_login"):
            try:
                await tools.call(name, {"force": True}, ReadBudget(4096, 1))
            except ReadFailure as error:
                assert str(error) == "CAPABILITY_NOT_ALLOWED"
            else:
                raise AssertionError("Unexpected write/login capability")
        item = {
            "lease_token": "offline",
            "operation": "read",
            "max_bytes": 4096,
            "max_requests": 1,
            "max_items": 1,
            "url": "https://www.xiaoheihe.cn/app/bbs/link/168860244",
        }
        result = await read_result(HeyboxReader(None, source), item)
        assert result["error_code"] == "OWNER_ACCOUNT_AUTHORIZATION_REQUIRED"
        assert result["used_requests"] == 0
        result = await read_result(BilibiliToolReader(public), {**item, "operation": "discover"})
        assert result["error_code"] == "DISCOVERY_QUERY_REQUIRED"
        assert result["used_requests"] == 0
    fixture = await successful_fixture_read(item)
    return {
        "status": "PASS",
        "tools": tools.list_tools(),
        "upstream_requests": 0,
        **fixture,
        "uid": os.getuid() if hasattr(os, "getuid") else None,
    }


if __name__ == "__main__":
    report = asyncio.run(main())
    for name in ("memory.peak", "pids.peak"):
        path = Path("/sys/fs/cgroup") / name
        report[name] = path.read_text().strip() if path.is_file() else None
    print(json.dumps(report))
