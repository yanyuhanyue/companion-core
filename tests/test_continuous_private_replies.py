"""Authenticated loopback receives, real Core/Spool, mocked provider I/O only."""

import asyncio
import json
import time
from datetime import timedelta

import httpx
import pytest

from companion.model import ModelClient
from companion.reply_segments import SegmentedReplyConfig, split_text
from companion_onebot_bridge import capture, continuous
from companion_onebot_bridge.window import read_status, request_stop
from tests.conftest import run
from tests.test_onebot_capture import capture_approval, source_window
from tests.test_onebot_direct import raw_event
from tests.test_segmented_image_memory import PARTS, REPLY


async def worker(core, stop):
    while not stop.is_set():
        if not await core.run_once("automatic-synthetic-worker"):
            await asyncio.sleep(0.01)


def response(request, calls):
    calls.append(request)
    return httpx.Response(200, json={
        "choices": [{"message": {"content": "合成自动回复"}}],
        "usage": {"prompt_tokens": 100, "completion_tokens": 20},
    })


async def exercise(core, window, client, protocol, sends, *, expected=1):
    pause, stop_worker, calls = asyncio.Event(), asyncio.Event(), []

    async def finish():
        if expected == 0:
            await asyncio.Future()
        while len(sends) < expected:  # noqa: ASYNC110 - existing mock appends to this receipt list
            await asyncio.sleep(0.01)
        pause.set()

    async with httpx.AsyncClient(transport=httpx.MockTransport(
        lambda request: response(request, calls),
    )) as model:
        core.model = ModelClient(core.settings, model)
        task = asyncio.create_task(worker(core, stop_worker))
        completed = asyncio.create_task(finish())
        try:
            result = await asyncio.wait_for(
                continuous.run(window, client, protocol, pause=pause), 15,
            )
        finally:
            stop_worker.set()
            completed.cancel()
            await asyncio.gather(task, completed, return_exceptions=True)
    return result, calls


def test_two_rapid_messages_and_duplicate_share_one_authenticated_connection(core, tmp_path):
    connections = []

    async def source(ws):
        connections.append(ws)
        await ws.send(json.dumps(raw_event(10, user_id=10003, sender={"user_id": 10003})))
        for number in (1, 1, 2):
            await ws.send(json.dumps(raw_event(number)))
        await ws.wait_closed()

    async def scenario():
        async with source_window(core, tmp_path, source, live=True, approved=True,
                                 scope_options={"automatic_reply": True}) as (
            window, client, protocol, handshakes, sends,
        ):
            result, calls = await exercise(core, window, client, protocol, sends, expected=2)
            assert result["state"] == "OPEN" and result["attempt_states"] == {"ACK": 2}
            assert result["new_inputs"] == len(calls) == len(sends) == 2
            assert len(connections) == len(handshakes) == 1
            assert window.db.execute("SELECT COUNT(*) FROM window_inputs").fetchone()[0] == 2
            assert all(row["state"] == "SETTLED" for row in core.store.rows(
                "SELECT state FROM model_usage"))
            assert not window.stop_path.exists()

    run(scenario())


def test_existing_reviewed_window_never_becomes_automatic(core, tmp_path):
    async def source(ws):
        await ws.send(json.dumps(raw_event()))

    async def scenario():
        async with source_window(core, tmp_path, source, live=True, approved=True) as (
            window, client, protocol, handshakes, sends,
        ):
            with pytest.raises(ValueError, match="AUTOMATIC_OWNER_REPLY_APPROVAL_REQUIRED"):
                await continuous.run(window, client, protocol)
            assert handshakes == sends == []
            assert window.status()["state"] == "OPEN"

    run(scenario())


def test_idle_beyond_once_capture_timeout_can_pause_and_resume_same_window(
    core, tmp_path, monkeypatch,
):
    connections = []
    monkeypatch.setattr(capture, "CAPTURE_WAIT_SECONDS", 0.01)

    async def source(ws):
        connections.append(ws)
        if len(connections) > 1:
            await ws.send(json.dumps(raw_event(1)))
        await ws.wait_closed()

    async def scenario():
        async with source_window(core, tmp_path, source, live=True, approved=True,
                                 scope_options={"automatic_reply": True}) as (
            window, client, protocol, _, sends,
        ):
            pause = asyncio.Event()
            task = asyncio.create_task(continuous.run(window, client, protocol, pause=pause))
            await asyncio.sleep(0.15)
            assert not task.done() and window.row()["state"] == "OPEN"
            assert read_status(window.plan.config)["state"] == "OPEN"
            assert window.count("window_inputs") == 0
            pause.set()
            assert (await asyncio.wait_for(task, 5))["state"] == "OPEN"
            result, calls = await exercise(core, window, client, protocol, sends)
            assert result["attempt_states"] == {"ACK": 1} and len(calls) == 1

    run(scenario())


