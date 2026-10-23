"""Native API/worker composition with generated media and synthetic provider transport."""

import asyncio
import base64
import io
import json

import av
import httpx
import pytest
from pydantic import SecretStr

from companion import budget, live_text
from companion.contracts import CoreError
from companion.core import Core
from companion.model import ModelClient
from companion.video_input import VideoInputPolicy, encode_animation, preview, text_and_estimate
from companion_onebot_bridge import continuous
from tests.conftest import run
from tests.test_animated_owner_images import AdvancingClock, animation, policy
from tests.test_live_image_input import options as image_options
from tests.test_live_image_input import png
from tests.test_onebot_capture import capture_approval, source_window
from tests.test_onebot_direct import raw_event
from tests.test_onebot_images import image_segment
from tests.test_private_companion import companion_options


def video_policy(**changes):
    return VideoInputPolicy(**{
        "approval_sha256": "d" * 64, "provider": "synthetic-video",
        "endpoint": "https://video.example.test/v1", "model": "configurable-video-one",
        "input_rate": 1000000, "cached_input_rate": 100000, "output_rate": 2000000,
        "estimated_tokens_per_second": 200, "max_output_tokens": 128,
        **changes,
    })


@pytest.mark.parametrize("format,mime", [
    ("GIF", "image/gif"), ("WEBP", "image/webp"), ("PNG", "image/apng"),
])
def test_animation_keeps_timeline_and_first_frame_preview_without_a_grid(format, mime):
    data, image_policy, video = animation(format), policy(), video_policy()
    first, animated = preview(data, mime, image_policy)
    assert animated and "首帧" in first.note and "300ms" in first.note
    from PIL import Image

    with Image.open(io.BytesIO(first.data)) as frame:
        assert frame.size == (12, 8) and frame.n_frames == 1
    encoded = encode_animation(data, mime, image_policy, video)
    assert 0.3 <= encoded.seconds <= 0.34
    with av.open(io.BytesIO(encoded.data)) as container:
        assert len(container.streams) == 1
        frames = [frame.to_image() for frame in container.decode(video=0)]
        first_color, last_color = frames[0].getpixel((2, 2)), frames[-1].getpixel((2, 2))
        assert first_color[0] > first_color[2] + 100
        assert last_color[2] > last_color[0] + 100
    messages = [{"role": "user", "content": [
        {"type": "text", "text": "说明动作。"}, encoded.part(),
    ]}]
    assert 60 <= text_and_estimate(video, messages)[1] <= 70


def test_video_limits_and_remote_urls_are_rejected_locally():
    with pytest.raises(CoreError, match="CONTENT_VIDEO_INPUT_INVALID"):
        encode_animation(animation(count=20), "image/gif", policy(), video_policy(max_seconds=1))
    with pytest.raises(CoreError, match="CONTENT_VIDEO_INPUT_INVALID"):
        encode_animation(animation(count=241), "image/gif", policy(), video_policy())
    with pytest.raises(CoreError, match="MODEL_VIDEO_INPUT_INVALID"):
        text_and_estimate(video_policy(), [{"role": "user", "content": [
            {"type": "text", "text": "read"},
            {"type": "video_url", "video_url": {"url": "https://internal.invalid/secret.mp4"}},
        ]}])


def test_fractional_apng_frame_durations_do_not_accumulate_rounding_drift():
    from PIL import Image

    frames = [Image.new("RGB", (4, 4), color) for color in ("red", "green", "blue")]
    data = io.BytesIO()
    frames[0].save(data, format="PNG", save_all=True,
                   append_images=[frames[index % 3] for index in range(1, 200)], duration=1.4)
    original = data.getvalue()
    duration = 0
    with Image.open(io.BytesIO(original)) as animation:
        for index in range(animation.n_frames):
            animation.seek(index)
            animation.load()
            duration += animation.info["duration"]
    encoded = encode_animation(original, "image/apng", policy(), video_policy())
    assert abs(encoded.seconds - duration / 1000) <= 0.040001


def scope_options(core, *, model):
    options, image = companion_options(core), image_options()
    options.update(image_scope=image["image_scope"],
                   image_approval_sha256=image["image_approval_sha256"])
    options["core_options"].update(image["core_options"], video_model_key=SecretStr(
        "synthetic-video-provider-key-12345",
    ))
    options["paid_options"].update(image_input=image["paid_options"]["image_input"],
                                   video_input=video_policy(model=model), per_request_calls=4)
    return options


