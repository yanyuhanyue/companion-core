"""Catalog offers and final image dispatch through real API and independent workers."""

import asyncio
import base64
import json

import httpx
import pytest

from companion.core import Core
from companion.model import ModelClient
from companion_onebot_bridge import capture, continuous
from tests.conftest import run
from tests.test_continuous_private_replies import worker
from tests.test_input_interrupt_bridge import model_response
from tests.test_next_memes_catalog import metadata, png
from tests.test_onebot_capture import capture_approval, source_window
from tests.test_onebot_direct import raw_event
from tests.test_onebot_window import reply_approval
from tests.test_owner_recall_bridge import recall_frame
from tests.test_owner_recall_bridge_media import advance_clock
from tests.test_private_companion import companion_options


def options(core, *, discovery=False):
    value = companion_options(core)
    value["core_options"].update(expression_selection_probability=1,
                                meme_discovery_enabled=discovery,
                                meme_discovery_daily_download_bytes=32 * 1024 * 1024,
                                meme_discovery_daily_storage_bytes=8 * 1024 * 1024)
    return value


@pytest.mark.parametrize("outcome", ["sent", "missing", "unknown"])
def test_selected_catalog_bytes_use_real_api_independent_worker_and_receipts(
    core, tmp_path, monkeypatch, outcome,
):
    from companion import api

    calls, invoked, instances = [], [], []
    runtime = None
    candidate = None

    async def source(ws):
        await ws.send(json.dumps(raw_event(1, text="太棒了，成功了")))
        await ws.wait_closed()

    def construct(*args, **kwargs):
        instance = Core(*args, clock=core.clock, **kwargs)
        instances.append(instance)
        return instance

    def answer(request):
        calls.append(json.loads(request.content)["messages"])
        if outcome == "missing":
            runtime.catalog.remove(candidate)
        return model_response("完成啦！⬡" + candidate + "⬡")

    def protocol_reply(request):
        if request.url.path == "/send_private_msg":
            invoked.append(request)
            if outcome == "unknown":
                raise httpx.ReadTimeout("synthetic delivery response loss")

    async def scenario():
        nonlocal runtime, candidate
        monkeypatch.setattr(api, "Core", construct)
        async with source_window(
            core, tmp_path, source, live=True, approved=True,
            scope_options=options(core), bridge_options={"meme_enabled": True},
            independent_api=True, protocol_reply=protocol_reply,
        ) as (window, client, protocol, _, sends), httpx.AsyncClient(
            transport=httpx.MockTransport(answer),
        ) as model_http:
            runtime = window.memes
            candidate = runtime.catalog.add(png(), metadata(), check=lambda: None)
            independent = Core(core.settings, model=ModelClient(core.settings, model_http),
                               clock=core.clock)
            stop, pause = asyncio.Event(), asyncio.Event()

            def emitted(kind, **values):
                if kind == "automatic_input_delivered":
                    pause.set()

            monkeypatch.setattr(continuous, "emit", emitted)
            task = asyncio.create_task(worker(independent, stop))
            clock = asyncio.create_task(advance_clock(core, stop))
            try:
                result = await asyncio.wait_for(
                    continuous.run(window, client, protocol, pause=pause), 20,
                )
                assert len(calls) == len(invoked) == 1
                assert candidate in json.dumps(calls[0], ensure_ascii=False)
                assert instances[0] is not independent
                assert instances[0].store is not independent.store
                pin = runtime.db.execute("SELECT state,sent_at FROM meme_pins").fetchone()
                if outcome == "unknown":
                    assert result["state"] == "STOPPED" and pin[0] == "UNKNOWN"
                    assert sends == [] and pin[1] is None
                    assert (await continuous.run(window, client, protocol))["state"] == "STOPPED"
                    assert len(invoked) == 1
                else:
                    assert result["state"] == "OPEN" and len(sends) == 1
                    parts = sends[0]["message"]
                    assert parts[0] == {"type": "text", "data": {"text": "完成啦！"}}
                    if outcome == "sent":
                        assert len(parts) == 2 and pin[0] == "SENT" and pin[1] is not None
                        assert parts[1]["data"]["file"] == "base64://" + base64.b64encode(
                            png()).decode("ascii")
                    else:
                        assert len(parts) == 1 and pin[0] == "CANCELLED"
                assert core.store.one("SELECT COUNT(*) n FROM model_usage WHERE state='SETTLED'")[
                    "n"] == 1
            finally:
                stop.set()
                await asyncio.gather(task, clock)
                independent.close()

    run(scenario())


