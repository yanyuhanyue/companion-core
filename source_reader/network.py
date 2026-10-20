"""Fixed public targets, pinned DNS connections, and one budget across all read steps."""

import asyncio
import hashlib
import ipaddress
import re
import socket
from dataclasses import dataclass, field
from urllib.parse import urlsplit, urlunsplit

import httpcore


class ReadFailure(Exception):
    pass


def post_url(value):
    url = urlsplit(value)
    match = re.fullmatch(r"/app/bbs/link/([0-9]{1,32}|[0-9a-fA-F]{12})/?", url.path)
    if (
        url.scheme != "https"
        or url.hostname != "www.xiaoheihe.cn"
        or not match
        or url.username
        or url.password
        or url.port not in {None, 443}
        or url.fragment
        or len(value) > 2048
    ):
        raise ReadFailure("CONTENT_URL_NOT_ALLOWED")
    return "https://www.xiaoheihe.cn/app/bbs/link/" + match[1].lower()


def video_url(value):
    url = urlsplit(value)
    match = re.fullmatch(r"/video/(BV[0-9A-Za-z]{10}|av[0-9]{1,20})/?", url.path)
    if (
        url.scheme != "https"
        or url.hostname != "www.bilibili.com"
        or not match
        or url.username
        or url.password
        or url.port not in {None, 443}
        or url.fragment
    ):
        raise ReadFailure("CONTENT_URL_NOT_ALLOWED")
    return "https://www.bilibili.com/video/" + match[1]


def permitted_url(value):
    url = urlsplit(value)
    if (
        url.scheme != "https"
        or url.username
        or url.password
        or url.fragment
        or url.port not in {None, 443}
        or len(value) > 2048
    ):
        raise ReadFailure("CONTENT_URL_NOT_ALLOWED")
    if url.hostname == "api.bilibili.com" and url.path in {
        "/x/web-interface/view",
        "/x/player/v2",
        "/x/web-interface/search/type",
        "/x/v2/reply",
    }:
        return
    if url.hostname in {"aisubtitle.hdslb.com", "i0.hdslb.com"} and re.fullmatch(
        r"/bfs/subtitle/[A-Za-z0-9_.-]{1,240}\.json", url.path
    ):
        return
    if url.hostname == "aisubtitle.hdslb.com" and re.fullmatch(
        r"/bfs/ai_subtitle/prod/[0-9a-fA-F]{32,128}(?:\.json)?", url.path
    ):
        return
    if (
        url.hostname in {"www.bilibili.com", "www.xiaoheihe.cn"}
        and url.path == "/"
        and not url.query
    ):
        return
    if url.hostname == "www.xiaoheihe.cn":
        post_url(value)
        return
    video_url(value)


async def public_addresses(host, port):
    entries = await asyncio.get_running_loop().getaddrinfo(host, port, type=socket.SOCK_STREAM)
    addresses = sorted({row[4][0] for row in entries})
    if not addresses or any(not ipaddress.ip_address(ip).is_global for ip in addresses):
        raise ReadFailure("READER_NONPUBLIC_ADDRESS")
    return addresses


class PublicNetwork(httpcore.AsyncNetworkBackend):
    def __init__(self, resolver=public_addresses, backend=None):
        self.resolver = resolver
        self.backend = backend or httpcore.AnyIOBackend()

    async def connect_tcp(
        self,
        host,
        port,
        timeout=None,  # noqa: ASYNC109 - HTTPCore backend interface
        local_address=None,
        socket_options=None,
    ):
        # Resolve once, validate every answer, and dial the numeric address. HTTPCore retains
        # the original origin for Host, TLS SNI and certificate validation.
        async with asyncio.timeout(timeout or 10):
            addresses = await self.resolver(host, port)
            if not addresses or any(not ipaddress.ip_address(ip).is_global for ip in addresses):
                raise ReadFailure("READER_NONPUBLIC_ADDRESS")
            return await self.backend.connect_tcp(
                addresses[0],
                port,
                timeout=timeout,
                local_address=local_address,
                socket_options=socket_options,
            )

    async def connect_unix_socket(self, *args, **kwargs):
        raise ReadFailure("CONTENT_URL_NOT_ALLOWED")

    async def sleep(self, seconds):
        await asyncio.sleep(seconds)


@dataclass
class ReadBudget:
    max_bytes: int
    max_requests: int
    used_bytes: int = 0
    used_requests: int = 0
    sources: list = field(default_factory=list)
    authorization: bytes | None = field(default=None, repr=False)

    def reserve(self):
        if self.used_requests >= self.max_requests:
            raise ReadFailure("READER_REQUEST_LIMIT")
        if self.used_bytes >= self.max_bytes:
            raise ReadFailure("READER_BYTE_LIMIT")
        self.used_requests += 1


async def response_body(response, budget):
    """Read raw HTTPCore bytes; the public and configured-source clients share limits."""
    headers = {key.decode().lower(): value.decode() for key, value in response.headers}
    if response.status in {401, 403, 412, 429}:
        raise ReadFailure("UPSTREAM_ACCESS_LIMIT")
    if response.status != 200:
        raise ReadFailure("UPSTREAM_HTTP_ERROR")
    if headers.get("content-encoding", "identity").strip().lower() != "identity":
        raise ReadFailure("READER_ENCODING_UNSUPPORTED")
    if int(headers.get("content-length", "0")) > budget.max_bytes - budget.used_bytes:
        raise ReadFailure("READER_BYTE_LIMIT")
    body = bytearray()
    async for chunk in response.aiter_stream():
        budget.used_bytes += len(chunk)
        if budget.used_bytes > budget.max_bytes:
            raise ReadFailure("READER_BYTE_LIMIT")
        body.extend(chunk)
    if not body:
        raise ReadFailure("READ_BODY_UNAVAILABLE")
    return bytes(body)


class PublicHTTP:
    def __init__(self, pool=None):
        self.pool = pool or httpcore.AsyncConnectionPool(
            network_backend=PublicNetwork(),
            max_connections=1,
            max_keepalive_connections=0,
            retries=0,
        )

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        await self.pool.aclose()

    async def get(self, url, budget, role, *, headers=None):
        permitted_url(url)
        budget.reserve()
        async with self.pool.stream(
            "GET",
            url,
            headers={
                **(headers or {}),
                "Accept-Encoding": "identity",
                "User-Agent": "CompanionSourceReader/0.6",
            },
            extensions={"timeout": {key: 8.0 for key in ("connect", "read", "write", "pool")}},
        ) as response:
            body = await response_body(response, budget)
        parsed = urlsplit(url)
        # Signed CDN query parameters are transient fetch inputs, never persisted evidence.
        budget.sources.append(
            {
                "role": role,
                "url": urlunsplit((parsed.scheme, parsed.netloc, parsed.path, "", "")),
                "sha256": hashlib.sha256(body).hexdigest(),
                "bytes": len(body),
            }
        )
        return body