@pytest.mark.parametrize("selected,model,extra_static", [
    (True, "configurable-video-one", False), (True, "configurable-video-two", False),
    (False, "configurable-video-one", False), (True, "configurable-video-one", True),
])
def test_deepseek_selects_video_tool_then_independent_worker_chats(
    core, tmp_path, monkeypatch, selected, model, extra_static,
):
    from companion import __main__, api
    from companion import core as core_module

    core.clock = AdvancingClock(core.clock.now)
    calls, instances, sends_seen, downloads = [], [], [], []
    original = animation(size=(12, 8))

    async def source(ws):
        message = [
            {"type": "text", "data": {"text": "这张动图在做什么？"}},
            image_segment("https://gchat.qpic.cn/animation"),
        ]
        if extra_static:
            message.append(image_segment("https://gchat.qpic.cn/static"))
        await ws.send(json.dumps(raw_event(
            1, time=int(core.clock.now.timestamp()), message=message,
        )))
        await ws.wait_closed()

    def construct(*args, **kwargs):
        instance = Core(*args, clock=core.clock, **kwargs)
        instances.append(instance)
        return instance

    def respond(request):
        body = json.loads(request.content)
        calls.append((request, body))
        message = {"content": "画面是一个红色区域，只查看了首帧。"}
        if "tools" in body and selected:
            message = {"content": None, "tool_calls": [{
                "id": "call_video_1", "type": "function", "function": {
                    "name": "understand_video", "arguments": '{"question":"描述颜色变化过程"}',
                },
            }]}
        elif body["model"] == model:
            assert str(request.url) == "https://video.example.test/v1/chat/completions"
            assert request.headers["Authorization"] == "Bearer synthetic-video-provider-key-12345"
            assert "thinking" not in body and "tools" not in body
            parts = body["messages"][0]["content"]
            assert parts[1]["type"] == "video_url"
            if extra_static:
                assert len(parts) == 3 and parts[2]["type"] == "image_url"
            video = base64.b64decode(parts[1]["video_url"]["url"].split(",", 1)[1])
            with av.open(io.BytesIO(video)) as container:
                assert len(list(container.decode(video=0))) == 8
            message = {"content": "红色先变成绿色，再变成蓝色，连续发生。"
                                  "synthetic-video-provider-key-12345"}
        elif "tools" not in body:
            assert body["model"] == core.settings.model_name
            message = {"content": "它先变绿，再变蓝。" if selected else "我只看到了红色首帧。"}
        return httpx.Response(200, json={"choices": [{"message": message}],
            "usage": {"prompt_tokens": 100, "completion_tokens": 20}})

    async def fetch(image, *_args, **_kwargs):
        downloads.append(image.slot)
        if image.slot == 1:
            return png(), "image/png"
        return original, "image/gif"

    async def scenario():
        from companion_onebot_bridge import pending_media

        monkeypatch.setattr(api, "Core", construct)
        monkeypatch.setattr(core_module, "Core", construct)
        monkeypatch.setattr(pending_media, "fetch_image", fetch)
        monkeypatch.setattr(api, "AsyncClient", lambda **kwargs: httpx.AsyncClient(
            **kwargs, transport=httpx.MockTransport(respond)), raising=False)
        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as model_http:
            monkeypatch.setattr(__main__, "ModelClient", lambda settings, _: ModelClient(
                settings, model_http))
            options = scope_options(core, model=model)
            if extra_static:
                image = options["paid_options"]["image_input"]
                options["paid_options"]["image_input"] = image.model_copy(
                    update={"max_images_per_request": "unlimited"},
                )
            async with source_window(
                core, tmp_path, source, live=True, approved=True,
                scope_options=options, rich_input_scope="references_v1",
                independent_api=True,
            ) as (window, client, protocol, _, sends):
                pause = asyncio.Event()

                def emitted(kind, **_values):
                    if kind == "automatic_input_delivered":
                        sends_seen.extend(sends)
                        pause.set()

                monkeypatch.setattr(continuous, "emit", emitted)
                task = asyncio.create_task(__main__.worker(core.settings))
                try:
                    result = await asyncio.wait_for(
                        continuous.run(window, client, protocol, pause=pause), 25,
                    )
                    assert result["state"] == "OPEN" and result["new_inputs"] == 1
                    assert result["attempt_states"] == {"ACK": 1}
                finally:
                    task.cancel()
                    await asyncio.gather(task, return_exceptions=True)
        assert len(instances) == 2 and instances[0].store is not instances[1].store
        assert downloads == ([0, 1] if extra_static else [0])
        assert len(calls) == (3 if selected else 2) and len(sends_seen) == 1
        usages = core.store.rows("SELECT * FROM model_usage ORDER BY rowid")
        assert [row["scenario"] for row in usages] == (
            ["content_vision", "content_video", "chat"] if selected else ["content_vision", "chat"]
        )
        assert all(row["state"] == "SETTLED" for row in usages)
        if selected:
            assert usages[1]["provider"] == "synthetic-video" and usages[1]["model"] == model
            assert usages[1]["billed_micro_usd"] == 140
        final = calls[-1][1]
        assert "tools" not in final and final["model"] == core.settings.model_name
        assert ("连续发生" in json.dumps(final, ensure_ascii=False)) is selected
        assert core.store.one("SELECT COUNT(*) n FROM outbox")["n"] == 1
        assert "synthetic-video-provider-key-12345" not in json.dumps(final)
        assert budget.total_monthly(core.store, "2000-01-01") >= 140 if selected else True

    try:
        run(scenario())
    except httpx.HTTPStatusError as error:
        raise AssertionError(error.response.text) from error


