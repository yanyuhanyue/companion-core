"""Bridge content boundaries, with no AstrBot installation or QQ sends."""

import asyncio
import importlib.util
import json
import sys
import types
from pathlib import Path

import httpx
import pytest

from astrbot_plugin_companion_bridge import content
from astrbot_plugin_companion_bridge.bridge import Spool, normalize


@pytest.fixture
def bridge_module(tmp_path, monkeypatch):
    modules = {
        name: types.ModuleType(name)
        for name in ("astrbot", "astrbot.api", "astrbot.api.event", "astrbot.api.star")
    }
    modules["astrbot.api"].AstrBotConfig = dict
    modules["astrbot.api"].logger = types.SimpleNamespace(
        error=lambda *a: None, debug=lambda *a: None, warning=lambda *a: None
    )
    api = modules["astrbot.api.event"]
    api.AstrMessageEvent = object
    api.MessageChain = object
    api.filter = types.SimpleNamespace(
        EventMessageType=types.SimpleNamespace(PRIVATE_MESSAGE=1),
        event_message_type=lambda *a, **k: lambda method: method,
    )
    star = modules["astrbot.api.star"]

    class Star:
        def __init__(self, context):
            self.context = context

    star.Star = Star
    star.Context = object
    star.StarTools = types.SimpleNamespace(get_data_dir=lambda: tmp_path)
    star.register = lambda *a: lambda cls: cls
    for name, module in modules.items():
        monkeypatch.setitem(sys.modules, name, module)
    name = "astrbot_plugin_companion_bridge._content_test_main"
    spec = importlib.util.spec_from_file_location(
        name, Path("astrbot_plugin_companion_bridge/main.py")
    )
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, name, module)
    spec.loader.exec_module(module)
    return module


def config(**overrides):
    return {
        "owner_qq_id": "10001",
        "bot_qq_id": "10002",
        "bridge_token": "x" * 32,
        **overrides,
    }


def event(order, *, text="看看这个", sender="10001", message_id="42", raw=None):
    return types.SimpleNamespace(
        get_platform_name=lambda: "aiocqhttp",
        get_sender_id=lambda: sender,
        get_group_id=lambda: "",
        unified_msg_origin="qq:FriendMessage:10001",
        message_str=text,
        stop_event=lambda: order.append("stop"),
        message_obj=types.SimpleNamespace(
            self_id="10002",
            message_id=message_id,
            timestamp=1792382400,
            raw_message=raw or {},
        ),
    )


def image_raw(url="https://gchat.qpic.cn/image/one?rkey=private-signature"):
    return {"message": [{"type": "image", "data": {"url": url}}]}


def test_unauthorized_and_disabled_handler_never_parses_content(bridge_module, monkeypatch):
    async def unexpected(*args, **kwargs):
        pytest.fail("share parsing must not run")

    monkeypatch.setattr(bridge_module, "prepare_shared", unexpected)
    order = []
    bridge = bridge_module.CompanionBridge(object(), config(content_enabled=True))
    bridge.spool = types.SimpleNamespace(put=lambda *args: order.append("spool"))
    asyncio.run(bridge.on_private_message(event(order, sender="99999", raw=image_raw())))
    assert order == []
    bridge.config["content_enabled"] = False
    asyncio.run(bridge.on_private_message(event(order, raw=image_raw())))
    assert order == ["stop", "spool"]


def test_manifest_is_durable_after_stop_and_signed_urls_stay_in_memory(
    bridge_module, tmp_path, monkeypatch
):
    order, queued = [], []
    bridge = bridge_module.CompanionBridge(object(), config(content_enabled=True))
    bridge.spool = Spool(str(tmp_path / "spool.db"))
    bridge.content_queue = types.SimpleNamespace(put=lambda *args: queued.append(args))
    original_prepare = content.prepare_shared

    async def prepare(*args, **kwargs):
        order.append("prepare")
        assert order[0] == "stop"
        return await original_prepare(*args, **kwargs)

    monkeypatch.setattr(bridge_module, "prepare_shared", prepare)
    url = image_raw()["message"][0]["data"]["url"]
    try:
        asyncio.run(bridge.on_private_message(event(order, text="截图 " + url, raw=image_raw())))
        body = bridge.spool.db.execute("SELECT body FROM inbox").fetchone()[0]
        assert order == ["stop", "prepare"]
        assert "private-signature" not in body and "gchat.qpic.cn" not in body
        assert json.loads(body)["payload"]["shared_content"] == [{"kind": "image", "slot": 0}]
        assert queued[0][2] == [{"slot": 0, "url": url}]
    finally:
        bridge.spool.db.close()


