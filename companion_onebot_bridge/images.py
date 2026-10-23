"""Ordered current-owner images, transient URLs, and source-checked bounded GETs."""

import asyncio
import hashlib
import ipaddress
import logging
import socket
from contextvars import ContextVar
from dataclasses import dataclass
from urllib.parse import urlsplit

import httpx

from .spool import encode

HOSTS = frozenset({"multimedia.nt.qq.com.cn", "gchat.qpic.cn", "c2cpicdw.qpic.cn"})
# The authenticated platform message envelope contains at most 64 segments.
MAX_OWNER_IMAGES = 64
_private_download = ContextVar("private_qq_image_download", default=False)


class _PrivateTrace(logging.Filter):
    def filter(self, record):
        return not _private_download.get()


# HTTPCore DEBUG traces include full response headers (including signed Location).
# Filter only this task's download; do not change logger levels or other requests.
for _name in ("connection", "connection_pool", "http11", "http2", "proxy", "socks_proxy"):
    logging.getLogger("httpcore." + _name).addFilter(_PrivateTrace())


@dataclass(frozen=True, repr=False)
class OriginalImage:
    index: int
    slot: int
    source_sha256: str
    url: str

    def projection(self):
        return {
            "index": self.index,
            "type": "image",
            "actor": "UNVERIFIED",
            "coverage": "UNREAD",
            "slot": self.slot,
            "source_sha256": self.source_sha256,
        }


def image_url(value):
    if (
        not isinstance(value, str)
        or len(value) > 4096
        or "\\" in value
        or any(ord(c) <= 32 or ord(c) == 127 for c in value)
    ):
        raise ValueError("IMAGE_URL_NOT_ALLOWED")
    try:
        url = urlsplit(value)
        if (
            url.scheme not in {"http", "https"}
            or url.hostname not in HOSTS
            or url.username is not None
            or url.password is not None
            or url.fragment
            or url.port not in {None, 80, 443}
            or not url.path.startswith("/")
        ):
            raise ValueError()
        return url._replace(scheme="https", netloc=url.hostname).geturl()
    except ValueError:
        raise ValueError("IMAGE_URL_NOT_ALLOWED") from None


def original_images(message):
    """Caller has authenticated the envelope; never walk node/reply/card descendants."""
    if isinstance(message, str):
        return []
    if not isinstance(message, list) or not 1 <= len(message) <= 64:
        raise ValueError("INVALID_IMAGE_MESSAGE")
    images = [
        (i, value)
        for i, value in enumerate(message)
        if isinstance(value, dict) and value.get("type") == "image"
    ]
    return [_original_image(index, slot, value) for slot, (index, value) in enumerate(images)]


def _original_image(index, slot, value):
    data = value.get("data")
    if set(value) != {"type", "data"} or not isinstance(data, dict):
        raise ValueError("INVALID_IMAGE_SEGMENT")
    # SnowLuma emits subtype 11 for owner-sent animated image emoji. The original
    # CDN bytes still cross the signed upload boundary; flash/market-face do not.
    if (
        not set(data) <= {"url", "file", "sub_type", "summary"}
        or type(data.get("sub_type", 0)) is not int
        or data.get("sub_type", 0) not in {0, 11}
        or any(
            not isinstance(data[key], str) or (key != "url" and "\x00" in data[key])
            for key in ("url", "file", "summary") if key in data
        )
    ):
        raise ValueError("IMAGE_KIND_UNSUPPORTED")
    url = image_url(data.get("url") or data.get("file"))
    return OriginalImage(index, slot, hashlib.sha256(encode(value).encode()).hexdigest(), url)


def global_address(value):
    try:
        ip = ipaddress.ip_address(value)
        return (
            ip.is_global
            and not ip.is_multicast
            and (ip.version == 4 or not (ip.ipv4_mapped or ip.sixtofour or ip.teredo))
        )
    except ValueError:
        return False


async def resolve_public(host, port):
    addresses = await asyncio.wait_for(
        asyncio.get_running_loop().getaddrinfo(host, port, type=socket.SOCK_STREAM),
        5,
    )
    values = sorted({row[4][0] for row in addresses})
    if not values or any(not global_address(value) for value in values):
        raise ValueError("IMAGE_NONPUBLIC_ADDRESS")
    return values


