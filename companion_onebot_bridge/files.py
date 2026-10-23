"""Source-bound private QQ file bytes; parsing/execution belongs outside Bridge.

Pinned SnowLuma db142db9e6398c779283eab9afc3552da1e2c361:
event-converter/element-codecs.ts:497-519 defines the received file segment;
actions/group-file.ts:329-357 defines get_private_file_url and its {url} result;
protocol/oidb-services/group-file/get-private-file-url.ts:49-56 returns a native
http://server:port URL. Only that authenticated action can supply a destination.
"""

import asyncio
import hashlib
import ipaddress
import re
from dataclasses import dataclass
from urllib.parse import urlsplit

import httpx

from .event_normalizer import decode, normalize
from .images import _private_download, global_address, resolve_public
from .network import CheckedTransport, checked_http_client, private_request_matches
from .onebot_client import bounded_post
from .spool import encode

MAX_FILE_BYTES = 128 * 1024 * 1024
MAX_OWNER_FILES = 64
TIMEOUT_SECONDS = 10
ACTION = "/get_private_file_url"
MIMES = frozenset({
    "application/octet-stream", "application/pdf", "application/msword", "text/plain",
    "text/markdown", "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
})


def _opaque(value, maximum, *, empty=False):
    return (isinstance(value, str) and (empty or bool(value.strip()))
            and len(value) <= maximum
            and not re.search(r"[\x00-\x1f\x7f-\x9f]", value)
            and not any(0xD800 <= ord(char) <= 0xDFFF for char in value))


@dataclass(frozen=True, repr=False)
class OriginalFile:
    index: int
    slot: int
    source_sha256: str
    file_id: str
    filename: str
    size: int | None
    file_hash: str = ""

    def __post_init__(self):
        if (type(self.index) is not int or not 0 <= self.index < 64
            or type(self.slot) is not int or not 0 <= self.slot <= self.index
            or not isinstance(self.source_sha256, str)
            or re.fullmatch(r"[0-9a-f]{64}", self.source_sha256) is None
            or not _opaque(self.file_id, 512)
            or not _opaque(self.filename, 255) or self.filename in {".", ".."}
            or any(char in self.filename for char in "\\/:")
            or not _opaque(self.file_hash, 1024, empty=True)
            or (self.size is not None
                and (type(self.size) is not int or not 1 <= self.size <= 2**53 - 1))):
            raise ValueError("INVALID_FILE_SEGMENT")

    def projection(self):
        return {"index": self.index, "type": "file", "actor": "UNVERIFIED",
                "coverage": "UNREAD", "slot": self.slot, "file_id": self.file_id,
                "filename": self.filename, "size": self.size,
                "source_sha256": self.source_sha256}


@dataclass(frozen=True, repr=False)
class DownloadedFile:
    original: OriginalFile
    body: bytes
    mime: str
    media_sha256: str

    def metadata(self):
        return {"slot": self.original.slot, "source_sha256": self.original.source_sha256,
                "file_id": self.original.file_id, "filename": self.original.filename,
                "size": self.original.size, "bytes": len(self.body), "mime": self.mime,
                "media_sha256": self.media_sha256}


def _alias(data, first, second, *, default=None):
    if first in data and second in data and (
        type(data[first]) is not type(data[second]) or data[first] != data[second]
    ):
        raise ValueError("FILE_ALIAS_MISMATCH")
    return data.get(first, data.get(second, default))


def original_files(message):
    """Caller authenticates owner/friend envelope; never follow quoted descendants."""
    if isinstance(message, str):
        return []
    if not isinstance(message, list) or not 1 <= len(message) <= 64:
        raise ValueError("INVALID_FILE_MESSAGE")
    files = []
    for index, segment in enumerate(message):
        if not isinstance(segment, dict) or segment.get("type") != "file":
            continue
        data = segment.get("data")
        if (set(segment) != {"type", "data"} or not isinstance(data, dict)
            or not set(data) <= {"file", "file_id", "file_size", "name", "id", "size",
                                 "url", "file_hash"}
            or ("url" in data and not _opaque(data["url"], 4096, empty=True))):
            raise ValueError("INVALID_FILE_SEGMENT")
        size = _alias(data, "file_size", "size", default=0)
        if type(size) is not int or not 0 <= size <= 2**53 - 1:
            raise ValueError("INVALID_FILE_SIZE")
        files.append(OriginalFile(
            index, len(files), hashlib.sha256(encode(segment).encode()).hexdigest(),
            _alias(data, "file_id", "id"), _alias(data, "file", "name"), size or None,
            data.get("file_hash", ""),
        ))
    return files


