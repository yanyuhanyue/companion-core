"""Read guest HTML or an explicitly configured two-GET source, without account authority."""

import hashlib
import ipaddress
import json
import re
from datetime import UTC, datetime
from html.parser import HTMLParser
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit

import httpcore

from companion.source_policy import source_definition
from source_reader.network import ReadFailure, post_url, response_body

IMAGE_HOSTS = source_definition("heybox").image_hosts
POST_ID = r"(?:[0-9]{1,32}|[0-9a-fA-F]{12})"


def post_matches(post, identifier):
    actual = str(post.get("linkid", post.get("link_id", identifier))).lower()
    if actual == identifier:
        return True
    # Short share IDs resolve to numeric post IDs. Accept that mapping only when
    # this same response binds the requested alias in the official share locator.
    share = post.get("share_url")
    if (identifier.isdigit() or not re.fullmatch(r"[0-9a-f]{12}", identifier)
            or not re.fullmatch(r"[0-9]{1,32}", actual)
            or not isinstance(share, str) or len(share) > 2048
            or "\\" in share or any(ord(char) <= 32 for char in share)):
        return False
    try:
        locator = urlsplit(share)
        ids = [value.lower() for key, value in parse_qsl(
            locator.query, keep_blank_values=True, max_num_fields=20
        ) if key == "link_id"]
        return (locator.scheme == "https" and locator.hostname == "api.xiaoheihe.cn"
                and not locator.username and not locator.password
                and locator.port in {None, 443} and not locator.fragment
                and locator.path == "/v3/bbs/app/api/web/share" and ids == [identifier])
    except ValueError:
        return False


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


def comment_snapshot(body, post):
    groups = body.get("comments")
    if not isinstance(groups, list):
        return None
    total = post.get("comment_num")
    total = total if type(total) is int and 0 <= total <= 2147483647 else None
    more = body.get("has_more_floors") in (True, 1, "1")
    items, seen, remaining = [], set(), 6000
    partial = more or total is None or len(groups) > 40
    for group in groups[:40]:
        if not isinstance(group, dict):
            partial = True
            continue
        rows = group.get("comment", [group])
        if not isinstance(rows, list):
            partial = True
            continue
        root = None
        partial |= len(rows) > 40
        for row in rows[:40]:
            if not isinstance(row, dict):
                partial = True
                continue
            identifier = str(row.get("commentid", ""))
            if not re.fullmatch(r"[0-9]{1,32}", identifier) or identifier in seen:
                partial = True
                continue
            if len(items) == 40:
                partial = True
                break
            parts = article_parts(row.get("text", ""))
            text = "\n".join(value for kind, value in parts if kind == "text")
            images = row.get("imgs", [])
            image_count = len(images) if isinstance(images, list) else 0
            image_count += sum(kind == "image" for kind, _ in parts)
            allowance = min(1000, remaining)
            truncated = len(text) > allowance
            reply = str(row.get("replyid") or root or "")
            if reply == identifier:
                reply = ""
                partial = True
            user = row.get("user")
            author = user.get("username", "") if isinstance(user, dict) else ""
            floor = row.get("floor_num")
            items.append({"comment_id": identifier,
                "reply_to_id": reply if re.fullmatch(r"[0-9]{1,32}", reply) else None,
                "floor": floor if type(floor) is int and 0 <= floor <= 2147483647 else None,
                "author": author[:80] if isinstance(author, str) else "",
                "text": text[:allowance], "image_count": min(1000, image_count),
                "truncated": truncated})
            seen.add(identifier)
            root = root or identifier
            remaining -= min(len(text), allowance)
            partial |= truncated or row.get("has_more") in (True, 1, "1")
    return {"items": items, "reported_total": total,
            "partial": partial or total != len(items)}


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
            extensions=budget.request_extensions(),
        ) as response:
            return await response_body(response, budget)


class HeyboxReader:
    parser_version = "heybox/1"

    def grant_snapshot(self, required=False):
        snapshot = getattr(self.source, "grant_snapshot", None)
        return snapshot(required=required) if snapshot else None

    def __init__(self, public, source=None, clock=lambda: datetime.now(UTC).isoformat()):
        self.public, self.source, self.clock = public, source, clock
        self.parser_version = "heybox/2"

    async def read(self, item, budget):
        discover = item["operation"] == "discover"
        if item["operation"] not in {"discover", "read"}:
            raise ReadFailure("READER_OPERATION_INVALID")
        if discover and not self.source:
            raise ReadFailure("DISCOVERY_SOURCE_REQUIRED")
        url = "https://www.xiaoheihe.cn/" if discover else post_url(item["url"])
        comments = None
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
            if not post_matches(post, identifier):
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
            comments = comment_snapshot(payload["result"], post)
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
                    **({"comments": comments} if comments is not None else {}),
                },
            }
        ]
