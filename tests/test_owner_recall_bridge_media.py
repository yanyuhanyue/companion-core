"""Background original-media work yields admission and keeps one-shot I/O after restart."""

import asyncio
import json
import time
from datetime import timedelta

import httpx
import pytest

from companion.model import ModelClient
from companion_onebot_bridge import continuous, pending_media
from tests.conftest import run
from tests.test_continuous_private_replies import worker
from tests.test_input_interrupt_bridge import model_response
from tests.test_live_image_input import options as image_options
from tests.test_live_image_input import png
from tests.test_onebot_capture import capture_approval, source_window
from tests.test_onebot_direct import raw_event
from tests.test_onebot_images import image_segment
from tests.test_owner_recall_bridge import recall_frame
from tests.test_private_companion import companion_options


def scope(core):
    options, images = companion_options(core), image_options()
    options.update(image_scope=images["image_scope"],
                   image_approval_sha256=images["image_approval_sha256"])
    options["core_options"].update(images["core_options"])
    options["paid_options"]["image_input"] = images["paid_options"]["image_input"]
    return options


def original():
    return raw_event(1, message=[{"type": "text", "data": {"text": "看看这张图片"}},
                                 image_segment()])


async def resolve(*_):
    return ["1.1.1.1"]


async def advance_clock(core, stop):
    initial, start = core.clock.now, time.monotonic()
    while not stop.is_set():
        core.clock.now = initial + timedelta(seconds=time.monotonic() - start)
        await asyncio.sleep(0.01)


@pytest.mark.parametrize("phase", ["download", "upload"])
@pytest.mark.parametrize("control", ["new_input", "recall"])
def test_media_wait_does_not_block_new_input_or_recall(core, tmp_path, monkeypatch, phase, control):
    started, release = asyncio.Event(), asyncio.Event()
    downloads, calls = [], []

    async def source(ws):
        await ws.send(json.dumps(original()))
        await started.wait()
        await ws.send(recall_frame() if control == "recall" else json.dumps(
            raw_event(2, text="先不用看图，换个话题")))
        await ws.wait_closed()

    async def cdn(request):
        downloads.append(request)
        if phase == "download":
            started.set()
            await release.wait()
        return httpx.Response(200, content=png(), headers={"Content-Type": "image/png"})

    async def answer(request):
        calls.append(json.loads(request.content)["messages"])
        if phase == "upload" and len(calls) == 1:
            started.set()
            await release.wait()
            return model_response("图片里有蓝色区域")
        return model_response("好，我们换个话题。")

    async def scenario():
        async with source_window(core, tmp_path, source, live=True, approved=True,
                                 scope_options=scope(core), rich_input_scope="references_v1") as (
            window, client, protocol, _, sends,
        ), httpx.AsyncClient(transport=httpx.MockTransport(answer)) as model_http:
            core.model = ModelClient(core.settings, model_http)
            core.content.options = core.settings.content
            pause, stop = asyncio.Event(), asyncio.Event()

            async def accepted(response):
                path = response.request.url.path
                if response.is_success and (
                    (control == "recall" and path.endswith("/inputs/recall"))
                    or (control == "new_input" and path == "/internal/v1/inbox/events"
                        and json.loads(response.request.content)["payload"]["text"]
                        == "先不用看图，换个话题")
                ):
                    assert started.is_set() and not release.is_set()
                    release.set()

            def emitted(kind, **values):
                if (control == "recall" and kind == "automatic_input_retired") or (
                    control == "new_input" and kind == "automatic_input_delivered"
                ):
                    pause.set()

            client.event_hooks["response"].append(accepted)
            monkeypatch.setattr(continuous, "emit", emitted)
            task = asyncio.create_task(worker(core, stop))
            clock = asyncio.create_task(advance_clock(core, stop))
            try:
                result = await asyncio.wait_for(continuous.run(
                    window, client, protocol, pause=pause,
                    image_transport=httpx.MockTransport(cdn), image_resolver=resolve,
                ), 15)
                assert result["state"] == "OPEN" and release.is_set()
                assert len(downloads) == 1
                assert len(calls) == int(phase == "upload") + int(control == "new_input")
                assert len(sends) == int(control == "new_input")
                media = window.db.execute("SELECT * FROM automatic_media").fetchone()
                assert media["state"] == "CANCELLED" and media["manifest_json"] is None
                assert not core.store.one("SELECT 1 FROM model_usage WHERE state!='SETTLED'")
                window.verify()
            finally:
                release.set()
                stop.set()
                await asyncio.gather(task, clock)

    run(scenario())