def owner_files(frame, config, *, now=None):
    """Optional raw-frame entry point with the existing owner/private authentication."""
    event = normalize(frame, config, now=now, rich_input_scope="references_v1")
    if event is None:
        return []
    return original_files(decode(frame, config.max_frame_bytes, max_depth=20)["message"])


def _destination(value, allowed_hosts, allowed_ports):
    if (not _opaque(value, 4096) or "\\" in value
        or any(ord(char) <= 32 for char in value)):
        raise ValueError("FILE_URL_NOT_ALLOWED")
    try:
        url = urlsplit(value)
        port = url.port or (443 if url.scheme == "https" else 80)
        if (url.scheme not in {"http", "https"} or not url.hostname
            or not url.hostname.isascii() or url.username is not None
            or url.password is not None or url.fragment or "#" in value
            or not url.path.startswith("/") or url.path.startswith("//")
            or not 1 <= port <= 65535 or url.port == 0
            or (allowed_hosts is not None and url.hostname not in allowed_hosts)
            or (allowed_ports is not None and port not in allowed_ports)):
            raise ValueError()
        # HTTPX must not reinterpret authority, path or query while building the GET.
        target = httpx.URL(value)
        if (target.host != url.hostname
            or (target.port or (443 if target.scheme == "https" else 80)) != port
            or target.raw_path != (url.path + ("?" + url.query if url.query else "")).encode()):
            raise ValueError()
        return target
    except (ValueError, httpx.InvalidURL, UnicodeError):
        raise ValueError("FILE_URL_NOT_ALLOWED") from None


async def _native_url(original, onebot, check, synthetic):
    """Borrow the authenticated client, preserving its final window/network guards."""
    config = onebot.config
    body = {"user_id": int(config.owner_qq_id), "file_id": original.file_id,
            "file_hash": original.file_hash}

    async def boundary(request):
        await check()
        content = request.content
        if (request.method != "POST"
            or not private_request_matches(request, config.onebot_http_url, ACTION,
                                           synthetic=synthetic)
            or request.headers.get("Authorization") != "Bearer " + config.onebot_token
            or "Cookie" in request.headers or "Transfer-Encoding" in request.headers
            or decode(content, 16384) != body):
            raise ValueError("FILE_ACTION_CHANGED")
        # HTTPX dispatches stream, not the cached content inspected above.
        request.stream = httpx.ByteStream(content)
        request.headers["Content-Length"] = str(len(content))

    await check()
    async with checked_http_client(onebot.client, boundary) as client:
        result = await bounded_post(client, ACTION, body)
    await check()
    if (result.get("status") != "ok" or type(result.get("retcode")) is not int
        or result["retcode"] != 0 or not isinstance(result.get("data"), dict)
        or set(result["data"]) != {"url"}):
        raise ValueError("FILE_URL_RESOLUTION_FAILED")
    return result["data"]["url"]


async def _get(delegate, target, address, check):
    request = httpx.Request(
        "GET", target.copy_with(host=address),
        headers={"Host": target.netloc.decode("ascii"), "Accept-Encoding": "identity"},
        extensions={"sni_hostname": target.host,
                    "timeout": {key: 5.0 for key in ("connect", "read", "write", "pool")}},
    )

    async def boundary(actual):
        await check()
        if (actual.method != "GET" or actual.url != target.copy_with(host=address)
            or not global_address(actual.url.host)
            or actual.headers.get("Host") != target.netloc.decode("ascii")
            or actual.extensions.get("sni_hostname") != target.host
            or actual.headers.get("Accept-Encoding") != "identity"
            or "Authorization" in actual.headers or "Cookie" in actual.headers
            or actual.content):
            raise ValueError("FILE_DESTINATION_CHANGED")

    async def trace(name, info):
        if name.endswith("send_request_headers.started"):
            await boundary(request)

    request.extensions["trace"] = trace
    return await CheckedTransport(delegate, boundary).handle_async_request(request)


