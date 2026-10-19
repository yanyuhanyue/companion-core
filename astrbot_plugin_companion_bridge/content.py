"""Normalize owner-shared QQ cards and transfer images without persisting signed URLs.

Call only after the bridge has authenticated and claimed the owner's private event.
This module never imports Core, sends QQ messages, or invokes an LLM.
"""

import asyncio
import json
import re
from urllib.parse import parse_qs, urljoin, urlsplit

import httpx

QQ_IMAGE_HOSTS = {"multimedia.nt.qq.com.cn", "gchat.qpic.cn", "c2cpicdw.qpic.cn"}


def canonical_link(url):
    try:
        parsed = urlsplit(url)
        if parsed.scheme != "https" or parsed.username or parsed.port not in {None, 443}:
            return None
        if parsed.hostname in {"www.bilibili.com", "m.bilibili.com", "bilibili.com"}:
            match = re.fullmatch(r"/video/(BV[0-9A-Za-z]{10}|av[0-9]{1,20})/?", parsed.path)
            if match:
                return "https://www.bilibili.com/video/" + match[1]
        if parsed.hostname in {"www.xiaoheihe.cn", "xiaoheihe.cn", "api.xiaoheihe.cn"}:
            match = re.fullmatch(r"/(?:app/)?bbs/link/([0-9]{1,32})/?", parsed.path)
            post_id = match[1] if match else ""
            if parsed.path == "/v3/bbs/app/api/web/share":
                post_id = parse_qs(parsed.query).get("link_id", [""])[0]
            if re.fullmatch(r"[0-9]{1,32}", post_id):
                return "https://www.xiaoheihe.cn/app/bbs/link/" + post_id
        if parsed.hostname == "b23.tv" and re.fullmatch(r"/[A-Za-z0-9]{1,32}/?", parsed.path):
            return "https://b23.tv" + parsed.path.rstrip("/")
    except (TypeError, ValueError):
        pass
    return None


def qq_image_url(value):
    try:
        parsed = urlsplit(value)
        if (
            parsed.scheme not in {"https", "http"}
            or parsed.hostname not in QQ_IMAGE_HOSTS
            or parsed.username
            or parsed.port not in {None, 80, 443}
            or parsed.fragment
            or not parsed.path.startswith("/")
            or len(value) > 4096
        ):
            return None
        return parsed._replace(scheme="https", netloc=parsed.hostname).geturl()
    except (TypeError, ValueError):
        return None


def redact_image_urls(text):
    return re.sub(
        r"https?://[^\s<>\"']+",
        lambda match: "[图片]" if qq_image_url(match[0]) else match[0],
        text,
    )


def share_candidates(raw_message, text=""):
    links, images = [], []

    def add_link(value):
        if not isinstance(value, str) or len(value) > 2048:
            return
        canonical = canonical_link(value.rstrip(".,;:!?)]}，。"))
        if canonical and canonical not in links and len(links) < 2:
            links.append(canonical)

    for found in re.findall(r"https://[^\s<>\"'，。！；（）]+", text[:20000]):
        add_link(found)
    segments = raw_message.get("message", []) if isinstance(raw_message, dict) else []
    if not isinstance(segments, list):
        segments = []
    for segment in segments[:40]:
        if not isinstance(segment, dict) or not isinstance(segment.get("data"), dict):
            continue
        data = segment["data"]
        if segment.get("type") == "image" and len(images) < 2:
            url = qq_image_url(data.get("url") or data.get("file"))
            if url and not any(item["url"] == url for item in images):
                images.append({"slot": len(images), "url": url})
        elif segment.get("type") == "json":
            raw = data.get("data")
            if not isinstance(raw, str) or len(raw) > 32768:
                continue
            try:
                card = json.loads(raw)
            except (ValueError, RecursionError):
                continue
            pending, visited = [(card, 0)], 0
            while pending and visited < 256:
                node, depth = pending.pop()
                visited += 1
                if depth > 6:
                    continue
                if isinstance(node, dict):
                    for key, value in list(node.items())[:64]:
                        if key in {"qqdocurl", "jumpUrl", "jump_url", "url"}:
                            add_link(value)
                        elif isinstance(value, (dict, list)):
                            pending.append((value, depth + 1))
                elif isinstance(node, list):
                    pending.extend((value, depth + 1) for value in node[:32])
    return {"links": links, "images": images}


async def resolve_short_link(url, transport=None):
    candidate = canonical_link(url)
    if not candidate:
        return None
    if urlsplit(candidate).hostname != "b23.tv":
        return candidate
    async with (
        asyncio.timeout(10),
        httpx.AsyncClient(
            transport=transport, timeout=5, trust_env=False, follow_redirects=False
        ) as client,
    ):
        for _ in range(2):
            if urlsplit(candidate).hostname != "b23.tv":
                return candidate
            async with client.stream("GET", candidate) as response:
                if response.status_code not in {301, 302, 303, 307, 308}:
                    return None
                candidate = canonical_link(urljoin(candidate, response.headers.get("location", "")))
                if not candidate:
                    return None
        return candidate if urlsplit(candidate).hostname != "b23.tv" else None