async def _connect_image(delegate, target, pin, check):
    request = httpx.Request(
        "GET", target.copy_with(host=pin),
        headers={"Host": target.host, "Accept-Encoding": "identity"},
        extensions={"sni_hostname": target.host,
                    "timeout": {key: 5.0 for key in ("connect", "read", "write", "pool")}},
    )
    await check()
    dispatched = connect_failed = False

    def destination():
        if (request.method != "GET" or request.url != target.copy_with(host=pin)
            or not global_address(request.url.host) or request.headers.get("Host") != target.host
            or request.extensions.get("sni_hostname") != target.host
            or "Authorization" in request.headers or "Cookie" in request.headers):
            raise ValueError("IMAGE_DESTINATION_CHANGED")

    async def trace(name, info):
        nonlocal dispatched, connect_failed
        if name in {"connection.connect_tcp.failed", "connection.start_tls.failed"}:
            connect_failed = True
        if name.endswith("send_request_headers.started"):
            # Mark before awaiting the guard: once dispatch might have begun,
            # this request can never be repeated, including after a late error.
            dispatched = True
            await check()
            destination()

    request.extensions["trace"] = trace
    destination()
    try:
        return await delegate.handle_async_request(request)
    except (httpx.ConnectError, httpx.ConnectTimeout):
        if dispatched or not connect_failed:
            raise
        # The trusted transport proved TCP/TLS failed before HTTP headers.
        return None


async def fetch_image(image, max_bytes, *, check, transport=None, resolver=resolve_public):
    """The Core check runs after DNS, then actual destination/Host/SNI are rechecked.

    Use the transport directly: HTTPX's client request logger would print signed URLs.
    A proven pre-HTTP connect failure may try another validated address within the
    same ten-second lease. No dispatched GET is repeated; there are no redirects,
    Authorization/Cookie headers or disk writes here.
    """
    if type(max_bytes) is not int or not 1 <= max_bytes <= 4_000_000 or not callable(check):
        raise ValueError("INVALID_IMAGE_DOWNLOAD_GRANT")
    url = image_url(image.url)
    target = httpx.URL(url)
    delegate = transport or httpx.AsyncHTTPTransport(trust_env=False, retries=0)
    response = None
    quiet = _private_download.set(True)
    try:
        async with asyncio.timeout(10):
            addresses = await resolver(target.host, 443)
            if not addresses or any(not global_address(ip) for ip in addresses):
                raise ValueError("IMAGE_NONPUBLIC_ADDRESS")
            candidates = addresses[:3] if len(addresses) > 1 else addresses * 2
            for pin in candidates:
                response = await _connect_image(delegate, target, pin, check)
                if response is not None:
                    break
            if response is None:
                raise ValueError("IMAGE_CONNECTION_FAILED")
            if response.status_code != 200:
                raise ValueError("IMAGE_FETCH_REJECTED")
            if response.headers.get("content-encoding", "identity").strip().lower() != "identity":
                raise ValueError("IMAGE_ENCODING_UNSUPPORTED")
            mime = response.headers.get("content-type", "").split(";")[0].strip().lower()
            if mime not in {"image/png", "image/jpeg", "image/webp", "image/gif", "image/apng"}:
                raise ValueError("IMAGE_MIME_UNSUPPORTED")
            try:
                size = int(response.headers.get("content-length", "0"))
            except ValueError:
                raise ValueError("IMAGE_SIZE_INVALID") from None
            if not 0 <= size <= max_bytes:
                raise ValueError("IMAGE_TOO_LARGE")
            body = bytearray()
            async for chunk in response.aiter_bytes():
                body.extend(chunk)
                if len(body) > max_bytes:
                    raise ValueError("IMAGE_TOO_LARGE")
            if not body:
                raise ValueError("IMAGE_EMPTY")
            return bytes(body), mime
    except (httpx.ConnectError, httpx.ConnectTimeout):
        raise ValueError("IMAGE_CONNECTION_FAILED") from None
    except (httpx.HTTPError, OSError):
        raise ValueError("IMAGE_FETCH_FAILED") from None
    finally:
        try:
            if response is not None:
                await response.aclose()
            if transport is None:
                await delegate.aclose()
        finally:
            _private_download.reset(quiet)