async def fetch_file(original, max_bytes, *, onebot, check, transport=None,
                     resolver=resolve_public, synthetic=False, allowed_hosts=None,
                     allowed_ports=None):
    """One authenticated native URL lookup and one GET, with no retries or disk writes.

    ``check`` must verify the current source and Core lease, including recall/erasure.
    The private native-action result grants its public endpoint; optional exact host
    and port sets can narrow it. No message URL, caller URL or model action is accepted.
    ``synthetic`` only permits a mock loopback OneBot client, never a private download.
    Returned bytes are untrusted document content, not executable instructions.
    """
    if (not isinstance(original, OriginalFile) or type(max_bytes) is not int
        or not 1 <= max_bytes <= MAX_FILE_BYTES or not callable(check)
        or type(synthetic) is not bool
        or (allowed_hosts is not None and (
            not isinstance(allowed_hosts, (set, frozenset, tuple, list))
            or any(not _opaque(host, 253) for host in allowed_hosts)))
        or (allowed_ports is not None and (
            not isinstance(allowed_ports, (set, frozenset, tuple, list))
            or any(type(port) is not int or not 1 <= port <= 65535 for port in allowed_ports)))):
        raise ValueError("INVALID_FILE_DOWNLOAD_GRANT")
    if original.size is not None and original.size > max_bytes:
        raise ValueError("FILE_TOO_LARGE")
    delegate = transport or httpx.AsyncHTTPTransport(trust_env=False, retries=0)
    response = None
    delegate_closed = False
    quiet = _private_download.set(True)
    try:
        async with asyncio.timeout(TIMEOUT_SECONDS):
            target = _destination(await _native_url(original, onebot, check, synthetic),
                                  allowed_hosts, allowed_ports)
            try:
                ipaddress.ip_address(target.host)
            except ValueError:
                try:
                    addresses = await resolver(
                        target.host, target.port or (443 if target.scheme == "https" else 80))
                except ValueError as error:
                    if error.args == ("IMAGE_NONPUBLIC_ADDRESS",):
                        raise ValueError("FILE_NONPUBLIC_ADDRESS") from None
                    raise
            else:
                addresses = [target.host]
            if not addresses or any(not global_address(address) for address in addresses):
                raise ValueError("FILE_NONPUBLIC_ADDRESS")
            response = await _get(delegate, target, addresses[0], check)
            await check()
            if 300 <= response.status_code <= 399:
                raise ValueError("FILE_REDIRECT_REFUSED")
            if response.status_code != 200:
                raise ValueError("FILE_FETCH_REJECTED")
            if response.headers.get("content-encoding", "identity").strip().lower() != "identity":
                raise ValueError("FILE_ENCODING_UNSUPPORTED")
            header = response.headers.get("content-type", "application/octet-stream")
            mime = header.split(";", 1)[0].strip().lower()
            if not _opaque(header, 256) or mime not in MIMES:
                raise ValueError("FILE_MIME_UNSUPPORTED")
            declared = response.headers.get("content-length")
            if declared is not None:
                if re.fullmatch(r"[0-9]{1,16}", declared) is None:
                    raise ValueError("FILE_SIZE_INVALID")
                declared = int(declared)
                if declared > max_bytes:
                    raise ValueError("FILE_TOO_LARGE")
            body = bytearray()
            async for chunk in response.aiter_bytes():
                if len(chunk) > max_bytes - len(body):
                    raise ValueError("FILE_TOO_LARGE")
                body.extend(chunk)
            # Close before the final source/lease check: no successful return can
            # suspend on cleanup after that last revocation boundary.
            await response.aclose()
            response = None
            if transport is None:
                await delegate.aclose()
                delegate_closed = True
            await check()
            if not body:
                raise ValueError("FILE_EMPTY")
            if ((declared is not None and len(body) != declared)
                or (original.size is not None and len(body) != original.size)):
                raise ValueError("FILE_SIZE_MISMATCH")
            data = bytes(body)
            return DownloadedFile(original, data, mime, hashlib.sha256(data).hexdigest())
    except (TimeoutError, httpx.TimeoutException):
        raise ValueError("FILE_FETCH_TIMEOUT") from None
    except httpx.ConnectError:
        raise ValueError("FILE_CONNECTION_FAILED") from None
    except (httpx.HTTPError, OSError):
        raise ValueError("FILE_FETCH_FAILED") from None
    finally:
        try:
            if response is not None:
                await response.aclose()
            if transport is None and not delegate_closed:
                await delegate.aclose()
        finally:
            _private_download.reset(quiet)