@pytest.mark.parametrize("control", ["recall", "new_input", "duplicate"])
def test_received_control_fences_prepared_meme_before_dispatch(core, tmp_path, control):
    runtime = receiver = None
    invoked = []

    async def source(ws):
        await ws.send(json.dumps(raw_event(1, text="太棒了，成功了")))
        await ws.wait_closed()

    def remote(request):
        if request.url.path == "/send_private_msg":
            invoked.append(request)

    async def scenario():
        nonlocal runtime, receiver
        async with source_window(core, tmp_path, source, live=True, approved=True,
                                 scope_options=options(core),
                                 bridge_options={"meme_enabled": True}, protocol_reply=remote) as (
            window, client, protocol, _, sends,
        ):
            runtime = window.memes
            candidate = runtime.catalog.add(png(), metadata(), check=lambda: None)
            async with httpx.AsyncClient(transport=httpx.MockTransport(
                lambda request: model_response("完成啦！⬡" + candidate + "⬡"),
            )) as model_http:
                core.model = ModelClient(core.settings, model_http)
                await window.capture_once(client, protocol)
                await window.approve_captured(client, capture_approval(window))
                assert await core.run_once()
                receiver = continuous.OwnerSource(window, protocol, asyncio.Event())

                async def before(request):
                    if request.url.path == "/send_private_msg":
                        assert runtime.db.execute("SELECT state FROM meme_pins").fetchone()[0] == (
                            "PREPARED")
                        if control == "recall":
                            receiver.put_recall(recall_frame(), core.clock.now,
                                                ("127.0.0.1", 12345))
                        else:
                            frame = json.dumps(raw_event(
                                2 if control == "new_input" else 1,
                                text="换个话题" if control == "new_input" else "太棒了，成功了",
                            ))
                            _, event = capture.input_event(window, frame, core.clock.now)
                            receiver.put(frame, core.clock.now, ("127.0.0.1", 12345),
                                         event["event_id"])

                protocol.event_hooks["request"].append(before)
                result = await window.send_once(client, protocol, reply_approval(window.plan, core))
                assert result["state"] == "OPEN"
                if control == "duplicate":
                    assert result["attempt_states"] == {"ACK": 1}
                    assert len(sends) == len(invoked) == 1
                    assert runtime.db.execute("SELECT state FROM meme_pins").fetchone()[0] == "SENT"
                    assert receiver.first() is None
                else:
                    assert result["attempt_states"] == {"NOT_SENT": 1}
                    assert sends == invoked == []
                    assert runtime.db.execute("SELECT state FROM meme_pins").fetchone()[0] == (
                        "CANCELLED")
                    assert core.store.one("SELECT state FROM outbox")["state"] == "CANCELLED"
                    if control == "new_input":
                        assert core.store.one("SELECT reply_revision FROM conversations")[
                            "reply_revision"] == 1
                        assert receiver.first() is not None

    run(scenario())