def test_idle_websocket_disconnect_reconnects_without_resetting_window(core, tmp_path):
    connections = []

    async def source(ws):
        connections.append(ws)
        if len(connections) == 1:
            await ws.close()
        else:
            await ws.send(json.dumps(raw_event(1)))
            await ws.wait_closed()

    async def scenario():
        async with source_window(core, tmp_path, source, live=True, approved=True,
                                 scope_options={"automatic_reply": True}) as (
            window, client, protocol, _, sends,
        ):
            result, calls = await exercise(core, window, client, protocol, sends)
            assert len(connections) == 2 and len(calls) == 1
            assert result["new_inputs"] == 1 and result["attempt_states"] == {"ACK": 1}

    run(scenario())


@pytest.mark.parametrize("reason", ["stop", "expiry"])
def test_idle_owner_stop_and_expiry_stop_core_without_receiving(core, tmp_path, reason):
    async def source(ws):
        await ws.wait_closed()

    async def scenario():
        async with source_window(core, tmp_path, source, live=True, approved=True,
                                 scope_options={"automatic_reply": True}) as (
            window, client, protocol, _, sends,
        ):
            task = asyncio.create_task(continuous.run(window, client, protocol))
            await asyncio.sleep(0.05)
            if reason == "stop":
                request_stop(window.spool.directory, plan=window.plan)
            else:
                core.clock.now = window.plan.end
            assert (await asyncio.wait_for(task, 5))["state"] == "STOPPED"
            status = window.status()
            assert status["state"] == "STOPPED" and status["core_stop_confirmed"] is True
            assert status["new_inputs"] == 0 and sends == []
            assert core.store.rows("SELECT * FROM model_usage") == []

    run(scenario())


def test_uncertain_delivery_stops_without_automatic_resend(core, tmp_path):
    async def source(ws):
        await ws.send(json.dumps(raw_event(1)))
        await ws.wait_closed()

    attempted = []

    def protocol_reply(request):
        if request.url.path == "/send_private_msg":
            attempted.append(request)
            raise httpx.ReadTimeout("synthetic unknown delivery")

    async def scenario():
        async with source_window(core, tmp_path, source, live=True, approved=True,
                                 protocol_reply=protocol_reply,
                                 scope_options={"automatic_reply": True}) as (
            window, client, protocol, _, sends,
        ):
            result, calls = await exercise(core, window, client, protocol, sends, expected=0)
            assert result["state"] == "STOPPED" and result["core_stop_confirmed"]
            assert result["attempt_states"] == {"UNKNOWN": 1}
            assert len(attempted) == len(calls) == 1
            assert (await continuous.run(window, client, protocol))["state"] == "STOPPED"
            assert len(attempted) == 1

    run(scenario())


@pytest.mark.parametrize("already_approved", [False, True])
def test_restart_resumes_durably_captured_or_uploaded_input_once(core, tmp_path, already_approved):
    connections = []

    async def source(ws):
        connections.append(ws)
        if len(connections) == 1:
            await ws.send(json.dumps(raw_event(1)))
        await ws.wait_closed()

    async def scenario():
        async with source_window(core, tmp_path, source, live=True, approved=True,
                                 scope_options={"automatic_reply": True}) as (
            window, client, protocol, _, sends,
        ):
            await window.capture_once(client, protocol)
            if already_approved:
                await window.approve_captured(client, capture_approval(window))
            result, calls = await exercise(core, window, client, protocol, sends)
            assert result["attempt_states"] == {"ACK": 1}
            assert result["new_inputs"] == len(calls) == len(sends) == 1

    run(scenario())


def test_authenticated_buffer_survives_receiver_restart_and_rejects_tampering(core, tmp_path):
    received = asyncio.Event()
    connections = []

    async def source(ws):
        connections.append(ws)
        if len(connections) == 1:
            await ws.send(json.dumps(raw_event(1)))
            received.set()
        await ws.wait_closed()

    async def scenario():
        async with source_window(core, tmp_path, source, live=True, approved=True,
                                 scope_options={"automatic_reply": True}) as (
            window, client, protocol, _, sends,
        ):
            pause = asyncio.Event()
            async with continuous.OwnerSource(window, protocol, pause) as first:
                await asyncio.wait_for(received.wait(), 3)
                observed = await asyncio.wait_for(first.next(), 3)
                assert window.count("window_inputs") == 0
            # A fresh receiver reads the committed Spool row, without another network frame.
            resumed = continuous.OwnerSource(window, protocol, asyncio.Event())
            assert resumed.first() == observed
            row = window.db.execute("SELECT record_json FROM automatic_received").fetchone()
            changed = json.loads(row[0])
            changed["frame"] = json.dumps(raw_event(1, text="tampered after authenticated receive"))
            with window.db:
                window.db.execute("UPDATE automatic_received SET record_json=?",
                                  (json.dumps(changed),))
            with pytest.raises(ValueError, match="AUTHENTICATED_BUFFER_SIGNATURE_REQUIRED"):
                resumed.first()
            with window.db:
                window.db.execute("UPDATE automatic_received SET record_json=?", (row[0],))
            result, calls = await exercise(core, window, client, protocol, sends)
            assert result["attempt_states"] == {"ACK": 1} and len(calls) == 1
            assert window.db.execute("SELECT COUNT(*) FROM automatic_received").fetchone()[0] == 0

    run(scenario())