def test_pending_replay_keeps_first_manifest(bridge_module, tmp_path, monkeypatch):
    bridge = bridge_module.CompanionBridge(object(), config(content_enabled=True))
    bridge.spool = Spool(str(tmp_path / "spool.db"))
    calls = []

    async def prepare(*args, **kwargs):
        calls.append(True)
        return [{"kind": "link", "url": "https://www.bilibili.com/video/BV1234567890"}], []

    monkeypatch.setattr(bridge_module, "prepare_shared", prepare)
    try:
        asyncio.run(bridge.on_private_message(event([], text="https://b23.tv/abc")))
        first = bridge.spool.db.execute("SELECT body FROM inbox").fetchone()[0]
        asyncio.run(bridge.on_private_message(event([], text="https://b23.tv/abc")))
        assert bridge.spool.db.execute("SELECT body FROM inbox").fetchone()[0] == first
        assert len(calls) == 1
    finally:
        bridge.spool.db.close()


def test_concurrent_replay_keeps_first_durable_manifest(bridge_module, tmp_path, monkeypatch):
    async def scenario():
        bridge = bridge_module.CompanionBridge(object(), config(content_enabled=True))
        bridge.spool = Spool(str(tmp_path / "spool.db"))
        both_preparing = asyncio.Event()
        started = 0

        async def prepare(*args, **kwargs):
            nonlocal started
            started += 1
            current = started
            if started == 2:
                both_preparing.set()
            await both_preparing.wait()
            return [{"kind": "link", "url": f"https://www.xiaoheihe.cn/app/bbs/link/{current}"}], []

        monkeypatch.setattr(bridge_module, "prepare_shared", prepare)
        try:
            await asyncio.gather(
                bridge.on_private_message(event([], text="https://b23.tv/abc")),
                bridge.on_private_message(event([], text="https://b23.tv/abc")),
            )
            assert bridge.spool.db.execute("SELECT COUNT(*) FROM inbox").fetchone()[0] == 1
            assert started == 2
        finally:
            bridge.spool.db.close()

    asyncio.run(scenario())


def test_share_parser_failure_retains_owner_text(bridge_module, tmp_path, monkeypatch):
    bridge = bridge_module.CompanionBridge(object(), config(content_enabled=True))
    bridge.spool = Spool(str(tmp_path / "spool.db"))

    async def broken(*args, **kwargs):
        raise ValueError("broken card")

    monkeypatch.setattr(bridge_module, "prepare_shared", broken)
    try:
        asyncio.run(bridge.on_private_message(event([])))
        body = json.loads(bridge.spool.db.execute("SELECT body FROM inbox").fetchone()[0])
        assert body["payload"] == {"text": "看看这个", "media": []}
    finally:
        bridge.spool.db.close()


def test_queue_waits_for_durable_inbox_acceptance(tmp_path, monkeypatch):
    async def scenario():
        spool = Spool(str(tmp_path / "spool.db"))
        spool.put("key", normalize(event([]), "10001", "10002")[1])
        order, finished = [], asyncio.Event()

        def state(key):
            row = spool.db.execute("SELECT state FROM inbox WHERE key=?", (key,)).fetchone()
            return row[0] if row else None

        def respond(request):
            order.append("accepted")
            return httpx.Response(202)

        async def forward(*args, **kwargs):
            assert state("key") is None
            order.append("forward")
            finished.set()

        monkeypatch.setattr(content, "forward_images", forward)
        async with httpx.AsyncClient(
            base_url="https://core.test", transport=httpx.MockTransport(respond)
        ) as client:
            queue = content.ImageQueue(client, state)
            queue.put("key", "event", [{"slot": 0, "url": "signed-in-memory"}])
            task = asyncio.create_task(queue.run())
            try:
                await asyncio.sleep(0)
                assert order == []
                await spool.flush_one(client)
                await asyncio.wait_for(finished.wait(), 1)
                await queue.queue.join()
                assert order == ["accepted", "forward"] and queue.keys == set()
            finally:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
                queue.clear()
                spool.db.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("state,lifetime", [("rejected", 60), (None, 0), ("pending", 0.01)])
