"""Authenticated loopback capture, native content/paid/chat pipeline, mocked CDN/provider."""

import io
import json

import httpx
import pytest
from PIL import Image

from companion import budget, live_text, paid_budget
from companion.config import ObservationAdapterConfig
from companion.contracts import CoreError
from companion.image_input import ImageInputPolicy
from companion.media_config import ContentConfig
from companion.model import ModelClient
from companion.store import encode
from tests.conftest import run
from tests.test_onebot_capture import capture_approval, source_window
from tests.test_onebot_direct import raw_event
from tests.test_onebot_images import image_segment
from tests.test_onebot_window import reply_approval


def png():
    out = io.BytesIO()
    Image.new("RGB", (3, 2), "blue").save(out, format="PNG")
    return out.getvalue()


def options():
    image = ImageInputPolicy(
        approval_sha256="e" * 64,
        estimated_tokens_per_image=100,
        max_bytes_per_image=50000,
        max_pixels_per_image=100,
    )
    return {
        "image_scope": "owner_image_v1",
        "image_approval_sha256": image.approval_sha256,
        "core_options": {
            "content_enabled": True,
            "observation_enabled": True,
            "content": ContentConfig(use_chat_model_for_vision=True, image_daily_micro_usd=10000),
            "observation_adapters": {
                "content_image": ObservationAdapterConfig(
                    token="synthetic-image-adapter-token-12345",
                    source="qq_image",
                    media_analysis=True,
                )
            },
        },
        "paid_options": {"per_request_calls": 2, "image_input": image},
    }


@pytest.mark.parametrize(
    "drift", [None, "media", "observation", "admission", "source", "context_payload"]
)
def test_original_image_vision_then_chat_and_source_fenced_before_send(core, tmp_path, drift):
    calls, downloads = [], []
    raw = raw_event(
        message=[
            {
                "type": "text",
                "data": {"text": "看看这张图 https://www.bilibili.com/video/BV1xx411c7mD"},
            },
            image_segment(),
        ]
    )

    async def source(ws):
        await ws.send(json.dumps(raw))
        await ws.wait_closed()

    def model(request):
        body = json.loads(request.content)
        calls.append(body)
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": "图片中有蓝色区域" if len(calls) == 1 else "我看到蓝色。"
                        }
                    }
                ],
                "usage": {"prompt_tokens": 100, "completion_tokens": 20},
            },
        )

    def cdn(request):
        downloads.append(request)
        return httpx.Response(200, content=png(), headers={"Content-Type": "image/png"})

    async def resolve(host, port):
        return ["1.1.1.1"]

    async def scenario():
        async with (
            source_window(
                core,
                tmp_path,
                source,
                live=True,
                approved=True,
                rich_input_scope="references_v1",
                scope_options=options(),
            ) as (
                window,
                client,
                protocol,
                _,
                sends,
            ),
            httpx.AsyncClient(transport=httpx.MockTransport(model)) as model_http,
        ):

            async def read_response(response):
                await response.aread()

            client.event_hooks["response"].append(read_response)
            core.model = ModelClient(core.settings, model_http)
            # Core was constructed before this synthetic configuration was materialized.
            core.content.options = core.settings.content
            await window.capture_once(client, protocol)
            assert not core.store.one("SELECT 1 FROM events")
            try:
                await window.approve_captured(
                    client,
                    capture_approval(window),
                    image_transport=httpx.MockTransport(cdn),
                    image_resolver=resolve,
                )
            except httpx.HTTPStatusError as error:
                raise AssertionError(error.response.text) from error
            assert len(calls) == len(downloads) == 1
            assert core.store.one("SELECT state FROM content_items")["state"] == "SUCCEEDED"
            assert core.store.one("SELECT COUNT(*) n FROM content_items")["n"] == 1
            assert not core.store.one("SELECT 1 FROM exploration_requests")
            assert core.store.one("SELECT state FROM jobs")["state"] == "QUEUED"
            assert core.store.one("SELECT state FROM model_usage")["state"] == "SETTLED"
            assert await core.run_once()
            assert len(calls) == 2, core.store.one("SELECT last_error_code FROM jobs")
            assert calls[0]["messages"][0]["content"][1]["image_url"]["url"].startswith(
                "data:image/png;"
            )
            assert calls[1]["messages"][-1]["content"][1] == (
                calls[0]["messages"][0]["content"][1]
            )
            assert any("图片中有蓝色区域" in item["content"] for item in calls[1]["messages"])
            assert [
                r["scenario"]
                for r in core.store.rows("SELECT scenario FROM model_usage ORDER BY rowid")
            ] == ["content_vision", "chat"]
            job = core.store.one("SELECT * FROM jobs")
            context = json.loads(
                core.store.one(
                    "SELECT value FROM runtime_meta WHERE key=?",
                    (live_text.CONTEXT + job["job_id"],),
                )["value"]
            )
            assert len(context["image_sources"]) == 1
            if drift == "media":
                core.store.execute("UPDATE content_items SET media_sha256=?", ("0" * 64,))
            elif drift == "observation":
                core.store.execute(
                    "UPDATE events SET body_json=? WHERE source='observation.qq_image'",
                    (encode({"text": "forged"}),),
                )
            elif drift == "admission":
                core.store.execute(
                    "UPDATE model_admissions SET payload_sha256=? WHERE request_id IN "
                    "(SELECT request_id FROM model_usage WHERE scenario='content_vision')",
                    ("0" * 64,),
                )
            elif drift == "context_payload":
                context["payload_sha256"] = "0" * 64
                core.store.execute(
                    "UPDATE runtime_meta SET value=? WHERE key=?",
                    (encode(context), live_text.CONTEXT + job["job_id"]),
                )
            elif drift == "source":
                event = core.store.one("SELECT * FROM events WHERE source='qq.onebot'")
                body = json.loads(event["body_json"])
                body["rich"]["segments"][0]["source_sha256"] = "0" * 64
                core.store.execute(
                    "UPDATE events SET body_json=? WHERE event_id=?",
                    (encode(body), event["event_id"]),
                )
            if drift:
                with pytest.raises((ValueError, httpx.HTTPStatusError)):
                    await window.send_once(client, protocol, reply_approval(window.plan, core))
                assert sends == []
            else:
                status = await window.send_once(client, protocol, reply_approval(window.plan, core))
                assert status["attempt_states"] == {"ACK": 1} and len(sends) == 1
                assert paid_budget.journal(core.store, core.settings.paid_model)["head_sha256"]

    run(scenario())


