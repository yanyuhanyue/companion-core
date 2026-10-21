"""No CDN/socket/provider I/O: raw image source fixtures and HTTP MockTransport."""

import logging

import httpx
import pytest

from companion_onebot_bridge import images
from companion_onebot_bridge.window import digest
from tests.conftest import run

URL = "https://multimedia.nt.qq.com.cn/download?appid=1407&rkey=synthetic-private-reference"


def test_real_httpcore_trace_does_not_log_signed_redirect_header(caplog):
    from httpcore._backends.mock import AsyncMockBackend

    async def scenario():
        async with httpx.AsyncHTTPTransport(trust_env=False, retries=0) as transport:
            transport._pool._network_backend = AsyncMockBackend(
                [
                    b"HTTP/1.1 302 Found\r\nLocation: "
                    + URL.encode()
                    + b"\r\nContent-Length: 0\r\n\r\n",
                ]
            )

            async def resolve(host, port):
                return ["1.1.1.1"]

            async def check():
                return None

            with pytest.raises(ValueError, match="IMAGE_FETCH_REJECTED"):
                await images.fetch_image(
                    images.original_image([image_segment()]),
                    100,
                    check=check,
                    resolver=resolve,
                    transport=transport,
                )
            # Filtering is local to the download task, not a global logger disable.
            logging.getLogger("httpcore.http11").debug("public-trace-still-visible")

    with caplog.at_level(logging.DEBUG, logger="httpcore"):
        run(scenario())
    assert "public-trace-still-visible" in caplog.text
    assert URL not in caplog.text and "synthetic-private-reference" not in caplog.text


def image_segment(url=URL):
    return {"type": "image", "data": {"url": url, "file": "synthetic-file", "summary": ""}}


def test_only_one_top_level_image_and_source_hash_without_url_projection():
    raw = image_segment()
    value = images.original_image(
        [
            {"type": "reply", "data": {"id": "42"}},
            {"type": "text", "data": {"text": "owner text"}},
            raw,
            {"type": "node", "data": {"content": [image_segment()]}},
        ]
    )
    assert value.index == 2 and value.source_sha256 == digest(raw)
    assert value.projection() == {
        "index": 2,
        "type": "image",
        "actor": "UNVERIFIED",
        "coverage": "UNREAD",
        "slot": 0,
        "source_sha256": digest(raw),
    }
    assert URL not in repr(value) and URL not in repr(value.projection())
    assert images.original_image([{"type": "node", "data": {"content": [raw]}}]) is None
    assert images.original_image("[CQ:image,url=forged]") is None
    with pytest.raises(ValueError, match="ONE_OWNER_IMAGE_REQUIRED"):
        images.original_image([raw, raw])


@pytest.mark.parametrize(
    "url",
    [
        "https://multimedia.nt.qq.com.cn.evil.invalid/x",
        "file:///private",
        "https://multimedia.nt.qq.com.cn@127.0.0.1/x",
        "https://user:secret@gchat.qpic.cn/x",
        "https://gchat.qpic.cn:444/x",
        "https://gchat.qpic.cn/x#secret",
        "https://gchat.qpic.cn\\@evil.invalid/x",
        "https://gchat.qpic.cn/x\x00secret",
        "https://127.0.0.1/x",
        "data:image/png;base64,private",
    ],
)
def test_unknown_or_confused_image_destinations_are_rejected_before_io(url):
    with pytest.raises(ValueError, match="IMAGE_URL_NOT_ALLOWED"):
        images.original_image([image_segment(url)])


@pytest.mark.parametrize(
    "addresses",
    [[], ["127.0.0.1"], ["1.1.1.1", "10.0.0.1"], ["::1"], ["224.0.0.1"], ["::ffff:127.0.0.1"]],
)
def test_dns_private_or_mixed_answers_never_get(addresses):
    calls = []

    async def resolve(host, port):
        return addresses

    async def check():
        calls.append("check")

    def get(request):
        calls.append("GET")
        return httpx.Response(200, content=b"fake", headers={"content-type": "image/png"})

    with pytest.raises(ValueError, match="IMAGE_NONPUBLIC_ADDRESS"):
        run(
            images.fetch_image(
                images.original_image([image_segment()]),
                100,
                check=check,
                resolver=resolve,
                transport=httpx.MockTransport(get),
            )
        )
    assert calls == []


@pytest.mark.parametrize(
    "failure", [None, "source", "redirect", "bytes", "mime", "encoding", "error"]
)
def test_single_bounded_get_is_pinned_source_checked_and_never_logs_url(caplog, failure):
    calls = []
    caplog.set_level(logging.DEBUG)

    async def resolve(host, port):
        assert (host, port) == ("multimedia.nt.qq.com.cn", 443)
        calls.append("DNS")
        return ["1.1.1.1"]

    async def check():
        calls.append("check")
        if failure == "source":
            raise ValueError("IMAGE_SOURCE_CHANGED")

    def get(request):
        calls.append("GET")
        assert request.url.host == "1.1.1.1"
        assert request.headers["Host"] == "multimedia.nt.qq.com.cn"
        assert request.extensions["sni_hostname"] == "multimedia.nt.qq.com.cn"
        assert "Authorization" not in request.headers and "Cookie" not in request.headers
        if failure == "error":
            raise httpx.ConnectError(URL)
        headers = {"Content-Type": "image/png"}
        if failure == "mime":
            headers["Content-Type"] = "text/html"
        if failure == "encoding":
            headers["Content-Encoding"] = "gzip"
        return httpx.Response(
            302 if failure == "redirect" else 200,
            headers=headers,
            content=b"0123456789" if failure == "bytes" else b"fake",
        )

    invocation = images.fetch_image(
        images.original_image([image_segment()]),
        5,
        check=check,
        resolver=resolve,
        transport=httpx.MockTransport(get),
    )
    if failure:
        with pytest.raises(ValueError) as error:
            run(invocation)
        assert URL not in str(error.value)
    else:
        assert run(invocation) == (b"fake", "image/png")
    assert calls == (["DNS", "check"] if failure == "source" else ["DNS", "check", "GET"])
    assert URL not in caplog.text and "synthetic-private-reference" not in caplog.text
