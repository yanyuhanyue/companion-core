import argparse
import asyncio
import ipaddress
import json
import os
import re
from contextlib import AsyncExitStack
from urllib.parse import urlsplit

import httpx

from source_reader.bili_tools import BilibiliAccountHTTP, BilibiliToolReader
from source_reader.bilibili import BilibiliReader
from source_reader.browser import BrowserRenderer
from source_reader.heybox import HeyboxReader, SourceAPI
from source_reader.heybox_source import NativeHeyboxSource
from source_reader.network import PublicHTTP, ReadFailure
from source_reader.worker import read_result, tick


def core_address(value):
    url = urlsplit(value)
    if (
        url.scheme not in {"http", "https"}
        or not url.hostname
        or url.username
        or url.password
        or url.query
        or url.fragment
        or url.path not in {"", "/"}
    ):
        raise ValueError("Core needs a fixed HTTP(S) origin")
    if url.scheme == "http" and url.hostname != "localhost":
        address = ipaddress.ip_address(url.hostname)
        if not (address.is_private or address in ipaddress.ip_network("100.64.0.0/10")):
            raise ValueError("Plain HTTP Core must be private")
    return value.rstrip("/")


async def main(args):
    renderer = BrowserRenderer() if args.browser else None
    async with AsyncExitStack() as stack:
        public = await stack.enter_async_context(
            BilibiliAccountHTTP(args.account_file)
            if args.source == "bilibili" and args.account_file
            else PublicHTTP()
        )
        source = None
        if args.source == "heybox" and args.source_url:
            source_token = os.environ.get("COMPANION_HEYBOX_SOURCE_TOKEN", "")
            if source_token == os.environ.get("COMPANION_READER_TOKEN"):
                raise ValueError("Use different Core adapter and source service credentials")
            source = await stack.enter_async_context(SourceAPI(args.source_url, source_token))
        elif args.source == "heybox" and args.heybox_native:
            source = await stack.enter_async_context(NativeHeyboxSource(args.account_file))
        reader = (
            HeyboxReader(public, source)
            if args.source == "heybox"
            else BilibiliToolReader(public, args.search_keyword)
            if args.bili_tools
            else BilibiliReader(public, renderer)
        )
        if args.probe:
            result = await read_result(
                reader,
                {
                    "lease_token": "public_probe_only",
                    "operation": "read",
                    "url": args.probe,
                    "max_bytes": 524288,
                    "max_requests": 4,
                    "max_items": 1,
                },
            )
            # Public probes record evidence/quality status, not a stored transcript.
            for item in result["items"]:
                item["text_chars"] = len(item.pop("text"))
            print(json.dumps(result, ensure_ascii=False))
            return
        token = os.environ.get("COMPANION_READER_TOKEN", "")
        if len(token) < 24 or not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", args.adapter_id or ""):
            raise ValueError("Set a distinct source adapter ID and COMPANION_READER_TOKEN")
        async with httpx.AsyncClient(
            base_url=core_address(args.core_url),
            trust_env=False,
            follow_redirects=False,
            headers={"Authorization": "Bearer " + token},
            timeout=10,
        ) as core:
            while True:
                try:
                    worked = await tick(core, reader, args.adapter_id)
                except (httpx.HTTPError, ValueError, KeyError, ReadFailure):
                    worked = False
                    print("Reader connection unavailable; lease state retained", flush=True)
                if args.once:
                    return
                if not worked:
                    await asyncio.sleep(5)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Bounded Bilibili / Heybox source reader")
    parser.add_argument("--source", choices=["bilibili", "heybox"], default="bilibili")
    parser.add_argument("--source-url", help="Explicit two-GET Heybox service origin")
    parser.add_argument("--heybox-native", action="store_true", help="Enable pinned two-GET source")
    parser.add_argument("--bili-tools", action="store_true", help="Enable bounded Bilibili tools")
    parser.add_argument("--search-keyword", help="Operator-selected Bilibili discovery keyword")
    parser.add_argument("--account-file", help="Explicit expiring read-only account grant file")
    parser.add_argument("--core-url", default="http://127.0.0.1:8765")
    parser.add_argument("--adapter-id")
    parser.add_argument("--probe", help="Read one source item without connecting to Core")
    parser.add_argument("--browser", action="store_true", help="Enable bounded offline rendering")
    parser.add_argument("--once", action="store_true")
    try:
        args = parser.parse_args()
        if args.source_url and args.source != "heybox":
            parser.error("--source-url requires --source heybox")
        if args.browser and args.source != "bilibili":
            parser.error("--browser is the Bilibili offline renderer")
        if args.heybox_native and (args.source != "heybox" or args.source_url):
            parser.error("--heybox-native requires Heybox without --source-url")
        if args.bili_tools and (args.source != "bilibili" or args.browser):
            parser.error("--bili-tools requires Bilibili API mode")
        if args.account_file and args.source != "bilibili" and not args.heybox_native:
            parser.error("--account-file requires Bilibili or the native Heybox reader")
        if args.search_keyword and not args.bili_tools:
            parser.error("--search-keyword requires --bili-tools")
        asyncio.run(main(args))
    except KeyboardInterrupt:
        pass