@pytest.mark.parametrize("second", ["quoted_only", "image"])
def test_current_image_scope_has_no_hidden_share_history_or_extra_call_allowance(
    core,
    tmp_path,
    second,
):
    number, calls, downloads = 0, [], []

    async def source(ws):
        nonlocal number
        number += 1
        message = [image_segment()]
        if number == 2 and second == "quoted_only":
            message = [
                {"type": "reply", "data": {"id": "1"}},
                {"type": "node", "data": {"content": [image_segment()]}},
                {"type": "text", "data": {"text": "当前纯文字"}},
            ]
        await ws.send(json.dumps(raw_event(number, message=message)))
        await ws.wait_closed()

    def model(request):
        body = json.loads(request.content)
        calls.append(body)
        vision = isinstance(body["messages"][0]["content"], list)
        return httpx.Response(
            200,
            json={
                "choices": [
                    {"message": {"content": "UNIQUE_IMAGE_ANALYSIS" if vision else "合成回复"}}
                ],
                "usage": {"prompt_tokens": 100, "completion_tokens": 20},
            },
        )

    def cdn(request):
        downloads.append(request)
        return httpx.Response(200, content=png(), headers={"Content-Type": "image/png"})

    async def resolve(host, port):
        return ["1.1.1.1"]

    async def scenario():
        async with (
            source_window(
                core,
                tmp_path,
                source,
                live=True,
                approved=True,
                rich_input_scope="references_v1",
                scope_options=options(),
            ) as (
                window,
                client,
                protocol,
                _,
                sends,
            ),
            httpx.AsyncClient(transport=httpx.MockTransport(model)) as model_http,
        ):
            core.model = ModelClient(core.settings, model_http)
            core.content.options = core.settings.content
            for _ in range(2):
                await window.capture_once(client, protocol)
                await window.approve_captured(
                    client,
                    capture_approval(window),
                    image_resolver=resolve,
                    image_transport=httpx.MockTransport(cdn),
                )
                assert await core.run_once()
                await window.send_once(client, protocol, reply_approval(window.plan, core))
            if second == "quoted_only":
                assert len(calls) == 3 and len(downloads) == 1 and len(sends) == 2
                current = core.store.one(
                    "SELECT event_id FROM events WHERE source='qq.onebot' "
                    "ORDER BY rowid DESC LIMIT 1"
                )["event_id"]
                assert core.content.context(current) == []
                assert "UNIQUE_IMAGE_ANALYSIS" not in encode(calls[-1])
            else:
                assert len(calls) == 4 and len(downloads) == len(sends) == 2
                await window.capture_once(client, protocol)
                with pytest.raises(httpx.HTTPStatusError):
                    await window.approve_captured(
                        client,
                        capture_approval(window),
                        image_resolver=resolve,
                        image_transport=httpx.MockTransport(cdn),
                    )
                assert len(calls) == 4 and len(downloads) == len(sends) == 2
                assert core.store.one("SELECT COUNT(*) n FROM model_admissions")["n"] == 4

    run(scenario())


