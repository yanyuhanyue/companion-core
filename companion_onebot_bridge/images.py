"""One current-owner image, transient URL, and one source-checked bounded GET."""

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
    source_sha256: str
    url: str

    def projection(self):
        return {
            "index": self.index,
            "type": "image",
            "actor": "UNVERIFIED",
            "coverage": "UNREAD",
            "slot": 0,
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


def original_image(message):
    """Caller has authenticated the envelope; never walk node/reply/card descendants."""
    if isinstance(message, str):
        return None
    if not isinstance(message, list) or not 1 <= len(message) <= 64:
        raise ValueError("INVALID_IMAGE_MESSAGE")
    images = [
        (i, value)
        for i, value in enumerate(message)
        if isinstance(value, dict) and value.get("type") == "image"
    ]
    # Even duplicate images count: do not silently drop a second owner image.
    if len(images) > 1:
        raise ValueError("ONE_OWNER_IMAGE_REQUIRED")
    if not images:
        return None
    index, value = images[0]
    data = value.get("data")
    if set(value) != {"type", "data"} or not isinstance(data, dict):
        raise ValueError("INVALID_IMAGE_SEGMENT")
    # The pinned SnowLuma receive codec emits these fields for an ordinary image.
    # Sticker subtypes, flash pictures and market-face extensions are different
    # media scopes even when they happen to point at an otherwise allowed CDN.
    if (
        not set(data) <= {"url", "file", "sub_type", "summary"}
        or type(data.get("sub_type", 0)) is not int
        or data.get("sub_type", 0) != 0
        or any(
            not isinstance(data[key], str) or (key != "url" and "\x00" in data[key])
            for key in ("url", "file", "summary") if key in data
        )
    ):
        raise ValueError("IMAGE_KIND_UNSUPPORTED")
    url = image_url(data.get("url") or data.get("file"))
    return OriginalImage(index, hashlib.sha256(encode(value).encode()).hexdigest(), url)


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


async def fetch_image(image, max_bytes, *, check, transport=None, resolver=resolve_public):
    """The Core check runs after DNS, then actual destination/Host/SNI are rechecked.

    Use the transport directly: HTTPX's client request logger would print signed URLs.
    No Authorization/Cookie headers, redirect following, retries or disk writes exist here.
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
            pin = addresses[0]
            request = httpx.Request(
                "GET",
                target.copy_with(host=pin),
                headers={
                    "Host": target.host,
                    "Accept-Encoding": "identity",
                },
                extensions={
                    "sni_hostname": target.host,
                    "timeout": {key: 5.0 for key in ("connect", "read", "write", "pool")},
                },
            )
            await check()

            def destination():
                if (
                    request.method != "GET"
                    or request.url != target.copy_with(host=pin)
                    or not global_address(request.url.host)
                    or request.headers.get("Host") != target.host
                    or request.extensions.get("sni_hostname") != target.host
                    or "Authorization" in request.headers
                    or "Cookie" in request.headers
                ):
                    raise ValueError("IMAGE_DESTINATION_CHANGED")

            async def trace(name, info):
                if name.endswith("send_request_headers.started"):
                    # Connect/TLS may await. Recheck the same lease/source immediately
                    # before the actual GET headers, without another download attempt.
                    await check()
                    destination()

            request.extensions["trace"] = trace
            destination()
            response = await delegate.handle_async_request(request)
            if response.status_code != 200:
                raise ValueError("IMAGE_FETCH_REJECTED")
            if response.headers.get("content-encoding", "identity").strip().lower() != "identity":
                raise ValueError("IMAGE_ENCODING_UNSUPPORTED")
            mime = response.headers.get("content-type", "").split(";")[0].strip().lower()
            if mime not in {"image/png", "image/jpeg", "image/webp"}:
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