@pytest.mark.parametrize("resume", ["queued", "upload_completed"])
def test_restart_recovers_queued_or_confirmed_upload_without_repeating_io(
    core, tmp_path, monkeypatch, resume,
):
    downloads, calls = [], []

    async def source(ws):
        await ws.send(json.dumps(original()))
        await ws.wait_closed()

    def cdn(request):
        downloads.append(request)
        return httpx.Response(200, content=png(), headers={"Content-Type": "image/png"})

    def answer(request):
        calls.append(request)
        return model_response("蓝色图片" if len(calls) == 1 else "我看到了蓝色图片。")

    async def scenario():
        async with source_window(core, tmp_path, source, live=True, approved=True,
                                 scope_options=scope(core), rich_input_scope="references_v1") as (
            window, client, protocol, _, sends,
        ), httpx.AsyncClient(transport=httpx.MockTransport(answer)) as model_http:
            core.model = ModelClient(core.settings, model_http)
            core.content.options = core.settings.content
            await window.capture_once(client, protocol)
            await window.approve_captured(client, capture_approval(window), defer_media=True)
            queued = window.db.execute("SELECT * FROM automatic_media").fetchone()
            assert queued["state"] == "PENDING" and "看看这张图片" not in queued["manifest_json"]
            if resume == "upload_completed":
                transition = pending_media.transition

                def crash(window, event_id, state, slot, receipt=None):
                    if state == "DONE":
                        raise RuntimeError("synthetic crash before completion receipt")
                    return transition(window, event_id, state, slot, receipt)

                monkeypatch.setattr(pending_media, "transition", crash)
                pump = pending_media.MediaPump(window, client,
                    image_transport=httpx.MockTransport(cdn), image_resolver=resolve)
                with pytest.raises(RuntimeError, match="synthetic crash"):
                    await pump.execute(queued)
                monkeypatch.setattr(pending_media, "transition", transition)
                assert window.db.execute("SELECT state FROM automatic_media").fetchone()[0] == (
                    "UPLOADING")
            stop, pause = asyncio.Event(), asyncio.Event()
            monkeypatch.setattr(continuous, "emit", lambda kind, **values: (
                pause.set() if kind == "automatic_input_delivered" else None))
            task = asyncio.create_task(worker(core, stop))
            try:
                result = await asyncio.wait_for(continuous.run(
                    window, client, protocol, pause=pause,
                    image_transport=httpx.MockTransport(cdn), image_resolver=resolve,
                ), 15)
                assert result["state"] == "OPEN" and result["attempt_states"] == {"ACK": 1}
                assert len(downloads) == 1 and len(calls) == 2 and len(sends) == 1
                media = window.db.execute("SELECT * FROM automatic_media").fetchone()
                assert media["state"] == "DONE" and media["manifest_json"] is None
                window.verify()
            finally:
                stop.set()
                await task

    run(scenario())


@pytest.mark.parametrize("recovery", ["active", "expired", "erased"])
def test_restart_download_reuses_only_original_valid_lease(core, tmp_path, recovery):
    downloads, claims, checks = [], [], []

    async def source(ws):
        await ws.send(json.dumps(original()))
        await ws.wait_closed()

    def cdn(request):
        downloads.append(request)
        raise httpx.ReadTimeout("synthetic response lost after original GET")

    async def scenario():
        async with source_window(core, tmp_path, source, live=True, approved=True,
                                 scope_options=scope(core), rich_input_scope="references_v1") as (
            window, client, protocol, _, sends,
        ):
            core.content.options = core.settings.content

            async def track(request):
                if request.url.path.endswith("/image/claim"):
                    claims.append(json.loads(request.content))
                elif request.url.path.endswith("/image/check"):
                    checks.append(json.loads(request.content))

            client.event_hooks["request"].append(track)
            await window.capture_once(client, protocol)
            await window.approve_captured(client, capture_approval(window), defer_media=True)
            row = window.db.execute("SELECT * FROM automatic_media").fetchone()
            first = pending_media.MediaPump(window, client,
                image_transport=httpx.MockTransport(cdn), image_resolver=resolve)
            with pytest.raises(ValueError, match="IMAGE_FETCH_FAILED"):
                await first.execute(row)
            row = window.db.execute("SELECT * FROM automatic_media").fetchone()
            assert row["state"] == "DOWNLOADING"
            original_claim = json.loads(row["receipt_json"])
            original_source = json.loads(row["manifest_json"])["images"][0]["source_sha256"]
            item = core.store.one("SELECT * FROM content_items WHERE kind='qq_image'")
            if recovery == "expired":
                core.clock.advance(16)
            elif recovery == "erased":
                with core.store.transaction():
                    core.ledger.expire_events(core.store, [row["event_id"]], core.clock())
            resumed = pending_media.MediaPump(window, client,
                image_transport=httpx.MockTransport(cdn), image_resolver=resolve)
            if recovery == "expired":
                with pytest.raises(ValueError, match="IMAGE_DOWNLOAD_CLAIM_REJECTED"):
                    await resumed.execute(row)
            elif recovery == "active":
                with pytest.raises(ValueError, match="IMAGE_FETCH_FAILED"):
                    await resumed.execute(row)
            else:
                await resumed.execute(row)
            assert len(downloads) == (2 if recovery == "active" else 1) and sends == []
            assert core.store.rows("SELECT * FROM model_usage") == []
            assert claims == [{"request_key": "image:0", "source_sha256": original_source}]
            assert checks and all(check == {"lease_token": original_claim["lease_token"],
                "source_sha256": original_source} for check in checks)
            current = core.store.one("SELECT * FROM content_items WHERE item_id=?",
                                     (item["item_id"],))
            assert current["reserved_bytes"] == item["reserved_bytes"]
            if recovery == "active":
                assert current["state"] == "FETCHING"
                assert current["lease_token"] == original_claim["lease_token"]
                assert current["lease_until"] == original_claim["lease_until"]
                retry = window.db.execute("SELECT * FROM automatic_media").fetchone()
                assert retry["state"] == "DOWNLOADING"
                assert json.loads(retry["receipt_json"]) == original_claim
            if recovery == "erased":
                cancelled = window.db.execute("SELECT * FROM automatic_media").fetchone()
                assert cancelled["state"] == "CANCELLED" and cancelled["manifest_json"] is None
            await window.stop_live(client)
            row = window.db.execute("SELECT * FROM automatic_media").fetchone()
            assert row["state"] == "CANCELLED"
            assert row["manifest_json"] is None
            before = len(downloads), len(claims), len(checks)
            with pytest.raises(ValueError, match="WINDOW_STOPPED"):
                await resumed.execute(row)
            assert (len(downloads), len(claims), len(checks)) == before
            assert core.store.rows("SELECT * FROM model_usage") == [] and sends == []
            window.verify(active=False)

    run(scenario())