@pytest.mark.parametrize("boundary", [
    "stop", "key", "payload", "unknown", "missing_usage", "invalid_tool",
])
def test_video_boundary_changes_and_unknown_usage_never_send_or_repeat(
    core, tmp_path, boundary,
):
    calls = []
    model_name = "configurable-video-one"

    async def source(ws):
        await ws.send(json.dumps(raw_event(1, message=[image_segment()])))
        await ws.wait_closed()

    async def hook(request):
        if json.loads(request.content)["model"] == model_name:
            if boundary == "stop":
                live_text.stop_window(core.store, live_text.WindowReference(
                    window_sha256=core.settings.live_text_window.plan_sha256,
                ), core.clock)
            elif boundary == "key":
                core.settings.video_model_key = SecretStr("different-synthetic-video-key")
            elif boundary == "payload":
                request._content = b'{}'

    def respond(request):
        body = json.loads(request.content)
        calls.append(body)
        if body["model"] == model_name:
            if boundary == "unknown":
                raise httpx.ReadTimeout("synthetic unknown video result")
            result = {"choices": [{"message": {"content": "分析结果。"}}]}
            if boundary != "missing_usage":
                result["usage"] = {"prompt_tokens": 100, "completion_tokens": 20}
            return httpx.Response(200, json=result)
        return httpx.Response(200, json={
            "choices": [{"message": {"content": None, "tool_calls": [{
                "id": "call_video_1", "type": "function", "function": {
                    "name": "unknown_tool" if boundary == "invalid_tool" else "understand_video",
                    "arguments": '{"question":"描述动作"}',
                },
            }]}}], "usage": {"prompt_tokens": 100, "completion_tokens": 20},
        })

    async def scenario():
        async with source_window(
            core, tmp_path, source, live=True, approved=True,
            scope_options=scope_options(core, model=model_name), rich_input_scope="references_v1",
        ) as (window, client, protocol, _, sends):
            async with httpx.AsyncClient(transport=httpx.MockTransport(respond),
                                         event_hooks={"request": [hook]}) as model:
                core.model = ModelClient(core.settings, model)
                core.content.options = core.settings.content
                await window.capture_once(client, protocol)

                async def resolve(*_):
                    return ["1.1.1.1"]

                with pytest.raises(httpx.HTTPStatusError):
                    await window.approve_captured(client, capture_approval(window),
                        image_transport=httpx.MockTransport(lambda _: httpx.Response(
                            200, content=animation(size=(3, 2)),
                            headers={"Content-Type": "image/gif"},
                        )), image_resolver=resolve)
                expected_calls = 2 if boundary in {"unknown", "missing_usage"} else 1
                assert len(calls) == expected_calls and sends == []
                usages = core.store.rows("SELECT * FROM model_usage ORDER BY rowid")
                assert usages[0]["state"] == "SETTLED"
                if boundary != "invalid_tool":
                    assert len(usages) == 2 and usages[1]["state"] == "UNKNOWN"
                    assert usages[1]["billed_micro_usd"] > 0
                assert not core.store.rows("SELECT * FROM outbox")
                assert window.status()["state"] == "STOPPED"
                assert not await core.run_once()
                assert len(calls) == expected_calls

    run(scenario())