def test_idle_discovery_yields_to_new_input_without_stopping_chat(core, tmp_path, monkeypatch):
    started, cancelled = asyncio.Event(), asyncio.Event()
    public_calls, model_calls = [], []

    async def source(ws):
        await ws.send(json.dumps(raw_event(1, text="太棒了，成功了")))
        await started.wait()
        await ws.send(json.dumps(raw_event(2, text="换个话题，聊聊阅读")))
        await ws.wait_closed()

    async def public(request):
        public_calls.append(request)
        started.set()
        try:
            await asyncio.Future()
        finally:
            cancelled.set()

    async def resolver(*_):
        return ["93.184.216.34"]

    def answer(request):
        model_calls.append(request)
        return model_response("好，我们聊聊。")

    async def scenario():
        async with source_window(core, tmp_path, source, live=True, approved=True,
                                 scope_options=options(core, discovery=True), bridge_options={
                                     "meme_enabled": True, "meme_discovery_enabled": True,
                                 }) as (window, client, protocol, _, sends), httpx.AsyncClient(
            transport=httpx.MockTransport(answer),
        ) as model_http:
            window.memes.transport = httpx.MockTransport(public)
            window.memes.resolver = resolver
            core.model = ModelClient(core.settings, model_http)
            stop, pause = asyncio.Event(), asyncio.Event()

            def emitted(kind, **values):
                if kind == "automatic_input_delivered" and len(sends) == 2:
                    pause.set()

            monkeypatch.setattr(continuous, "emit", emitted)
            task = asyncio.create_task(worker(core, stop))
            clock = asyncio.create_task(advance_clock(core, stop))
            try:
                result = await asyncio.wait_for(
                    continuous.run(window, client, protocol, pause=pause), 20,
                )
                assert result["state"] == "OPEN" and result["attempt_states"] == {"ACK": 2}
                assert cancelled.is_set() and len(public_calls) == 1
                assert len(model_calls) == 2
                assert core.store.one("SELECT state FROM meme_discovery_reservations")[
                    "state"] == "UNKNOWN"
                assert core.store.one("SELECT COUNT(*) n FROM model_usage WHERE state='SETTLED'")[
                    "n"] == 2
            finally:
                stop.set()
                await asyncio.gather(task, clock)

    run(scenario())


def test_capture_replays_frozen_offer_after_lost_core_approval_ack(core, tmp_path):
    requests = []

    async def source(ws):
        await ws.send(json.dumps(raw_event(1, text="太棒了，成功了")))
        await ws.wait_closed()

    async def scenario():
        async with source_window(core, tmp_path, source, live=True, approved=True,
                                 scope_options=options(core),
                                 bridge_options={"meme_enabled": True}) as (
            window, client, protocol, _, sends,
        ):
            runtime = window.memes
            original = runtime.catalog.add(png(), metadata(), check=lambda: None)
            await window.capture_once(client, protocol)
            approval = capture_approval(window)
            row = window.db.execute("SELECT * FROM window_inputs").fetchone()
            event = json.loads(window.db.execute("SELECT body FROM inbox").fetchone()[0])
            receipt = json.loads(row["capture_json"])
            first = {key: value for key, value in approval.items() if key != "capture_sha256"}
            first.update(capture=receipt, meme_offer=runtime.prepare_offer(event, receipt))

            async def lost_ack(response):
                if response.request.url.path.endswith("/inputs/approve"):
                    assert response.status_code == 200
                    requests.append(json.loads(response.request.content))
                    if len(requests) == 1:
                        raise httpx.ReadTimeout("synthetic accepted approval ACK lost")

            client.event_hooks["response"].append(lost_ack)
            with pytest.raises(httpx.ReadTimeout):
                await window.core_post(client, "/internal/v1/live-text/inputs/approve", first)
            assert window.db.execute("SELECT state FROM window_inputs").fetchone()[0] == "CAPTURED"
            runtime.catalog.remove(original)
            later = runtime.catalog.add(png("red"), metadata(), check=lambda: None)
            receiver = continuous.OwnerSource(window, protocol, asyncio.Event())
            frame = json.dumps(raw_event(2, text="换个话题"))
            _, pending = capture.input_event(window, frame, core.clock.now)
            receiver.put(frame, core.clock.now, ("127.0.0.1", 12345), pending["event_id"])
            result = await window.approve_captured(client, approval)
            assert result["state"] == "OPEN" and sends == []
            assert len(requests) == 2 and requests[0] == requests[1]
            candidates = requests[1]["meme_offer"]["candidates"]
            assert [item["candidate_id"] for item in candidates] == [original]
            assert later not in {item["candidate_id"] for item in candidates}
            assert window.db.execute("SELECT state FROM window_inputs").fetchone()[0] == "APPROVED"

    run(scenario())
