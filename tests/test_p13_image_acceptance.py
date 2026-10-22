"""Partial CDN bodies must stop the existing image pipeline before paid I/O."""

import json
import logging

import httpx
import pytest

from companion.model import ModelClient
from tests.conftest import run
from tests.test_live_image_input import options, png
from tests.test_onebot_capture import capture_approval, source_window
from tests.test_onebot_direct import raw_event
from tests.test_onebot_images import URL, image_segment


@pytest.mark.parametrize("failure", ["read_timeout", "stream_over_limit"])
def test_partial_download_is_closed_once_without_model_or_fallback_reply(
    core, tmp_path, caplog, failure,
):
    downloads, calls, closed = [], [], []
    caplog.set_level(logging.DEBUG)

    class PartialBody(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield png()[:16]
            if failure == "read_timeout":
                raise httpx.ReadTimeout(URL)
            # The actual stream exceeds the approved 50,000 bytes, despite a small header.
            yield b"x" * 50000
            raise AssertionError("The rejected image stream must not be consumed further")

        async def aclose(self):
            closed.append(True)

    async def source(ws):
        await ws.send(json.dumps(raw_event(message=[image_segment()])))
        await ws.wait_closed()

    def cdn(request):
        downloads.append(request)
        return httpx.Response(200, stream=PartialBody(), headers={
            "Content-Type": "image/png", "Content-Length": "100",
        })

    def model(request):
        calls.append(request)
        raise AssertionError("An incomplete image must never reach the provider")

    async def resolve(host, port):
        return ["1.1.1.1"]

    async def scenario():
        async with (
            source_window(
                core, tmp_path, source, live=True, approved=True,
                rich_input_scope="references_v1", scope_options=options(),
            ) as (window, client, protocol, _, sends),
            httpx.AsyncClient(transport=httpx.MockTransport(model)) as model_http,
        ):
            core.model = ModelClient(core.settings, model_http)
            core.content.options = core.settings.content
            await window.capture_once(client, protocol)
            approval = capture_approval(window)
            with pytest.raises(ValueError, match=(
                "IMAGE_FETCH_FAILED" if failure == "read_timeout" else "IMAGE_TOO_LARGE"
            )):
                await window.approve_captured(
                    client, approval, image_transport=httpx.MockTransport(cdn),
                    image_resolver=resolve,
                )
            assert window.status()["state"] in {"STOPPED", "CORE_STOP_UNCONFIRMED"}
            assert len(downloads) == 1 and closed == [True]
            assert calls == sends == []
            assert not core.store.one("SELECT 1 FROM model_usage")
            assert not core.store.one("SELECT 1 FROM model_admissions")
            assert not core.store.one("SELECT 1 FROM observations")
            assert not core.store.one("SELECT 1 FROM outbox")
            assert core.store.one("SELECT COUNT(*) n FROM content_items")["n"] == 1
            with pytest.raises((ValueError, httpx.HTTPStatusError)):
                await window.approve_captured(client, approval)
            assert len(downloads) == 1 and calls == sends == []

    run(scenario())
    assert URL not in caplog.text and "synthetic-private-reference" not in caplog.text
