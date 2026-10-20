"""Bounded equivalents of four audited dsh-bili-scroll tools; no Agent or MCP writes.

Search/result conventions follow dsh-bili-scroll@9a3e0e67 (MIT). Subtitle
processing deliberately reuses the existing Reader's raw hashes and time spans.
"""

import asyncio
import html
import json
import re
from datetime import UTC, datetime
from urllib.parse import urlencode, urlsplit

from source_reader.accounts import account_grant, source_grant
from source_reader.bilibili import BilibiliReader, api_data
from source_reader.network import PublicHTTP, ReadFailure, video_url

TOOLS = ("bili_search", "bili_subtitle", "bili_comments", "bili_video_info")


class BilibiliAccountHTTP(PublicHTTP):
    reading_method = "account_api"

    def __init__(self, account_file, pool=None, clock=lambda: datetime.now(UTC)):
        super().__init__(pool)
        self.account_file, self.clock, self.blocked_grant = account_file, clock, None

    def verify(self, budget):
        grant, fingerprint = account_grant(self.account_file, self.clock(), "bilibili")
        if self.blocked_grant == fingerprint:
            raise ReadFailure("SOURCE_DISABLED")
        if budget.authorization is not None and budget.authorization != fingerprint:
            raise ReadFailure("SOURCE_CREDENTIAL_REVOKED")
        budget.authorization = fingerprint
        return grant

    def grant_snapshot(self, required=False):
        return source_grant(self.account_file, self.clock(), "bilibili", required)

    async def get(self, url, budget, role):
        grant = self.verify(budget)
        # CDN subtitles and page fallbacks never receive account cookies.
        headers = {}
        if urlsplit(url).hostname == "api.bilibili.com":
            headers["Cookie"] = "; ".join(f"{k}={v}" for k, v in grant["cookies"].items())
            headers["Referer"] = "https://www.bilibili.com/"
        try:
            raw = await super().get(url, budget, role, headers=headers)
        except ReadFailure as error:
            if str(error) == "UPSTREAM_ACCESS_LIMIT":
                self.blocked_grant = budget.authorization
            raise
        self.verify(budget)
        if urlsplit(url).hostname == "api.bilibili.com":
            code = json.loads(raw).get("code")
            if code in {-101, -111}:
                self.blocked_grant = budget.authorization
                raise ReadFailure("SOURCE_CREDENTIAL_REVOKED")
            if code in {-352, -412, -403, -10403}:
                self.blocked_grant = budget.authorization
                raise ReadFailure("UPSTREAM_ACCESS_LIMIT")
        return raw


