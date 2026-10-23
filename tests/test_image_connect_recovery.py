"""Only proven TCP/TLS failures before any GET dispatch permit another connection."""

import json

import httpx
import pytest

from companion_onebot_bridge import capture, images
from tests.conftest import run
from tests.test_capture_diagnostics import cli_failure
from tests.test_live_image_input import options
from tests.test_onebot_capture import capture_approval, source_window
from tests.test_onebot_direct import raw_event
from tests.test_onebot_images import URL, image_segment


class ConnectThenGet(httpx.AsyncBaseTransport):
    def __init__(self, delegate, *, failure="tcp", addresses=None):
        self.delegate, self.failure = delegate, failure
        self.addresses = addresses if addresses is not None else []
        self.gets = 0

    async def handle_async_request(self, request):
        self.addresses.append(request.url.host)
        trace = request.extensions["trace"]
        if len(self.addresses) == 1:
            if self.failure in {"tcp", "tls"}:
                name = "connect_tcp" if self.failure == "tcp" else "start_tls"
                await trace("connection." + name + ".failed", {})
            elif self.failure == "dispatched":
                await trace("http11.send_request_headers.started", {})
                self.gets += 1
            raise httpx.ConnectError(URL, request=request)
        await trace("http11.send_request_headers.started", {})
        self.gets += 1
        return await self.delegate.handle_async_request(request)


@pytest.mark.parametrize("failure", ["tcp", "tls", "dispatched", "unproven"])
def test_connect_recovery_requires_transport_proof_before_any_get(caplog, failure):
    checked, addresses = [], []

    async def resolve(*_):
        return ["1.1.1.1", "8.8.8.8"]

    async def check():
        checked.append(True)

    transport = ConnectThenGet(httpx.MockTransport(lambda _: httpx.Response(
        200, content=b"GIF89a", headers={"Content-Type": "image/gif"},
    )), failure=failure, addresses=addresses)
    call = images.fetch_image(images.original_images([image_segment()])[0], 100,
                              check=check, resolver=resolve, transport=transport)
    if failure in {"tcp", "tls"}:
        assert run(call) == (b"GIF89a", "image/gif")
        assert addresses == ["1.1.1.1", "8.8.8.8"] and transport.gets == 1
        assert len(checked) == 3  # Two lease checks and the one actual header dispatch.
    else:
        with pytest.raises(ValueError, match="IMAGE_CONNECTION_FAILED"):
            run(call)
        assert addresses == ["1.1.1.1"]
    assert URL not in caplog.text


def test_changed_source_blocks_reconnect_before_another_connection():
    addresses = []

    async def resolve(*_):
        return ["1.1.1.1", "8.8.8.8"]

    async def check():
        if addresses:
            raise ValueError("IMAGE_SOURCE_CHANGED")

    transport = ConnectThenGet(httpx.MockTransport(lambda _: pytest.fail("GET not permitted")),
                               addresses=addresses)
    with pytest.raises(ValueError, match="IMAGE_SOURCE_CHANGED"):
        run(images.fetch_image(images.original_images([image_segment()])[0], 100,
                               check=check, resolver=resolve, transport=transport))
    assert addresses == ["1.1.1.1"] and transport.gets == 0


def test_actual_httpcore_connect_failure_trace_recovers_without_repeating_get():
    import httpcore
    from httpcore._backends.mock import AsyncMockBackend

    addresses, checks = [], []

    class Backend(AsyncMockBackend):
        async def connect_tcp(self, host, port, **kwargs):
            addresses.append(host)
            if len(addresses) == 1:
                raise httpcore.ConnectError("synthetic TCP refusal")
            return await super().connect_tcp(host, port, **kwargs)

    async def resolve(*_):
        return ["1.1.1.1", "8.8.8.8"]

    async def check():
        checks.append(True)

    async def scenario():
        async with httpx.AsyncHTTPTransport(trust_env=False, retries=0) as transport:
            transport._pool._network_backend = Backend([
                b"HTTP/1.1 200 OK\r\nContent-Type: image/gif\r\nContent-Length: 6\r\n\r\nGIF89a",
            ])
            assert await images.fetch_image(images.original_images([image_segment()])[0],
                100, check=check, resolver=resolve, transport=transport) == (b"GIF89a", "image/gif")

    run(scenario())
    assert addresses == ["1.1.1.1", "8.8.8.8"] and len(checks) == 3


def test_image_download_cli_failure_preserves_safe_stage_without_the_signed_url(
    core, tmp_path, monkeypatch, capsys,
):
    async def source(ws):
        await ws.send(json.dumps(raw_event(message=[image_segment()])))
        await ws.wait_closed()

    async def resolve(*_):
        return ["1.1.1.1"]

    def refused(request):
        raise httpx.ConnectError(URL, request=request)

    async def scenario():
        async with source_window(core, tmp_path, source, live=True, approved=True,
                                 rich_input_scope="references_v1", scope_options=options()) as (
            window, client, protocol, _, sends,
        ):
            await window.capture_once(client, protocol)
            with pytest.raises(ValueError) as caught:
                await window.approve_captured(client, capture_approval(window),
                    image_transport=httpx.MockTransport(refused), image_resolver=resolve)
            assert capture.diagnostic_fields(caught.value) == {
                "capture_phase": "image_download", "capture_reason": "CAPTURE_CONNECTION_FAILED",
            }
            assert core.model.calls == sends == []
            return caught.value

    output, value = cli_failure(monkeypatch, capsys, run(scenario()))
    assert value["capture_phase"] == "image_download"
    assert value["capture_reason"] == "CAPTURE_CONNECTION_FAILED"
    assert URL not in output and "rkey" not in output