def test_rejected_or_expired_queue_drops_urls_without_network(monkeypatch, state, lifetime):
    async def scenario():
        async def unexpected(*args, **kwargs):
            pytest.fail("unaccepted or expired images must not be forwarded")

        monkeypatch.setattr(content, "forward_images", unexpected)
        queue = content.ImageQueue(object(), lambda key: state, lifetime=lifetime)
        queue.put("key", "event", [{"slot": 0, "url": "private"}])
        images = queue.queue._queue[0][2]
        task = asyncio.create_task(queue.run())
        try:
            await asyncio.wait_for(queue.queue.join(), 1)
            assert images == [] and queue.keys == set()
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    asyncio.run(scenario())


def test_queue_capacity_deduplicates_inflight_and_clear_releases_urls(monkeypatch):
    async def scenario():
        started, blocked = asyncio.Event(), asyncio.Event()

        async def forward(*args, **kwargs):
            started.set()
            await blocked.wait()

        monkeypatch.setattr(content, "forward_images", forward)
        queue = content.ImageQueue(object(), lambda key: None, capacity=2)
        images = [{"slot": 0, "url": "private"}]
        assert queue.put("one", "event1", images)
        active = queue.queue._queue[0][2]
        task = asyncio.create_task(queue.run())
        await started.wait()
        assert not queue.put("one", "event1", images)
        assert queue.put("two", "event2", images)
        pending = queue.queue._queue[0][2]
        assert not queue.put("three", "event3", images)
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        queue.clear()
        assert active == pending == [] and queue.keys == set() and queue.queue.empty()

    asyncio.run(scenario())


def test_slow_image_transfer_does_not_block_inbox_or_delivery_pump(bridge_module, monkeypatch):
    async def scenario():
        image_started, inbox_flushed, lease_requested = (
            asyncio.Event(), asyncio.Event(), asyncio.Event()
        )

        async def forward(*args, **kwargs):
            image_started.set()
            await asyncio.Event().wait()

        async def flush(*args):
            inbox_flushed.set()

        def respond(request):
            assert request.url.path == "/internal/v1/deliveries/lease"
            lease_requested.set()
            return httpx.Response(200, json={"items": []})

        monkeypatch.setattr(content, "forward_images", forward)
        async with httpx.AsyncClient(
            base_url="https://core.test", transport=httpx.MockTransport(respond)
        ) as client:
            bridge = bridge_module.CompanionBridge(object(), config(content_enabled=True))
            bridge.client = client
            async def no_result(*args):
                return False

            bridge.spool = types.SimpleNamespace(flush_one=flush, flush_result=no_result,
                config=types.SimpleNamespace(max_voice_lease_bytes=65536))
            monkeypatch.setattr(bridge_module, "host_driver", lambda *args:
                types.SimpleNamespace(config=types.SimpleNamespace(delivery_enabled=True)))
            queue = content.ImageQueue(client, lambda key: None)
            queue.put("key", "event", [{"slot": 0, "url": "private"}])
            image_task = asyncio.create_task(queue.run())
            await image_started.wait()
            pump_task = asyncio.create_task(bridge.pump())
            try:
                await asyncio.wait_for(inbox_flushed.wait(), 1)
                await asyncio.wait_for(lease_requested.wait(), 1)
                assert not image_task.done()
            finally:
                image_task.cancel()
                pump_task.cancel()
                await asyncio.gather(image_task, pump_task, return_exceptions=True)
                queue.clear()

    asyncio.run(scenario())