class BilibiliTools:
    """Static call surface. Unknown tools/parameters fail before network I/O."""

    def __init__(self, public):
        self.public = public

    def list_tools(self):
        return list(TOOLS)

    async def call(self, name, arguments, budget):
        if name not in TOOLS:
            raise ReadFailure("CAPABILITY_NOT_ALLOWED")
        allowed = (
            {"keyword", "num"}
            if name == "bili_search"
            else ({"bvid", "num"} if name == "bili_comments" else {"bvid"})
        )
        if not isinstance(arguments, dict) or set(arguments) - allowed:
            raise ReadFailure("CAPABILITY_ARGUMENT_INVALID")
        if not 0 < budget.max_requests <= 8 or not 0 < budget.max_bytes <= 1048576:
            raise ReadFailure("CAPABILITY_BUDGET_INVALID")
        async with asyncio.timeout(20):
            if name == "bili_search":
                result = await self.search(arguments, budget)
            elif name == "bili_comments":
                result = await self.comments(arguments, budget)
            else:
                item = self.video_item(arguments)
                result = await BilibiliReader(self.public, subtitles=name == "bili_subtitle").read(
                    item, budget
                )
                if name == "bili_subtitle" and result[0]["reading"]["level"] != "L2":
                    raise ReadFailure("NO_TRANSCRIPT")
            if isinstance(self.public, BilibiliAccountHTTP):
                self.public.verify(budget)
            return result

    @staticmethod
    def video_item(arguments):
        bvid = arguments.get("bvid")
        if not isinstance(bvid, str) or not re.fullmatch(r"BV[0-9A-Za-z]{10}", bvid):
            raise ReadFailure("CAPABILITY_ARGUMENT_INVALID")
        return {"operation": "read", "url": "https://www.bilibili.com/video/" + bvid}

    @staticmethod
    def count(arguments):
        count = arguments.get("num", 10)
        if type(count) is not int or not 1 <= count <= 20:
            raise ReadFailure("CAPABILITY_ARGUMENT_INVALID")
        return count

    async def search(self, arguments, budget):
        keyword, count = arguments.get("keyword"), self.count(arguments)
        if not isinstance(keyword, str) or not keyword.strip() or len(keyword) > 100:
            raise ReadFailure("CAPABILITY_ARGUMENT_INVALID")
        data = api_data(
            await self.public.get(
                "https://api.bilibili.com/x/web-interface/search/type?"
                + urlencode(
                    {"search_type": "video", "keyword": keyword, "page": 1, "order": "totalrank"}
                ),
                budget,
                "search",
            )
        )
        rows = data.get("result")
        if not isinstance(rows, list):
            raise ReadFailure("UPSTREAM_RESPONSE_INVALID")
        found = {}
        for row in rows[:40]:
            if not isinstance(row, dict):
                raise ReadFailure("UPSTREAM_RESPONSE_INVALID")
            bvid, title = row.get("bvid"), row.get("title")
            if (
                row.get("type", "video") != "video"
                or not isinstance(bvid, str)
                or not re.fullmatch(r"BV[0-9A-Za-z]{10}", bvid)
                or not isinstance(title, str)
                or not title.strip()
            ):
                continue
            url = video_url("https://www.bilibili.com/video/" + bvid)
            title = html.unescape(re.sub(r"<[^>]*>", "", title)).strip()[:300]
            if title:
                found.setdefault(url, {"url": url, "title": title, "text": ""})
            if len(found) == count:
                break
        return list(found.values())

    async def comments(self, arguments, budget):
        item, count = self.video_item(arguments), self.count(arguments)
        bvid = item["url"].rsplit("/", 1)[1]
        data = api_data(
            await self.public.get(
                "https://api.bilibili.com/x/web-interface/view?" + urlencode({"bvid": bvid}),
                budget,
                "metadata",
            )
        )
        aid = data.get("aid")
        if type(aid) is not int or aid <= 0:
            raise ReadFailure("UPSTREAM_RESPONSE_INVALID")
        data = api_data(
            await self.public.get(
                "https://api.bilibili.com/x/v2/reply?"
                + urlencode({"type": 1, "oid": aid, "pn": 1, "ps": count, "sort": 2}),
                budget,
                "comments",
            )
        )
        rows = data.get("replies") or []
        if not isinstance(rows, list):
            raise ReadFailure("UPSTREAM_RESPONSE_INVALID")
        return {
            "url": item["url"],
            "scope": "first_page_comments_only",
            "comments": [str(row["content"]["message"])[:1000] for row in rows[:count]],
            "sources": budget.sources,
        }


class BilibiliToolReader(BilibiliReader):
    """Lease-compatible reader; discovery uses only an operator-configured keyword."""

    def __init__(self, public, keyword=None):
        super().__init__(public)
        self.tools, self.keyword = BilibiliTools(public), keyword

    async def read(self, item, budget):
        if item["operation"] == "discover":
            if not self.keyword:
                raise ReadFailure("DISCOVERY_QUERY_REQUIRED")
            return await self.tools.call(
                "bili_search", {"keyword": self.keyword, "num": min(20, item["max_items"])}, budget
            )
        result = await super().read(item, budget)
        if isinstance(self.public, BilibiliAccountHTTP):
            self.public.verify(budget)
        return result
