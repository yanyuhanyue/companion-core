"""Authenticated receive priority across independent API and chat worker instances."""

import asyncio
import json

import httpx

from companion.core import Core
from companion.model import ModelClient
from companion.reply_segments import SegmentedReplyConfig
from companion_onebot_bridge import continuous
from tests.conftest import run
from tests.test_continuous_private_replies import worker
from tests.test_onebot_capture import source_window
from tests.test_onebot_direct import raw_event
from tests.test_private_companion import companion_options


def model_response(text):
    return httpx.Response(200, json={"choices": [{"message": {"content": text}}],
                                    "usage": {"prompt_tokens": 100, "completion_tokens": 20}})


def test_slow_reply_does_not_block_accepting_new_input_with_independent_api_worker(
    core, tmp_path, monkeypatch,
):
    from companion import api

    started, release, new_accepted = asyncio.Event(), asyncio.Event(), asyncio.Event()
    calls, instances = [], []

    async def source(ws):
        await ws.send(json.dumps(raw_event(1, text="先说第一件事")))
        await started.wait()
        await ws.send(json.dumps(raw_event(2, text="改聊第二件事")))
        await ws.send(json.dumps(raw_event(2, text="改聊第二件事")))
        await ws.wait_closed()

    def construct(*args, **kwargs):
        instance = Core(*args, clock=core.clock, **kwargs)
        instances.append(instance)
        return instance

    async def answer(request):
        calls.append(json.loads(request.content)["messages"])
        if len(calls) == 1:
            started.set()
            await release.wait()
            return model_response("过期的第一条回复")
        return model_response("第二件事的新回复")

    async def scenario():
        monkeypatch.setattr(api, "Core", construct)
        async with source_window(core, tmp_path, source, live=True, approved=True,
                                 scope_options=companion_options(core), independent_api=True) as (
            window, client, protocol, _, sends,
        ), httpx.AsyncClient(transport=httpx.MockTransport(answer)) as model_http:
            independent = Core(core.settings, model=ModelClient(core.settings, model_http),
                               clock=core.clock)
            stop, pause = asyncio.Event(), asyncio.Event()

            async def received(response):
                if response.request.url.path == "/internal/v1/inbox/events" and (
                    json.loads(response.request.content)["payload"]["text"] == "改聊第二件事"
                    and response.status_code == 202
                ):
                    new_accepted.set()

            client.event_hooks["response"].append(received)

            async def accepted():
                await started.wait()
                await new_accepted.wait()
                assert core.store.one("SELECT COUNT(*) n FROM events")["n"] == 2
                assert not release.is_set() and not sends
                release.set()

            def emitted(kind, **values):
                if kind == "automatic_input_delivered":
                    pause.set()

            monkeypatch.setattr(continuous, "emit", emitted)
            tasks = [asyncio.create_task(worker(independent, stop)),
                     asyncio.create_task(accepted())]
            try:
                result = await asyncio.wait_for(
                    continuous.run(window, client, protocol, pause=pause), 15,
                )
                assert result["state"] == "OPEN" and result["new_inputs"] == 2
                assert result["attempt_states"] == {"ACK": 1}
                assert [item["message"][0]["data"]["text"] for item in sends] == [
                    "第二件事的新回复"]
                assert window.db.execute("SELECT state FROM window_inputs ORDER BY rowid")\
                    .fetchall()[0][0] == "SUPERSEDED"
                assert core.store.one("SELECT reply_revision FROM conversations")[
                    "reply_revision"] == 2
                assert len(calls) == 2
                assert core.store.one("SELECT COUNT(*) n FROM model_usage "
                                      "WHERE state='SETTLED'")["n"] == 2
                assert instances[0] is not independent
                assert instances[0].store is not independent.store
            finally:
                release.set()
                stop.set()
                for task in tasks:
                    task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)
                independent.close()

    run(scenario())