def test_forward_claims_before_download_and_never_retries_uncertain_upload(monkeypatch):
    async def scenario():
        order = []

        def respond(request):
            if request.method == "GET":
                return httpx.Response(
                    200, json={"items": [{"slot": 0, "kind": "qq_image", "state": "WAITING"}]}
                )
            if request.url.path.endswith("claim"):
                order.append("claim")
                return httpx.Response(200, json={"max_bytes": 100})
            assert request.extensions["timeout"]["read"] == 35
            order.append("upload")
            raise httpx.ReadTimeout("unknown", request=request)

        async def fetch(*args, **kwargs):
            order.append("download")
            assert args[1] == 100
            return b"image", "image/png"

        monkeypatch.setattr(content, "fetch_qq_image", fetch)
        async with httpx.AsyncClient(
            base_url="https://core.test", transport=httpx.MockTransport(respond)
        ) as client:
            results = await content.forward_images(
                client, "event", [{"slot": 0, "url": "private"}], enabled=True
            )
        assert order == ["claim", "download", "upload"]
        assert results[0]["state"] == "UNKNOWN"

    asyncio.run(scenario())


def test_initialize_bad_reader_validates_before_allocating_resources(bridge_module, monkeypatch):
    def unexpected(*args, **kwargs):
        pytest.fail("invalid configuration must fail before resource allocation")

    monkeypatch.setattr(bridge_module, "Spool", unexpected)
    bridge = bridge_module.CompanionBridge(object(), config(bilibili_reader_enabled=True))
    with pytest.raises(ValueError, match="requires migration"):
        asyncio.run(bridge.initialize())
    assert bridge.task is bridge.client is bridge.spool is None


def test_initialize_partial_failure_closes_every_client_and_spool(
    bridge_module, tmp_path, monkeypatch
):
    clients, databases = [], []

    def spool_factory(path):
        spool = Spool(path)
        databases.append(spool.db)
        return spool

    def client_factory(**kwargs):

        async def close():
            client.closed = True

        client = types.SimpleNamespace(closed=False, aclose=close)
        clients.append(client)
        return client

    monkeypatch.setattr(bridge_module, "Spool", spool_factory)
    monkeypatch.setattr(bridge_module.httpx, "AsyncClient", client_factory)
    def bad_queue(*args):
        raise RuntimeError("content setup failed")

    monkeypatch.setattr(bridge_module, "ImageQueue", bad_queue)
    bridge = bridge_module.CompanionBridge(object(), config(content_enabled=True))
    with pytest.raises(RuntimeError, match="setup failed"):
        asyncio.run(bridge.initialize())
    assert all(client.closed for client in clients)
    assert bridge.task is bridge.client is bridge.spool is None
    assert not hasattr(bridge, "reader_tasks")
    import sqlite3

    with pytest.raises(sqlite3.ProgrammingError, match="closed"):
        databases[0].execute("SELECT 1")


def test_terminate_cancels_content_and_delivery_tasks_and_is_repeatable(bridge_module, monkeypatch):
    async def scenario():
        started = asyncio.Event()

        async def block(*args, **kwargs):
            started.set()
            await asyncio.Event().wait()

        monkeypatch.setattr(bridge_module.CompanionBridge, "pump", block)
        monkeypatch.setattr(content, "forward_images", block)
        bridge = bridge_module.CompanionBridge(
            object(),
            config(
                content_enabled=True,
            ),
        )
        await bridge.initialize()
        bridge.content_queue.put("key", "event", [{"slot": 0, "url": "private"}])
        queue = bridge.content_queue
        images = queue.queue._queue[0][2]
        tasks = [bridge.task, bridge.content_task]
        clients = [bridge.client]
        await started.wait()
        await bridge.terminate()
        await bridge.terminate()
        assert all(task.done() for task in tasks)
        assert all(client.is_closed for client in clients)
        assert images == [] and queue.keys == set()
        assert bridge.client is bridge.spool is bridge.content_queue is None

    asyncio.run(scenario())


def test_owner_link_keeps_manifest_for_independent_reader(bridge_module, tmp_path):
    async def scenario():
        bridge = bridge_module.CompanionBridge(object(), config(content_enabled=True))
        bridge.spool = Spool(str(tmp_path / "spool.db"))
        try:
            await bridge.on_private_message(
                event([], text="https://www.bilibili.com/video/BV1234567890")
            )
            saved = json.loads(bridge.spool.db.execute("SELECT body FROM inbox").fetchone()[0])
            assert saved["payload"]["shared_content"][0]["kind"] == "link"
            assert not hasattr(bridge, "reader_tasks")
        finally:
            bridge.spool.db.close()

    asyncio.run(scenario())
