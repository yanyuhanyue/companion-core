"""Read guest HTML or an explicitly configured two-GET source, without account authority."""

import hashlib
import ipaddress
import json
import re
from datetime import UTC, datetime
from html.parser import HTMLParser
from urllib.parse import urlencode, urljoin, urlsplit, urlunsplit

import httpcore

from source_reader.network import ReadFailure, post_url, response_body

IMAGE_HOSTS = {"imgheybox.max-c.com", "imgheybox1.max-c.com"}
POST_ID = r"(?:[0-9]{1,32}|[0-9a-fA-F]{12})"


def image_url(value):
    url = urlsplit(urljoin("https://www.xiaoheihe.cn/", value))
    if (
        url.scheme not in {"http", "https"}
        or url.hostname not in IMAGE_HOSTS
        or url.username
        or url.password
        or url.port not in {None, 80, 443}
        or url.fragment
        or not url.path.startswith("/")
        or len(value) > 2048
    ):
        raise ValueError("Unusable article image")
    return urlunsplit(("https", url.hostname, url.path, "", ""))


class ArticleHTML(HTMLParser):
    def __init__(self, article_only=False):
        super().__init__(convert_charrefs=True)
        self.article_only, self.depth, self.skip = article_only, 0, 0
        self.parts, self.text = [], []
        self.title, self.in_title, self.login_form = "", False, False

    def flush(self):
        text = " ".join("".join(self.text).split())
        if text:
            self.parts.append(("text", text))
        self.text = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "article":
            self.depth += 1
        if tag in {"script", "style", "nav", "form", "button"}:
            self.skip += 1
        if tag == "input" and attrs.get("type", "").lower() == "password":
            self.login_form = True
        if tag == "title":
            self.in_title = True
        if tag == "meta" and attrs.get("property") == "og:title":
            self.title = attrs.get("content", "")
        if self.skip or (self.article_only and not self.depth):
            return
        if tag in {"p", "div", "br", "li", "h1", "h2", "h3", "blockquote"}:
            self.flush()
        if tag == "img":
            self.flush()
            self.parts.append(("image", attrs.get("src", attrs.get("data-src", ""))))

    def handle_endtag(self, tag):
        if tag in {"p", "div", "li", "h1", "h2", "h3", "blockquote", "article"}:
            self.flush()
        if tag == "article":
            self.depth = max(0, self.depth - 1)
        if tag in {"script", "style", "nav", "form", "button"}:
            self.skip = max(0, self.skip - 1)
        if tag == "title":
            self.in_title = False

    def handle_data(self, value):
        if self.in_title and not self.title:
            self.title = value.strip()
        if not self.skip and (not self.article_only or self.depth):
            self.text.append(value)

    def finish(self):
        self.close()
        self.flush()
        return self.parts


def article_parts(value):
    if isinstance(value, str):
        try:
            decoded = json.loads(value)
            value = decoded if isinstance(decoded, list) else value
        except ValueError:
            pass
    blocks = value if isinstance(value, list) else [value]
    if len(blocks) > 1000:
        raise ReadFailure("UPSTREAM_RESPONSE_INVALID")
    parts = []
    for block in blocks:
        if isinstance(block, dict):
            if block.get("type") in {"img", "image"}:
                parts.append(("image", block.get("url", "")))
                continue
            if block.get("type") not in {"text", "html", "paragraph"}:
                continue
            block = block.get("text", block.get("content", ""))
        if isinstance(block, str):
            parser = ArticleHTML()
            parser.feed(block)
            parts.extend(parser.finish())
    return parts


def render_article(parts, limit=11500):
    text, images, body_chars, truncated = "", [], 0, False
    prepared, urls = [], []
    for kind, value in parts:
        if kind == "image":
            try:
                value = image_url(value)
            except (TypeError, ValueError):
                truncated = True
                continue
            if value in urls:
                continue
            if len(urls) == 32:
                truncated = True
                continue
            urls.append(value)
        prepared.append((kind, value))
    # Reserve image placeholders before truncating long prose so later images remain queued.
    remaining_text = limit - sum(len(f"[图片{i + 1}链接]") + 1 for i in range(len(urls)))
    for kind, value in prepared:
        separator = "\n" if text else ""
        if kind == "image":
            addition = f"[图片{len(images) + 1}链接]"
            images.append({"url": value, "text_offset": len(text) + len(separator)})
        else:
            addition = value.strip()
            available = max(0, remaining_text - len(separator))
            if len(addition) > available:
                addition, truncated = addition[:available], True
            if not addition:
                continue
            body_chars += len(addition)
            remaining_text -= len(separator) + len(addition)
        text += separator + addition
    if not body_chars:
        raise ReadFailure("READ_BODY_UNAVAILABLE")
    if body_chars < 80 and re.search(r"(?:请|需要|必须)登录.{0,12}(?:查看|阅读)", text):
        raise ReadFailure("UPSTREAM_ACCESS_LIMIT")
    return text, images, truncated


