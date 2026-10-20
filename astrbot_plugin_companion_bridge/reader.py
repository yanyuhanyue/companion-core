"""Independent bounded public-page reader. No Core import or platform account authority."""

import asyncio
import ipaddress
import json
import re
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit

import httpx


class ReadFailure(Exception):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


def validate_source_api(url, token):
    parsed = urlsplit(url)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
        or len(token) < 24
    ):
        raise ValueError("Source API needs a fixed HTTP(S) address and a separate token")
    if parsed.scheme == "http" and parsed.hostname != "localhost":
        try:
            address = ipaddress.ip_address(parsed.hostname)
        except ValueError as error:
            raise ValueError("Plain HTTP source API needs a private IP") from error
        if not (address.is_private or address in ipaddress.ip_network("100.64.0.0/10")):
            raise ValueError("Plain HTTP source API needs a private IP")


class PostText(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts, self.skip = [], 0

    def handle_starttag(self, tag, attrs):
        if tag in {"script", "style"}:
            self.skip += 1

    def handle_endtag(self, tag):
        if tag in {"script", "style"}:
            self.skip = max(0, self.skip - 1)

    def handle_data(self, data):
        if not self.skip and data.strip():
            self.parts.append(data.strip())


def post_text(value):
    if isinstance(value, str):
        try:
            decoded = json.loads(value)
            value = decoded if isinstance(decoded, list) else value
        except ValueError:
            pass
    blocks = value if isinstance(value, list) else [value]
    parser = PostText()
    for block in blocks[:1000]:
        if isinstance(block, str):
            parser.feed(block)
        elif isinstance(block, dict) and block.get("type") not in {"img", "image", "video"}:
            text = block.get("text", block.get("content", ""))
            if isinstance(text, str):
                parser.feed(text)
    return "\n".join(parser.parts)[:12000]


def post_images(value):
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            return []
    if not isinstance(value, list):
        return []
    return [
        str(block["url"])
        for block in value[:1000]
        if isinstance(block, dict)
        and block.get("type") in {"img", "image"}
        and isinstance(block.get("url"), str)
    ][:2]


async def read_heybox_api(client, item):
    """Optional two-endpoint HTTP contract; no dependency on a second AI/identity runtime."""
    if item["operation"] == "discover":
        path, params = "/api/feeds", {"page": 1, "limit": min(40, int(item["max_items"]))}
    elif item["operation"] == "read":
        canonical = safe_target("heybox", item["url"])
        path = "/api/post/detail/" + canonical.rsplit("/", 1)[1]
        params = {"page": 1, "index": 1, "limit": 1, "is_first": 1, "owner_only": 1}
    else:
        raise ReadFailure("READER_OPERATION_INVALID")
    async with client.stream(
        "GET", path, params=params, follow_redirects=False, headers={"Accept-Encoding": "identity"}
    ) as response:
        if response.status_code in {401, 403, 429}:
            raise ReadFailure("UPSTREAM_ACCESS_LIMIT")
        if response.status_code != 200:
            raise ReadFailure("UPSTREAM_HTTP_ERROR")
        if response.headers.get("content-encoding", "identity").strip().lower() != "identity":
            raise ReadFailure("READER_ENCODING_UNSUPPORTED")
        raw = bytearray()
        async for chunk in response.aiter_bytes():
            raw.extend(chunk)
            if len(raw) > int(item["max_bytes"]):
                raise ReadFailure("READER_BYTE_LIMIT")
    payload = json.loads(raw)
    if payload.get("status") != "ok" or not isinstance(payload.get("result"), dict):
        raise ReadFailure("UPSTREAM_RESPONSE_INVALID")
    if item["operation"] == "discover":
        rows = payload["result"].get("links")
        if not isinstance(rows, list):
            raise ReadFailure("UPSTREAM_RESPONSE_INVALID")
        result = []
        for row in rows[: int(item["max_items"])]:
            if not isinstance(row, dict):
                raise ReadFailure("UPSTREAM_RESPONSE_INVALID")
            post_id, title = str(row.get("linkid", "")), str(row.get("title", "")).strip()
            if re.fullmatch(r"[0-9]{1,32}", post_id) and title:
                result.append(
                    {
                        "title": title[:300],
                        "text": "",
                        "url": "https://www.xiaoheihe.cn/app/bbs/link/" + post_id,
                    }
                )
        return result, len(raw)
    post = payload["result"].get("link")
    if not isinstance(post, dict):
        raise ReadFailure("UPSTREAM_RESPONSE_INVALID")
    title, text = str(post.get("title", "")).strip(), post_text(post.get("text", ""))
    if not title or not text:
        raise ReadFailure("READ_BODY_UNAVAILABLE")
    return [
        {
            "title": title[:300],
            "text": text,
            "url": canonical,
            "image_urls": post_images(post.get("text", "")),
        }
    ], len(raw)


class PublicPage(HTMLParser):
    def __init__(self, source):
        super().__init__(convert_charrefs=True)
        self.source = source
        self.title, self.description, self.body, self.links = "", "", [], []
        self.article_depth, self.skip, self.in_title = 0, 0, False
        self.anchor = None

    def handle_starttag(self, tag, attrs):
        values = dict(attrs)
        if tag in {"script", "style"}:
            self.skip += 1
        if tag == "title":
            self.in_title = True
        if tag == "article" or self.article_depth:
            if tag not in {"img", "br", "hr", "input", "meta", "link", "source"}:
                self.article_depth += 1
        if tag == "meta":
            name = values.get("property", values.get("name", ""))
            if name in {"og:title"}:
                self.title = values.get("content", "")
            if name in {"og:description", "description"}:
                self.description = values.get("content", "")
        if tag == "a":
            self.anchor = {"url": values.get("href", ""), "title": values.get("title", "")}
        if tag == "img" and self.anchor and not self.anchor["title"]:
            self.anchor["title"] = values.get("alt", "")

    def handle_endtag(self, tag):
        if tag in {"script", "style"}:
            self.skip = max(0, self.skip - 1)
        if tag == "title":
            self.in_title = False
        if self.article_depth and tag not in {"img", "br", "hr", "input", "meta", "link", "source"}:
            self.article_depth -= 1
        if tag == "a" and self.anchor:
            self.links.append(self.anchor)
            self.anchor = None

    def handle_data(self, data):
        if self.skip:
            return
        if self.in_title and not self.title:
            self.title = data
        if self.article_depth and data.strip():
            self.body.append(data.strip())
        if self.anchor and not self.anchor["title"] and data.strip():
            self.anchor["title"] = data.strip()


def safe_target(source, url):
    parsed = urlsplit(url)
    if parsed.scheme != "https" or parsed.username or parsed.port not in {None, 443}:
        raise ReadFailure("CONTENT_URL_NOT_ALLOWED")
    if source == "bilibili" and parsed.hostname == "www.bilibili.com":
        match = re.fullmatch(r"/video/(BV[0-9A-Za-z]{10}|av[0-9]{1,20})/?", parsed.path)
        if match:
            return "https://www.bilibili.com/video/" + match[1]
    if source == "heybox" and parsed.hostname == "www.xiaoheihe.cn":
        match = re.fullmatch(r"/app/bbs/link/([0-9]{1,32}|[0-9a-fA-F]{12})/?", parsed.path)
        if match:
            return "https://www.xiaoheihe.cn/app/bbs/link/" + match[1]
    raise ReadFailure("CONTENT_URL_NOT_ALLOWED")


async def bounded_get(client, url, max_bytes, params=None):
    parsed = urlsplit(url)
    allowed = {
        ("www.bilibili.com", "/"),
        ("www.xiaoheihe.cn", "/"),
        ("api.bilibili.com", "/x/web-interface/view"),
    }
    if (parsed.hostname, parsed.path) not in allowed:
        safe_target("heybox" if parsed.hostname == "www.xiaoheihe.cn" else "bilibili", url)
    if parsed.scheme != "https" or parsed.username or parsed.port not in {None, 443}:
        raise ReadFailure("CONTENT_URL_NOT_ALLOWED")
    async with client.stream(
        "GET", url, params=params, follow_redirects=False, headers={"Accept-Encoding": "identity"}
    ) as response:
        if response.status_code in {401, 403, 412, 429}:
            raise ReadFailure("UPSTREAM_ACCESS_LIMIT")
        if response.status_code != 200:
            raise ReadFailure("UPSTREAM_HTTP_ERROR")
        if response.headers.get("content-encoding", "identity").strip().lower() != "identity":
            raise ReadFailure("READER_ENCODING_UNSUPPORTED")
        if int(response.headers.get("content-length", "0")) > max_bytes:
            raise ReadFailure("READER_BYTE_LIMIT")
        chunks = bytearray()
        async for chunk in response.aiter_bytes():
            chunks.extend(chunk)
            if len(chunks) > max_bytes:
                raise ReadFailure("READER_BYTE_LIMIT")
        return bytes(chunks)


async def read_public(client, source, item):
    if source not in {"bilibili", "heybox"}:
        raise ReadFailure("READER_SOURCE_INVALID")
    max_bytes = min(1048576, int(item["max_bytes"]))
    operation = item["operation"]
    if operation == "read":
        url = safe_target(source, item["url"])
        if source == "bilibili":
            video = url.rsplit("/", 1)[1]
            data = await bounded_get(
                client,
                "https://api.bilibili.com/x/web-interface/view",
                max_bytes,
                {"bvid": video} if video.startswith("BV") else {"aid": video[2:]},
            )
            payload = json.loads(data)
            if payload.get("code") != 0 or not isinstance(payload.get("data"), dict):
                raise ReadFailure("UPSTREAM_RESPONSE_INVALID")
            video_data = payload["data"]
            title, text = (
                str(video_data.get("title", "")).strip(),
                str(video_data.get("desc", "")).strip(),
            )
            if not title or not text:
                raise ReadFailure("READ_BODY_UNAVAILABLE")
            picture = str(video_data.get("pic", ""))
            return [
                {
                    "title": title[:300],
                    "text": text[:12000],
                    "url": url,
                    "image_urls": [picture] if picture else [],
                }
            ], len(data)
    elif operation == "discover":
        url = "https://www.bilibili.com/" if source == "bilibili" else "https://www.xiaoheihe.cn/"
    else:
        raise ReadFailure("READER_OPERATION_INVALID")
    data = await bounded_get(client, url, max_bytes)
    page = PublicPage(source)
    page.feed(data.decode("utf-8", errors="replace"))
    if operation == "read":
        text = "\n".join(page.body).strip()
        if not page.title.strip() or not text:
            # A title-only SPA shell is not a read post; dynamic pages need a content adapter.
            raise ReadFailure("READ_BODY_UNAVAILABLE")
        return [{"title": page.title[:300], "text": text[:12000], "url": url}], len(data)
    result, seen = [], set()
    for link in page.links:
        try:
            canonical = safe_target(source, urljoin(url, link["url"]))
        except (ReadFailure, ValueError):
            continue
        if canonical not in seen and link["title"].strip():
            seen.add(canonical)
            result.append({"title": link["title"].strip()[:300], "text": "", "url": canonical})
    return result[: min(40, int(item["max_items"]))], len(data)


async def reader_tick(core_client, public_client, adapter_id, source, source_client=None):
    prefix = "/internal/v2/readers/" + adapter_id
    response = await core_client.post(prefix + "/lease")
    response.raise_for_status()
    item = response.json()["item"]
    if not item:
        return False
    result = {"lease_token": item["lease_token"], "status": "SUCCESS", "items": []}
    try:
        async with asyncio.timeout(20):
            result["items"], result["used_bytes"] = (
                await read_heybox_api(source_client, item)
                if source == "heybox" and source_client is not None
                else await read_public(public_client, source, item)
            )
    except ReadFailure as error:
        result.update(status="FAILED", error_code=error.code)
    except (TimeoutError, httpx.HTTPError):
        result.update(status="UNKNOWN", error_code="READER_NETWORK_UNKNOWN")
    except (ValueError, KeyError, TypeError):
        result.update(status="FAILED", error_code="UPSTREAM_RESPONSE_INVALID")
    # No external I/O replay after an uncertain result report; the lease expires conservatively.
    response = await core_client.post(prefix + "/" + item["request_id"] + "/result", json=result)
    response.raise_for_status()
    return True
