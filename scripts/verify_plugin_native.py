"""Reproduce pinned upstream clients and real MCP tools/list without external I/O.

Use the separate hash-locked native-probe environment documented in plugin-compat-p3.md.
The SDK is imported only here, never by Core or production Readers.
"""

import argparse
import asyncio
import hashlib
import importlib.metadata
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HEYBOX = "90d3c9d4d051f912d80ba02b38d5f8f0a5a95aac"
BILI = "9a3e0e67d82feedf755c763807c806c4d125a245"
ALLOWLIST = ["bili_search", "bili_subtitle", "bili_comments", "bili_video_info"]


def verify_checkout(path, expected):
    head = subprocess.check_output(
        ["git", "-C", str(path), "rev-parse", "HEAD"], text=True, timeout=10
    ).strip()
    if (
        head != expected
        or subprocess.run(
            ["git", "-C", str(path), "diff", "--quiet", "HEAD"], timeout=10
        ).returncode
    ):
        raise ValueError("PLUGIN_UNAVAILABLE")


async def verify(heybox, bili):
    import httpx
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    verify_checkout(heybox, HEYBOX)
    verify_checkout(bili, BILI)
    sys.path.insert(0, str(heybox))
    from xiaoheihe.api_client import CredentialInvalidError, XiaoheiheApiClient
    from xiaoheihe.models import Credentials

    calls = []

    def handler(request):
        assert request.method == "GET"
        calls.append({"method": request.method, "path": request.url.path})
        fixture = {
            "/bbs/app/feeds": "feed_recommendation.json",
            "/bbs/app/link/tree": "thread_context.json",
        }[request.url.path]
        return httpx.Response(
            200, json=json.loads((heybox / "tests/fixtures" / fixture).read_text(encoding="utf-8"))
        )

    async with httpx.AsyncClient(
        base_url="https://api.xiaoheihe.cn",
        transport=httpx.MockTransport(handler),
        trust_env=False,
    ) as client:
        source = XiaoheiheApiClient(
            "p3-offline", client=client, max_retries=0, min_request_interval_seconds=0
        )
        try:
            await source.fetch_feed()
        except CredentialInvalidError:
            assert not calls
        else:
            raise AssertionError("Missing upstream account guard")
        source.credentials = Credentials("p3-offline", "123", "fixture", {"pkey": "mock-only"})
        feed = await source.fetch_feed()
        post = await source.fetch_thread_context("30003")
        assert feed.items and post.body

    # Use a dedicated child Home without a DSH profile or credentials. Native MCP can only
    # register these four tools; its separate bootstrap denies all socket operations.
    env = {
        key: os.environ[key]
        for key in ("SYSTEMROOT", "WINDIR", "PATH", "TEMP", "TMP")
        if key in os.environ
    }
    env.update(
        BILI_MCP_TOOLS=",".join(ALLOWLIST), PYTHONDONTWRITEBYTECODE="1", PYTHONIOENCODING="utf-8"
    )
    # MCP merges its default HOME/profile variables into env. Override those defaults
    # explicitly rather than assuming a small env dict removes them.
    probe_home = ROOT / ".local/p3/native-home"
    for name in ("tmp", "cache", "appdata", "localappdata", "dsh"):
        (probe_home / name).mkdir(parents=True, exist_ok=True)
    env.update(
        HOME=str(probe_home),
        USERPROFILE=str(probe_home),
        DSH_HOME=str(probe_home / "dsh"),
        APPDATA=str(probe_home / "appdata"),
        LOCALAPPDATA=str(probe_home / "localappdata"),
        XDG_CACHE_HOME=str(probe_home / "cache"),
        TEMP=str(probe_home / "tmp"),
        TMP=str(probe_home / "tmp"),
        PYTHONNOUSERSITE="1",
    )
    if os.name == "nt":
        env["HOMEDRIVE"], env["HOMEPATH"] = os.path.splitdrive(str(probe_home))
    server_file = bili / "vendor/bilibili-mcp/mcp_server.py"
    if (server_file.parent / "bili_credential.json").exists():
        raise ValueError("NATIVE_PROBE_MUST_HAVE_NO_ACCOUNT_FILE")
    server = StdioServerParameters(
        command=sys.executable,
        args=["-B", str(ROOT / "scripts/plugin_probe_child.py"), str(server_file)],
        env=env,
        cwd=str(bili),
    )
    async with asyncio.timeout(30), stdio_client(server) as streams:
        async with ClientSession(*streams) as session:
            await session.initialize()
            names = sorted(tool.name for tool in (await session.list_tools()).tools)
            assert names == sorted(ALLOWLIST)
            denied = await session.call_tool(
                "bili_reply", {"bvid": "BV1Sb41127UZ", "force": True, "text": "fixture"}
            )
            assert denied.isError
            no_account = await session.call_tool("bili_subtitle", {"bvid": "BV1Sb41127UZ"})
            assert no_account.isError
    return {
        "heybox_commit": HEYBOX,
        "bilibili_commit": BILI,
        "native_external_requests": 0,
        "native_mcp_tools_list": names,
        "hidden_write_call_rejected": True,
        "account_absence_rejected": True,
        "mock_heybox_gets": calls,
        "mock_feed_items": len(feed.items),
        "mock_post_text_chars": len(post.body),
        "server_sha256": hashlib.sha256(server_file.read_bytes()).hexdigest(),
        "packages": {
            name: importlib.metadata.version(name)
            for name in ("mcp", "bilibili-api-python", "httpx", "aiosqlite")
        },
        "full_astrbot_or_dsh_agent_started": False,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--upstream-root", type=Path, required=True)
    args = parser.parse_args()
    root = args.upstream_root.resolve()
    print(json.dumps(asyncio.run(verify(root / "heybox", root / "bili")), indent=2))