@pytest.mark.parametrize(
    "failure",
    [
        "too_many_images",
        "mime",
        "pixels",
        "dns_stop",
        "unknown_usage",
        "epoch_after_vision",
        "media_hash_after_vision",
        "model_getter",
        "reserve",
        "signature",
        "ledger",
        "upload_bytes",
        "upload_mime",
        "upload_lease",
        "lease_before_dispatch",
    ],
)
def test_image_intake_failure_never_falls_back_or_retries(core, tmp_path, monkeypatch, failure):
    calls, downloads = [], []
    raw = raw_event(
        message=[
            {"type": "text", "data": {"text": "看图 https://www.bilibili.com/video/BV1xx411c7mD"}},
            image_segment(),
        ]
    )
    if failure == "too_many_images":
        raw["message"].extend([image_segment()] * 4)

    async def source(ws):
        await ws.send(json.dumps(raw))
        await ws.wait_closed()

    def model(request):
        calls.append(request)
        if failure == "media_hash_after_vision":
            core.store.execute("UPDATE content_items SET media_sha256=?", ("0" * 64,))
        if failure == "epoch_after_vision":
            with core.store.transaction():
                core.ledger.expire_events(
                    core.store,
                    [
                        core.store.one("SELECT event_id FROM events WHERE source='qq.onebot'")[
                            "event_id"
                        ]
                    ],
                    core.clock(),
                )
        value = {"choices": [{"message": {"content": "合成图片分析"}}]}
        if failure != "unknown_usage":
            value["usage"] = {"prompt_tokens": 100, "completion_tokens": 10}
        return httpx.Response(200, json=value)

    def cdn(request):
        downloads.append(request)
        return httpx.Response(
            200,
            content=png(),
            headers={"Content-Type": "text/plain" if failure == "mime" else "image/png"},
        )

    async def resolve(host, port):
        if failure == "dns_stop":
            live_text.stop_window(
                core.store,
                live_text.WindowReference(window_sha256=core.settings.live_text_window.plan_sha256),
            )
        return ["1.1.1.1"]

    opts = options()
    if failure == "pixels":
        opts["paid_options"]["image_input"] = opts["paid_options"]["image_input"].model_copy(
            update={"max_pixels_per_image": 1}
        )

    async def scenario():
        async with (
            source_window(
                core,
                tmp_path,
                source,
                live=True,
                approved=True,
                rich_input_scope="references_v1",
                scope_options=opts,
            ) as (
                window,
                client,
                protocol,
                _,
                sends,
            ),
            httpx.AsyncClient(transport=httpx.MockTransport(model)) as model_http,
        ):
            core.model = ModelClient(core.settings, model_http)
            core.content.options = core.settings.content
            if failure == "model_getter":

                def unavailable_model():
                    raise RuntimeError("synthetic model getter failure")

                core.content.model = unavailable_model
            if failure == "lease_before_dispatch":

                async def change_lease(request):
                    core.store.execute(
                        "UPDATE content_items SET lease_token=?", ("content_lease_" + "0" * 32,)
                    )

                model_http.event_hooks["request"].append(change_lease)
            if failure == "too_many_images":
                await window.capture_once(client, protocol)
                with pytest.raises(httpx.HTTPStatusError):
                    await window.approve_captured(client, capture_approval(window))
                assert not core.store.one("SELECT 1 FROM events")
                return
            await window.capture_once(client, protocol)

            async def mutate_after_ingest(response):
                if response.request.url.path != "/internal/v1/inbox/events":
                    return
                event_id = core.store.one("SELECT event_id FROM events WHERE source='qq.onebot'")[
                    "event_id"
                ]
                if failure == "signature":
                    key = live_text.INPUT + event_id
                    value = json.loads(
                        core.store.one("SELECT value FROM runtime_meta WHERE key=?", (key,))[
                            "value"
                        ]
                    )
                    value["capture"]["signature"] = "0" * 128
                    core.store.execute(
                        "UPDATE runtime_meta SET value=? WHERE key=?", (encode(value), key)
                    )
                elif failure == "ledger":

                    def crash(*args):
                        raise RuntimeError("synthetic apply failure")

                    with monkeypatch.context() as patch:
                        patch.setattr(core.ledger, "apply", crash)
                        with pytest.raises(RuntimeError):
                            with core.store.transaction():
                                core.ledger.expire_events(core.store, [event_id], core.clock())

            async def mutate_upload(request):
                if not request.url.path.endswith("/image"):
                    return
                if failure == "upload_bytes":
                    data = io.BytesIO()
                    Image.new("RGB", (2, 2), "red").save(data, format="PNG")
                    request._content = data.getvalue()
                    request.stream = httpx.ByteStream(request._content)
                    request.headers["Content-Length"] = str(len(request._content))
                elif failure == "upload_mime":
                    request.headers["Content-Type"] = "image/jpeg"
                elif failure == "upload_lease":
                    proof = json.loads(request.headers["X-Content-Proof"])
                    proof["lease_token"] = "content_lease_" + "0" * 32
                    request.headers["X-Content-Proof"] = encode(proof)

            client.event_hooks["request"].append(mutate_upload)
            client.event_hooks["response"].append(mutate_after_ingest)
            if failure == "reserve":

                def refused(*args, **kwargs):
                    raise CoreError("SYNTHETIC_RESERVE_FAILED", 429)

                monkeypatch.setattr(budget, "reserve", refused)
            with pytest.raises((ValueError, httpx.HTTPStatusError, CoreError)):
                await window.approve_captured(
                    client,
                    capture_approval(window),
                    image_transport=httpx.MockTransport(cdn),
                    image_resolver=resolve,
                )
            assert sends == [] and len(calls) <= 1
            assert not core.store.one("SELECT 1 FROM outbox")
            assert not core.store.one("SELECT 1 FROM exploration_requests")
            assert window.status()["state"] in {"STOPPED", "CORE_STOP_UNCONFIRMED"}
            if failure in {"dns_stop", "signature", "ledger"}:
                assert downloads == calls == []
            if failure in {"mime", "pixels", "reserve", "model_getter", "lease_before_dispatch",
                           "upload_bytes", "upload_mime", "upload_lease"}:
                assert calls == [] and len(downloads) == 1
            if failure == "model_getter":
                assert not core.store.one("SELECT 1 FROM model_usage")
                assert not core.store.one("SELECT 1 FROM model_admissions")
            if failure == "media_hash_after_vision":
                assert len(calls) == 1
                assert not core.store.one("SELECT 1 FROM observations")
                usage = core.store.one("SELECT * FROM model_usage")
                assert usage["state"] == "SETTLED"
                assert usage["input_tokens"] == 100 and usage["output_tokens"] == 10
            if failure == "epoch_after_vision":
                usage = core.store.one("SELECT * FROM model_usage")
                assert usage["state"] == "SETTLED"
                assert usage["input_tokens"] == 100 and usage["output_tokens"] == 10
                assert not core.store.one("SELECT 1 FROM observations")
            if failure == "unknown_usage":
                assert core.store.one("SELECT state FROM model_usage")["state"] == "UNKNOWN"
            # A second approval cannot recover the URL or refund the original attempt.
            with pytest.raises((ValueError, httpx.HTTPStatusError)):
                await window.approve_captured(client, capture_approval(window))
            assert len(calls) <= 1 and len(downloads) <= 1

    run(scenario())
