"""Ordered authenticated image batches; only synthetic CDN/model transports are used."""

import io
import json

import httpx
import pytest
from PIL import Image

from companion.model import ModelClient
from companion_onebot_bridge.event_normalizer import normalize
from companion_onebot_bridge.images import MAX_OWNER_IMAGES
from companion_onebot_bridge.window import digest
from tests.conftest import run
from tests.test_live_image_input import options
from tests.test_onebot_capture import capture_approval, source_window
from tests.test_onebot_direct import config, raw_event
from tests.test_onebot_images import image_segment


def image_inputs(count):
    return [image_segment(f"https://gchat.qpic.cn/synthetic-{index}.png") for index in range(count)]


def test_normalized_images_preserve_slots_positions_and_owner_question():
    first, second = image_inputs(2)
    raw = raw_event(message=[
        {"type": "text", "data": {"text": "比较这两张图片"}},
        first,
        {"type": "node", "data": {"content": [image_segment()]}},
        {"type": "text", "data": {"text": "，按发送顺序编号。"}},
        second,
    ])
    payload = normalize(json.dumps(raw), config(), now=raw["time"],
                        rich_input_scope="references_v1", image_scope="owner_image_v1")["payload"]
    assert payload["text"] == "比较这两张图片，按发送顺序编号。"
    assert payload["shared_content"] == [{"kind": "image", "slot": 0}, {"kind": "image", "slot": 1}]
    images = [item for item in payload["rich"]["segments"] if item["type"] == "image"]
    assert [(item["index"], item["slot"], item["source_sha256"]) for item in images] == [
        (1, 0, digest(first)), (4, 1, digest(second)),
    ]
    assert "gchat.qpic.cn" not in json.dumps(payload)


@pytest.mark.parametrize("count, failure", [
    (2, None), (MAX_OWNER_IMAGES, None), (3, "second"), (2, "policy"),
])
def test_authenticated_batch_downloads_once_in_order_and_stops_on_failure(
    core, tmp_path, count, failure,
):
    downloads, uploads, calls = [], [], []
    raw = raw_event(message=[
        {"type": "text", "data": {"text": "按顺序比较这些图片的颜色。"}}, *image_inputs(count),
    ])
    data = []
    for color in ("red", "blue", "green", "yellow")[:count]:
        out = io.BytesIO()
        Image.new("RGB", (3, 2), color).save(out, format="PNG")
        data.append(out.getvalue())

    async def source(ws):
        await ws.send(json.dumps(raw))
        await ws.wait_closed()

    async def resolve(host, port):
        assert (host, port) == ("gchat.qpic.cn", 443)
        return ["1.1.1.1"]

    def cdn(request):
        index = len(downloads)
        assert request.url.path == f"/synthetic-{index}.png"
        downloads.append(request)
        return httpx.Response(
            404 if failure and index == 1 else 200,
            content=data[index], headers={"Content-Type": "image/png"},
        )

    def model(request):
        body = json.loads(request.content)
        calls.append(body)
        return httpx.Response(200, json={
            "choices": [{"message": {"content": "图1红色，图2蓝色，颜色不同。"}}],
            "usage": {"prompt_tokens": count * 100, "completion_tokens": 20},
        })

    opts = options()
    opts["paid_options"]["image_input"] = opts["paid_options"]["image_input"].model_copy(
        update={"max_images_per_request": 1 if failure == "policy" else count},
    )

    async def scenario():
        async with (
            source_window(core, tmp_path, source, live=True, approved=True,
                          rich_input_scope="references_v1", scope_options=opts)
            as (window, client, protocol, _, sends),
            httpx.AsyncClient(transport=httpx.MockTransport(model)) as model_http,
        ):
            core.model = ModelClient(core.settings, model_http)
            core.content.options = core.settings.content

            async def observe_upload(request):
                if request.url.path.endswith("/image"):
                    index = len(uploads)
                    assert request.headers["X-Content-Key"] == f"image:{index}"
                    assert request.content == data[index]
                    proof = json.loads(request.headers["X-Content-Proof"])
                    assert proof["source_sha256"] == digest(raw["message"][index + 1])
                    uploads.append(request)

            client.event_hooks["request"].append(observe_upload)
            await window.capture_once(client, protocol)
            operation = window.approve_captured(
                client, capture_approval(window), image_resolver=resolve,
                image_transport=httpx.MockTransport(cdn),
            )
            if failure == "policy":
                with pytest.raises(httpx.HTTPStatusError):
                    await operation
                assert downloads == uploads == calls == []
                assert not core.store.one("SELECT 1 FROM model_usage")
            elif failure:
                with pytest.raises(ValueError, match="IMAGE_FETCH_REJECTED"):
                    await operation
                assert len(downloads) == 2 and len(uploads) == 1
                assert not calls and not core.store.one("SELECT 1 FROM model_usage")
                assert window.status()["state"] in {"STOPPED", "CORE_STOP_UNCONFIRMED"}
            else:
                try:
                    await operation
                except httpx.HTTPStatusError as error:
                    raise AssertionError(error.response.text) from error
                assert len(downloads) == len(uploads) == count
                assert len(calls) == 1
                parts = calls[0]["messages"][0]["content"]
                assert len([part for part in parts if part["type"] == "image_url"]) == count
                assert [row["state"] for row in core.store.rows(
                    "SELECT state FROM content_items ORDER BY slot",
                )] == ["SUCCEEDED"] * count
                assert core.store.one("SELECT COUNT(*) n FROM model_usage")["n"] == 1
            assert not sends and not core.store.one("SELECT 1 FROM outbox")

    run(scenario())