class SourceAPI:
    """A fixed owner-configured service origin; no arbitrary routes or upstream credentials."""

    def __init__(self, origin, token, pool=None):
        url = urlsplit(origin)
        if (
            url.scheme not in {"http", "https"}
            or not url.hostname
            or url.username
            or url.password
            or url.query
            or url.fragment
            or url.path not in {"", "/"}
            or url.port == 0
            or not re.fullmatch(r"[\x21-\x7e]{24,512}", token)
        ):
            raise ValueError("Set a fixed source origin and a distinct source token")
        if url.scheme == "http" and url.hostname != "localhost":
            ip = ipaddress.ip_address(url.hostname)
            if not (ip.is_private or ip in ipaddress.ip_network("100.64.0.0/10")):
                raise ValueError("Plain HTTP source origin must be private")
        self.origin, self.token = origin.rstrip("/"), token
        self.pool = pool or httpcore.AsyncConnectionPool(
            max_connections=1, max_keepalive_connections=0, retries=0
        )

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        await self.pool.aclose()

    async def get(self, item, budget):
        if item["operation"] == "discover":
            path = "/api/feeds"
            params = {"page": 1, "limit": min(40, int(item["max_items"]))}
        elif item["operation"] == "read":
            identifier = post_url(item["url"]).rsplit("/", 1)[1]
            path = "/api/post/detail/" + identifier
            params = {"page": 1, "index": 1, "limit": 1, "is_first": 1, "owner_only": 1}
        else:
            raise ReadFailure("READER_OPERATION_INVALID")
        budget.reserve()
        async with self.pool.stream(
            "GET",
            self.origin + path + "?" + urlencode(params),
            headers={"Authorization": "Bearer " + self.token, "Accept-Encoding": "identity"},
            extensions={"timeout": {key: 8.0 for key in ("connect", "read", "write", "pool")}},
        ) as response:
            return await response_body(response, budget)


class HeyboxReader:
    def __init__(self, public, source=None, clock=lambda: datetime.now(UTC).isoformat()):
        self.public, self.source, self.clock = public, source, clock

    async def read(self, item, budget):
        discover = item["operation"] == "discover"
        if item["operation"] not in {"discover", "read"}:
            raise ReadFailure("READER_OPERATION_INVALID")
        if discover and not self.source:
            raise ReadFailure("DISCOVERY_SOURCE_REQUIRED")
        url = "https://www.xiaoheihe.cn/" if discover else post_url(item["url"])
        if self.source:
            raw = await self.source.get(item, budget)
            payload = json.loads(raw)
            if payload.get("status") in {
                "relogin",
                "login",
                "need_login",
                "show_captcha",
                "forbidden",
            }:
                raise ReadFailure("UPSTREAM_ACCESS_LIMIT")
            if payload.get("status") != "ok" or not isinstance(payload.get("result"), dict):
                raise ReadFailure("UPSTREAM_RESPONSE_INVALID")
            if discover:
                rows = payload["result"].get("links")
                if not isinstance(rows, list):
                    raise ReadFailure("UPSTREAM_RESPONSE_INVALID")
                found = {}
                for row in rows[: min(40, item["max_items"])]:
                    identifier, title = str(row.get("linkid", "")), row.get("title", "")
                    if (
                        re.fullmatch(POST_ID, identifier)
                        and isinstance(title, str)
                        and title.strip()
                    ):
                        target = "https://www.xiaoheihe.cn/app/bbs/link/" + identifier.lower()
                        found.setdefault(target, {"url": target, "title": title.strip()[:300]})
                return list(found.values())
            post = payload["result"].get("link", payload["result"].get("post"))
            if not isinstance(post, dict):
                raise ReadFailure("READ_BODY_UNAVAILABLE")
            identifier = url.rsplit("/", 1)[1]
            if str(post.get("linkid", post.get("link_id", identifier))).lower() != identifier:
                raise ReadFailure("READER_TARGET_MISMATCH")
            title = post.get("title", "")
            parts = article_parts(post.get("text", post.get("content", "")))
            # The native tree response may keep images separately. Their original relative
            # placement is unknown; append references after the body, never invent captions.
            extra_images = post.get("images", post.get("image_urls", []))
            if not isinstance(extra_images, list) or len(extra_images) > 1000:
                raise ReadFailure("UPSTREAM_RESPONSE_INVALID")
            parts.extend(
                (
                    "image",
                    value.get("url", value.get("src", "")) if isinstance(value, dict) else value,
                )
                for value in extra_images
            )
            method = "source_api"
        else:
            raw = await self.public.get(url, budget, "page")
            page = ArticleHTML(article_only=True)
            page.feed(raw.decode("utf-8", errors="strict"))
            parts = page.finish()
            if page.login_form:
                raise ReadFailure("UPSTREAM_ACCESS_LIMIT")
            title, method = page.title, "public_html"
        if not isinstance(title, str):
            raise ReadFailure("UPSTREAM_RESPONSE_INVALID")
        text, images, truncated = render_article(parts)
        return [
            {
                "url": url,
                "title": title.strip()[:300] or "小黑盒帖子 " + url.rsplit("/", 1)[1],
                "text": text,
                "image_urls": [image["url"] for image in images],
                "article": {
                    "method": method,
                    "fetched_at": self.clock(),
                    "response_sha256": hashlib.sha256(raw).hexdigest(),
                    "response_bytes": len(raw),
                    "images": images,
                    "truncated": truncated,
                },
            }
        ]
