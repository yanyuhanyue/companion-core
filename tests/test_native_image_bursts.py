"""Real native receive/media/send tasks, Core HTTP and independent SDK workers."""

import asyncio
import base64
import hashlib
import io
import json
from contextlib import asynccontextmanager
from datetime import UTC, datetime

import httpx
import pytest
from PIL import Image

from companion import api
from companion.core import Core
from companion_onebot_bridge import app, pending_media
from tests.conftest import run
from tests.native_continuous_support import host, until
from tests.test_live_image_input import options
from tests.test_onebot_direct import raw_event
from tests.test_onebot_images import image_segment
from tests.test_owner_image_recovery import run_worker


def picture(color):
    output = io.BytesIO()
    Image.new("RGB", (3, 2), color).save(output, format="PNG")
    return output.getvalue()


def model_images(request):
    return [base64.b64decode(part["image_url"]["url"].split(",", 1)[1])
            for message in request["messages"] if isinstance(message["content"], list)
            for part in message["content"] if part["type"] == "image_url"]


@asynccontextmanager
async def bridge_tasks(active, pump):
    async with asyncio.TaskGroup() as group:
        tasks = [
            group.create_task(app.receive(active.config, active.spool, active.wake, active.stop,
                active.metrics, active.onebot, runtime=active.runtime, core=active.client)),
            group.create_task(app.upload(active.client, active.spool, active.wake, active.stop,
                active.metrics, runtime=active.runtime)),
            group.create_task(app.deliver(active.client, active.onebot, active.spool, active.stop,
                active.metrics, runtime=active.runtime, protocol=active.protocol)),
            group.create_task(app.transfer_media(pump, active.stop)),
        ]
        try:
            yield tasks
        finally:
            active.stop.set()
            for task in tasks:
                task.cancel()


@asynccontextmanager
async def native_host(core, tmp_path, monkeypatch, *, count, history=False):
    inbound = asyncio.Queue()

    async def source(ws):
        async def send():
            while True:
                await ws.send(await inbound.get())
        sender = asyncio.create_task(send())
        try:
            await ws.wait_closed()
        finally:
            sender.cancel()
            await asyncio.gather(sender, return_exceptions=True)

    monkeypatch.setattr(api, "Core", lambda *args, **kwargs: Core(
        *args, clock=core.clock, **kwargs))
    scope = options()
    scope["core_options"]["content"] = scope["core_options"]["content"].model_copy(
        update={"max_images_per_event": count})
    scope["paid_options"]["image_input"] = scope["paid_options"]["image_input"].model_copy(
        update={"max_images_per_request": count})
    async with host(core, tmp_path, source,
        core_options={**scope["core_options"], "rich_input_scope": "references_v1"},
        paid_options=scope["paid_options"], purposes=("chat", "image", "content",
            *(('history',) if history else ())),
        bridge_options={"rich_input_scope": "references_v1"}, independent_api=True,
    ) as (active, handshakes, sends, calls):
        core.content.options = core.settings.content
        yield active, inbound, handshakes, sends, calls


async def send(core, inbound, number, message):
    await inbound.put(json.dumps(raw_event(number, time=int(datetime.now(UTC).timestamp()),
                                            message=message), ensure_ascii=False))


async def resolve(*_):
    return ["1.1.1.1"]