def test_automatic_segmented_reply_preserves_order_and_uses_one_generation(core, tmp_path):
    async def source(ws):
        await ws.send(json.dumps(raw_event(1)))
        await ws.wait_closed()

    policy = SegmentedReplyConfig(min_chars=12, interval_ms=0)

    async def scenario():
        async with source_window(core, tmp_path, source, live=True, approved=True,
                                 scope_options={
                                     "automatic_reply": True, "segmented_reply": policy,
                                     "max_send_attempts": 5,
                                     "core_options": {"segmented_reply": policy},
                                 }) as (window, client, protocol, _, sends):
            calls, pause, stop_worker = [], asyncio.Event(), asyncio.Event()

            def answer(request):
                calls.append(request)
                return httpx.Response(200, json={
                    "choices": [{"message": {"content": REPLY}}],
                    "usage": {"prompt_tokens": 100, "completion_tokens": 40},
                })

            def emitted(kind, **values):
                if kind == "automatic_input_delivered":
                    pause.set()

            original = continuous.emit
            continuous.emit = emitted
            try:
                async with httpx.AsyncClient(transport=httpx.MockTransport(answer)) as model:
                    core.model = ModelClient(core.settings, model)
                    task = asyncio.create_task(worker(core, stop_worker))
                    try:
                        result = await asyncio.wait_for(
                            continuous.run(window, client, protocol, pause=pause), 10,
                        )
                    finally:
                        stop_worker.set()
                        await task
            finally:
                continuous.emit = original
            assert [item["message"][0]["data"]["text"] for item in sends] == PARTS
            assert len(calls) == 1 and result["attempt_states"] == {"ACK": 3}
            assert result["new_inputs"] == 1

    run(scenario())


def test_owner_recall_and_unrelated_notices_do_not_interrupt_private_replies(core, tmp_path):
    async def source(ws):
        common = {"time": raw_event()["time"], "self_id": 10002,
                  "post_type": "notice", "user_id": 10001}
        for notice in [
            common | {"notice_type": "friend_recall", "message_id": 123},
            common | {"notice_type": "notify", "sub_type": "input_status",
                      "event_type": 1, "status_text": "正在输入"},
            common | {"notice_type": "notify", "sub_type": "poke", "target_id": 10002},
            common | {"notice_type": "friend_recall", "user_id": 10003, "message_id": 456},
        ]:
            await ws.send(json.dumps(notice))
        await ws.send(json.dumps(raw_event(1)))
        await ws.send(json.dumps(raw_event(2)))
        await ws.wait_closed()

    async def scenario():
        async with source_window(core, tmp_path, source, live=True, approved=True,
                                 scope_options={"automatic_reply": True}) as (
            window, client, protocol, _, sends,
        ):
            result, calls = await exercise(core, window, client, protocol, sends, expected=2)
            assert result["state"] == "OPEN"
            assert result["new_inputs"] == len(calls) == len(sends) == 2
            assert core.store.one("SELECT COUNT(*) n FROM events")["n"] == 2

    run(scenario())


@pytest.mark.parametrize("fault,expected", [
    ("account", "INVALID_CAPTURE_ACCOUNT"),
    ("input_status", "INVALID_CAPTURE_INPUT_STATUS"),
])
def test_notice_routing_preserves_account_and_input_status_checks(core, tmp_path, fault, expected):
    async def source(ws):
        notice = {"time": raw_event()["time"], "self_id": 10002,
                  "post_type": "notice", "user_id": 10001,
                  "notice_type": "friend_recall", "message_id": 123}
        if fault == "account":
            notice["self_id"] = 10003
        else:
            notice.update(notice_type="notify", sub_type="input_status")
        await ws.send(json.dumps(notice))
        await ws.wait_closed()

    async def scenario():
        async with source_window(core, tmp_path, source, live=True, approved=True,
                                 scope_options={"automatic_reply": True}) as (
            window, client, protocol, _, sends,
        ):
            with pytest.raises(ValueError, match=expected):
                await asyncio.wait_for(continuous.run(window, client, protocol), 5)
            assert window.status()["state"] == "STOPPED" and sends == []
            assert core.store.one("SELECT COUNT(*) n FROM events")["n"] == 0

    run(scenario())