def test_new_input_between_bubbles_keeps_ack_prefix_and_cancels_old_tail(
    core, tmp_path, monkeypatch,
):
    first_sent = asyncio.Event()
    calls = []

    async def source(ws):
        await ws.send(json.dumps(raw_event(1, text="先详细说第一件事")))
        await first_sent.wait()
        await ws.send(json.dumps(raw_event(2, text="等一下，先说第二件事")))
        await ws.wait_closed()

    def answer(request):
        calls.append(json.loads(request.content)["messages"])
        return model_response("第一段\n第二段\n第三段" if len(calls) == 1 else "新的话题")

    async def scenario():
        policy = SegmentedReplyConfig(mode="semantic", interval_ms=1500)
        options = companion_options(core)
        options.update(segmented_reply=policy, max_send_attempts=5)
        options["core_options"]["segmented_reply"] = policy

        def sent(request):
            if request.url.path == "/send_private_msg":
                first_sent.set()

        async with source_window(core, tmp_path, source, live=True, approved=True,
                                 scope_options=options, protocol_reply=sent) as (
            window, client, protocol, _, sends,
        ), httpx.AsyncClient(transport=httpx.MockTransport(answer)) as model_http:
            core.model = ModelClient(core.settings, model_http)
            stop, pause = asyncio.Event(), asyncio.Event()

            def emitted(kind, **values):
                if kind == "automatic_input_delivered":
                    pause.set()

            monkeypatch.setattr(continuous, "emit", emitted)
            task = asyncio.create_task(worker(core, stop))
            try:
                result = await asyncio.wait_for(
                    continuous.run(window, client, protocol, pause=pause), 10,
                )
                assert result["state"] == "OPEN" and result["attempt_states"] == {"ACK": 2}
                assert [item["message"][0]["data"]["text"] for item in sends] == [
                    "第一段", "新的话题"]
                assert [row[0] for row in window.db.execute(
                    "SELECT state FROM window_inputs ORDER BY rowid")] == ["SUPERSEDED", "ACK"]
                assert len(calls) == 2
            finally:
                stop.set()
                await task

    run(scenario())


def test_buffered_owner_turn_fences_plain_reply_before_core_ingests_it(core, tmp_path):
    from companion_onebot_bridge import capture
    from tests.test_onebot_capture import capture_approval
    from tests.test_onebot_window import reply_approval

    async def source(ws):
        await ws.send(json.dumps(raw_event(1, text="先谈第一件事")))
        await ws.wait_closed()

    async def scenario():
        async with source_window(core, tmp_path, source, live=True, approved=True,
                                 scope_options=companion_options(core)) as (
            window, client, protocol, _, sends,
        ), httpx.AsyncClient(transport=httpx.MockTransport(
            lambda request: model_response("旧话题回复"),
        )) as model_http:
            core.model = ModelClient(core.settings, model_http)
            await window.capture_once(client, protocol)
            await window.approve_captured(client, capture_approval(window))
            assert await core.run_once()
            receiver = continuous.OwnerSource(window, protocol, asyncio.Event())

            async def before(request):
                if request.url.path == "/send_private_msg":
                    frame = json.dumps(raw_event(2, text="等等，换个话题"))
                    _, event = capture.input_event(window, frame, core.clock.now)
                    receiver.put(frame, core.clock.now, ("127.0.0.1", 12345), event["event_id"])

            protocol.event_hooks["request"].append(before)
            result = await window.send_once(client, protocol, reply_approval(window.plan, core))
            assert result["state"] == "OPEN" and result["attempt_states"] == {"NOT_SENT": 1}
            assert sends == [] and receiver.first() is not None
            assert core.store.one("SELECT reply_revision FROM conversations")["reply_revision"] == 1
            assert core.store.one("SELECT state FROM outbox")["state"] == "CANCELLED"
            proof = json.loads(window.db.execute("SELECT body FROM results").fetchone()[0])
            assert proof["result"] == "NOT_SENT"
            window.verify(active=False)

    run(scenario())