@pytest.mark.parametrize("failure", ["connection", "read_timeout", "log_failure"])
def test_failed_picture_keeps_task_group_alive_for_sibling_image_text_and_next_turn(
    core, tmp_path, monkeypatch, capsys, failure,
):
    async def scenario():
        async with native_host(core, tmp_path, monkeypatch, count=2) as values:
            active, inbound, handshakes, sends, calls = values
            failed, downloads, requests = asyncio.Event(), [], []
            good = picture("green")

            def cdn(request):
                downloads.append(request.url.path)
                if request.url.path.endswith("/bad"):
                    failed.set()
                    private = str(request.url) + " synthetic-owner-private-detail"
                    if failure != "read_timeout":
                        raise httpx.ConnectError(private, request=request)
                    raise httpx.ReadTimeout(private, request=request)
                return httpx.Response(200, content=good, headers={"Content-Type": "image/png"})

            async def tracked(request):
                if request.url.path.endswith(("/image/claim", "/image")):
                    requests.append(request.url.path)

            active.client.event_hooks["request"].append(tracked)
            if failure == "log_failure":
                from companion_onebot_bridge import continuous

                def unavailable(*args, **kwargs):
                    raise OSError("synthetic telemetry sink unavailable")

                monkeypatch.setattr(continuous, "emit", unavailable)
            pump = pending_media.MediaPump(active.runtime, active.client,
                image_transport=httpx.MockTransport(cdn), image_resolver=resolve,
                metrics=active.metrics)
            async with bridge_tasks(active, pump) as tasks:
                await send(core, inbound, 1, [
                    {"type": "text", "data": {"text": "保留这段文字，并看看附件"}},
                    image_segment("https://gchat.qpic.cn/bad?rkey=synthetic-signed-key", subtype=1),
                    image_segment("https://gchat.qpic.cn/good", subtype=1),
                ])
                await until(failed.is_set, tasks=tasks, deadline_seconds=20)
                original = core.store.one("SELECT * FROM content_items WHERE slot=0")
                core.clock.advance(16)
                await core.content.run_once()
                await until(lambda: active.db.execute("SELECT state FROM automatic_media")
                    .fetchone()[0] == "DONE", tasks=tasks, deadline_seconds=20)
                assert [row["state"] for row in core.store.rows(
                    "SELECT state FROM content_items ORDER BY slot")] == ["FAILED", "SUCCEEDED"]
                closed = core.store.one("SELECT * FROM content_items WHERE slot=0")
                assert closed["reserved_bytes"] == original["reserved_bytes"]
                assert closed["lease_token"] == original["lease_token"]
                assert closed["lease_until"] == original["lease_until"]
                first = await run_worker(core, tmp_path, 1, "文字已收到，一张图片暂时读不到。")
                assert len(first) == 1 and model_images(first[0]) == [good]
                assert "保留这段文字" in json.dumps(first[0]["messages"], ensure_ascii=False)
                await until(lambda: active.attempts() == {"ACK": 1}, tasks=tasks,
                            deadline_seconds=20)
                await send(core, inbound, 2, [{"type": "text", "data": {"text": "继续聊天"}}])
                await until(lambda: len(core.store.rows("SELECT * FROM events")) == 2,
                            tasks=tasks, deadline_seconds=20)
                second = await run_worker(core, tmp_path, 2, "可以，继续聊。")
                assert len(second) == 1 and model_images(second[0]) == []
                await until(lambda: active.attempts() == {"ACK": 2}, tasks=tasks,
                            deadline_seconds=20)
                assert active.runtime.row()["state"] == "ACTIVE"
                assert all(not task.done() for task in tasks)
                assert len(handshakes) == 1 and len(sends) == 2 and calls == []
                assert downloads == ["/bad", "/good"]
                assert sum(path.endswith("/image/claim") for path in requests) == 2
                assert sum(path.endswith("/image") for path in requests) == 1
                assert [row["stage"] for row in active.events].count("media_unavailable") == 1
                assert core.store.rows("SELECT scenario,state FROM model_usage") == [
                    {"scenario": "chat", "state": "SETTLED"},
                    {"scenario": "chat", "state": "SETTLED"},
                ]
                active.runtime.verify()
    run(scenario())
    output = capsys.readouterr().out
    if failure != "log_failure":
        assert '"event": "media_download_failed"' in output
        assert ("IMAGE_FETCH_FAILED" if failure == "read_timeout" else
                "IMAGE_CONNECTION_FAILED") in output
    assert "synthetic-signed-key" not in output and "synthetic-owner-private-detail" not in output


