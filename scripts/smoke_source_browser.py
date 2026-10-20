"""Explicit offline Chromium sandbox smoke; run inside the bounded Reader container."""

import asyncio
import json
import time
from pathlib import Path

from source_reader.browser import BrowserRenderer
from source_reader.network import ReadFailure


def cgroup_value(name):
    path = Path("/sys/fs/cgroup") / name
    return path.read_text().strip() if path.is_file() else None


async def main():
    started = time.monotonic()
    report = {"sandbox_requested": True, "network_requirement": "container --network none"}
    renderer = BrowserRenderer(timeout=5)
    url = "https://www.bilibili.com/video/BV1GJ411x7h7"
    try:
        result = await renderer.metadata(
            url,
            b"<script>window.__INITIAL_STATE__={videoData:{title:'sandbox',desc:'offline'}}</script>",
        )
        assert result["title"] == "sandbox" and result["desc"] == "offline"
        renderer.timeout = 1
        try:
            await renderer.metadata(url, b"<script>while(true){}</script>")
            raise AssertionError("Unbounded page unexpectedly completed")
        except ReadFailure as error:
            assert str(error) == "READER_BROWSER_TIMEOUT"
        renderer.timeout = 5
        result = await renderer.metadata(url, b'<meta property="og:title" content="healthy">')
        assert result["title"] == "healthy" and renderer.active_contexts == 0
        report["status"] = "PASS"
    except (ReadFailure, AssertionError) as error:
        report.update(
            status="BLOCKED",
            error_code=str(error),
            diagnostic=str(error.__cause__ or error)[:3000],
        )
    report.update(
        elapsed_seconds=round(time.monotonic() - started, 3),
        active_contexts=renderer.active_contexts,
        container_memory_peak_bytes=cgroup_value("memory.peak"),
        container_memory_limit_bytes=cgroup_value("memory.max"),
        container_pids_peak=cgroup_value("pids.peak"),
    )
    print(json.dumps(report, ensure_ascii=False), flush=True)
    return report["status"] == "PASS"


if __name__ == "__main__":
    raise SystemExit(0 if asyncio.run(main()) else 2)