async def prepare_shared(raw_message, text="", *, enabled=False, transport=None):
    if not enabled:
        return [], []
    candidates = share_candidates(raw_message, text)
    manifest = []
    for url in candidates["links"]:
        try:
            resolved = await resolve_short_link(url, transport)
        except (httpx.HTTPError, TimeoutError):
            resolved = None
        if resolved and not any(item.get("url") == resolved for item in manifest):
            manifest.append({"kind": "link", "url": resolved})
    for image in candidates["images"]:
        manifest.append({"kind": "image", "slot": image["slot"]})
    return manifest, candidates["images"]


async def fetch_qq_image(url, max_bytes=1_000_000, transport=None):
    url = qq_image_url(url)
    if not url:
        raise ValueError("CONTENT_IMAGE_URL_NOT_ALLOWED")
    async with (
        asyncio.timeout(10),
        httpx.AsyncClient(
            transport=transport, timeout=10, trust_env=False, follow_redirects=False
        ) as client,
    ):
        async with client.stream("GET", url) as response:
            if response.status_code != 200:
                raise ValueError("CONTENT_IMAGE_FETCH_FAILED")
            mime = response.headers.get("content-type", "").split(";")[0].lower().strip()
            if mime not in {"image/png", "image/jpeg"}:
                raise ValueError("CONTENT_IMAGE_TYPE_UNSUPPORTED")
            data = bytearray()
            async for part in response.aiter_bytes():
                data.extend(part)
                if len(data) > max_bytes:
                    raise ValueError("CONTENT_IMAGE_TOO_LARGE")
    return bytes(data), mime


async def forward_images(
    client, owner_event_id, images, *, enabled=False, max_bytes=1_000_000, transport=None
):
    """Call after inbox acceptance. Ambiguous POST results are never retried here."""
    if not enabled:
        return []
    results = []
    try:
        status = await client.get(f"/internal/v2/content/{owner_event_id}")
        status.raise_for_status()
        allowed = {
            item["slot"]
            for item in status.json()["items"]
            if item["kind"] == "qq_image" and item["state"] == "WAITING"
        }
    except (ValueError, KeyError, httpx.HTTPError):
        return [
            {
                "slot": image["slot"],
                "state": "UNKNOWN",
                "error_code": "CONTENT_PREFLIGHT_UNAVAILABLE",
            }
            for image in images[:2]
        ]
    for image in images[:2]:
        slot = image["slot"]
        if slot not in allowed:
            continue
        try:
            claim = await client.post(
                f"/internal/v2/content/{owner_event_id}/image/claim",
                json={"request_key": f"image:{slot}"},
            )
            claim.raise_for_status()
            allowed_bytes = min(max_bytes, int(claim.json()["max_bytes"]))
            data, mime = await fetch_qq_image(image["url"], allowed_bytes, transport)
            response = await client.post(
                f"/internal/v2/content/{owner_event_id}/image",
                content=data,
                headers={"Content-Type": mime, "X-Content-Key": f"image:{slot}"},
                timeout=35,
            )
            response.raise_for_status()
            results.append({"slot": slot, **response.json()})
        except (ValueError, KeyError, httpx.HTTPError, TimeoutError):
            results.append(
                {"slot": slot, "state": "UNKNOWN", "error_code": "CONTENT_IMAGE_UNAVAILABLE"}
            )
    return results


class ImageQueue:
    """A bounded, short-lived transfer queue; signed URLs exist only in memory."""

    def __init__(self, client, inbox_state, *, capacity=16, lifetime=60):
        self.client = client
        self.inbox_state = inbox_state
        self.capacity = capacity
        self.lifetime = lifetime
        self.queue = asyncio.Queue(maxsize=capacity)
        self.keys = set()

    def put(self, key, event_id, images):
        if not images or key in self.keys or len(self.keys) >= self.capacity:
            return False
        images = [{"slot": image["slot"], "url": image["url"]} for image in images[:2]]
        deadline = asyncio.get_running_loop().time() + self.lifetime
        self.queue.put_nowait((key, event_id, images, deadline))
        self.keys.add(key)
        return True

    async def run(self):
        while True:
            key, event_id, images, deadline = await self.queue.get()
            try:
                if deadline <= asyncio.get_running_loop().time():
                    continue
                async with asyncio.timeout_at(deadline):
                    while True:
                        state = self.inbox_state(key)
                        if state is None:
                            break  # Spool deletes only after a 202 inbox acknowledgment.
                        if state != "pending":
                            break
                        await asyncio.sleep(0.5)
                    if state is None:
                        await forward_images(self.client, event_id, images, enabled=True)
            except Exception:
                # Any uncertain transfer stays claimed in Core; never retry external I/O here.
                pass
            finally:
                images.clear()
                self.keys.discard(key)
                self.queue.task_done()

    def clear(self):
        while not self.queue.empty():
            key, _, images, _ = self.queue.get_nowait()
            images.clear()
            self.keys.discard(key)
            self.queue.task_done()