@pytest.mark.parametrize("count", [1, 3])
@pytest.mark.parametrize("mode", ["downloading", "staged", "same_message"])
def test_image_burst_and_latest_text_keep_all_originals_in_order(
    core, tmp_path, monkeypatch, count, mode,
):
    async def scenario():
        async with native_host(core, tmp_path, monkeypatch, count=count, history=True) as values:
            active, inbound, handshakes, sends, calls = values
            originals = [picture(color) for color in ("red", "blue", "green")][:count]
            started, release, downloads = asyncio.Event(), asyncio.Event(), []
            question = "结合这些图片，按发送顺序说明区别"

            async def cdn(request):
                index = int(request.url.path.rsplit("/", 1)[1])
                downloads.append(index)
                if mode != "staged" and index == count // 2:
                    started.set()
                    await release.wait()
                return httpx.Response(200, content=originals[index],
                                      headers={"Content-Type": "image/png"})

            pump = pending_media.MediaPump(active.runtime, active.client,
                image_transport=httpx.MockTransport(cdn), image_resolver=resolve)
            async with bridge_tasks(active, pump) as tasks:
                segments = [image_segment(f"https://gchat.qpic.cn/original/{i}", subtype=1)
                            for i in range(count)]
                if mode == "same_message":
                    await send(core, inbound, 1, [
                        {"type": "text", "data": {"text": question}}, *segments])
                    await until(started.is_set, tasks=tasks, deadline_seconds=20)
                    total_inputs = 1
                else:
                    for index, segment in enumerate(segments):
                        await send(core, inbound, index + 1, [segment])
                        await until(lambda n=index + 1: active.captures() == n, tasks=tasks,
                                    deadline_seconds=20)
                        if mode == "staged":
                            await until(lambda n=index + 1: core.store.one(
                                "SELECT COUNT(*) n FROM captured_images")["n"] == n,
                                tasks=tasks, deadline_seconds=20)
                    if mode == "downloading":
                        await until(started.is_set, tasks=tasks, deadline_seconds=20)
                    await send(core, inbound, count + 1,
                               [{"type": "text", "data": {"text": question}}])
                    total_inputs = count + 1
                await until(lambda: len(core.store.rows("SELECT * FROM events")) == total_inputs,
                            tasks=tasks, deadline_seconds=20)
                job = core.store.one("SELECT * FROM jobs WHERE kind='chat' ORDER BY rowid DESC")
                if mode != "staged":
                    assert core.claim_job("wait-for-originals", job_id=job["job_id"]) is None
                release.set()
                await until(lambda: len(core.store.rows("SELECT * FROM captured_images")) == count,
                            tasks=tasks, deadline_seconds=20)
                request = await run_worker(core, tmp_path, 1, "按原图顺序进行合成回答。")
                assert len(request) == 1 and model_images(request[0]) == originals
                assert question in json.dumps(request[0]["messages"], ensure_ascii=False)
                await until(lambda: active.attempts() == {"ACK": 1}, tasks=tasks,
                            deadline_seconds=20)
                assert [row["origin_event_id"] for row in core.store.rows("SELECT * FROM outbox")
                        ] == [job["event_id"]]
                assert len(sends) == len(handshakes) == 1 and calls == []
                assert downloads == list(range(count))
                assert core.store.rows("SELECT scenario,state FROM model_usage") == [
                    {"scenario": "chat", "state": "SETTLED"}]
                assert [row["media_sha256"] for row in core.store.rows(
                    "SELECT c.media_sha256 FROM agent_image_inputs a JOIN content_items c "
                    "USING(item_id) WHERE a.job_id=? ORDER BY a.position", (job["job_id"],))] == [
                    hashlib.sha256(data).hexdigest() for data in originals]
                assert active.runtime.row()["state"] == "ACTIVE"
                active.runtime.verify()
    run(scenario())