@pytest.mark.parametrize("resume", [False, True])
def test_progressing_long_reply_finishes_and_resume_keeps_signed_segments(
    core, tmp_path, monkeypatch, resume,
):
    production = SegmentedReplyConfig(max_segments="unlimited", mode="semantic", interval_ms=1500)
    parts = split_text("好\n" * 200, production)
    assert (len(parts) - 1) * production.interval_ms / 1000 > continuous.REPLY_WAIT_SECONDS
    monkeypatch.setattr(continuous, "REPLY_WAIT_SECONDS", 0.5)
    monkeypatch.setattr(continuous, "SEND_PROGRESS_SECONDS", 0.3)
    policy = SegmentedReplyConfig(max_segments="unlimited", mode="semantic", interval_ms=600)

    async def source(ws):
        await ws.send(json.dumps(raw_event(1)))
        await ws.wait_closed()

    async def scenario():
        async with source_window(core, tmp_path, source, live=True, approved=True,
                                 scope_options={"automatic_reply": True,
                                                "segmented_reply": policy,
                                                "max_send_attempts": 5,
                                                "core_options": {"segmented_reply": policy}}) as (
            window, client, protocol, _, sends,
        ):
            pause, stop_worker, calls = asyncio.Event(), asyncio.Event(), []
            started, initial = time.monotonic(), core.clock.now

            async def advance_clock():
                while not stop_worker.is_set():
                    core.clock.now = initial + timedelta(seconds=time.monotonic() - started)
                    await asyncio.sleep(0.01)

            def answer(request):
                calls.append(request)
                return httpx.Response(200, json={
                    "choices": [{"message": {"content": "好呀\n接着说\n我在听\n慢慢聊"}}],
                    "usage": {"prompt_tokens": 100, "completion_tokens": 20},
                })

            def emitted(kind, **values):
                if kind == "automatic_input_delivered":
                    pause.set()

            monkeypatch.setattr(continuous, "emit", emitted)
            async with httpx.AsyncClient(transport=httpx.MockTransport(answer)) as model:
                core.model = ModelClient(core.settings, model)
                if resume:
                    await window.capture_once(client, protocol)
                    await window.approve_captured(client, capture_approval(window))
                    assert await core.run_once()
                    await continuous.pump_private(window, client, protocol)
                    assert len(sends) == 1
                tasks = [asyncio.create_task(worker(core, stop_worker)),
                         asyncio.create_task(advance_clock())]
                try:
                    result = await asyncio.wait_for(
                        continuous.run(window, client, protocol, pause=pause), 10,
                    )
                finally:
                    stop_worker.set()
                    await asyncio.gather(*tasks)
            assert result["state"] == "OPEN" and result["attempt_states"] == {"ACK": 4}
            assert len(calls) == 1 and len(sends) == 4
            assert [item["message"][0]["data"]["text"] for item in sends] == [
                "好呀", "接着说", "我在听", "慢慢聊",
            ]
            assert not core.store.one("SELECT 1 FROM model_usage WHERE state='UNKNOWN'")

    run(scenario())


@pytest.mark.parametrize("stage", ["model", "send"])
def test_reply_wait_and_send_no_progress_remain_bounded(core, tmp_path, monkeypatch, stage):
    monkeypatch.setattr(continuous, "REPLY_WAIT_SECONDS", 0.2)
    monkeypatch.setattr(continuous, "SEND_PROGRESS_SECONDS", 0.2)

    async def source(ws):
        await ws.send(json.dumps(raw_event(1)))
        await ws.wait_closed()

    async def no_progress(*args, **kwargs):
        return False

    async def scenario():
        async with source_window(core, tmp_path, source, live=True, approved=True,
                                 scope_options={"automatic_reply": True}) as (
            window, client, protocol, _, sends,
        ):
            await window.capture_once(client, protocol)
            await window.approve_captured(client, capture_approval(window))
            if stage == "send":
                async with httpx.AsyncClient(transport=httpx.MockTransport(
                    lambda request: response(request, []),
                )) as model:
                    core.model = ModelClient(core.settings, model)
                    assert await core.run_once()
                monkeypatch.setattr(continuous, "deliver_tick", no_progress)
            with pytest.raises(ValueError, match="AUTOMATIC_REPLY_TIMEOUT"):
                await asyncio.wait_for(continuous.run(window, client, protocol), 5)
            assert window.status()["state"] == "STOPPED" and sends == []

    run(scenario())
