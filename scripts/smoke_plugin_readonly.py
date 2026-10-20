"""Offline production-Reader smoke for a no-network, non-root, read-only container."""

import asyncio
import json
import os
from pathlib import Path

from source_reader.bili_tools import BilibiliToolReader, BilibiliTools
from source_reader.heybox import HeyboxReader
from source_reader.heybox_source import NativeHeyboxSource
from source_reader.network import PublicHTTP, ReadBudget, ReadFailure
from source_reader.worker import read_result


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
    return {
        "status": "PASS",
        "tools": tools.list_tools(),
        "upstream_requests": 0,
        "uid": os.getuid() if hasattr(os, "getuid") else None,
    }


if __name__ == "__main__":
    report = asyncio.run(main())
    for name in ("memory.peak", "pids.peak"):
        path = Path("/sys/fs/cgroup") / name
        report[name] = path.read_text().strip() if path.is_file() else None
    print(json.dumps(report))
