"""Canonical notice identities and durable authenticated recall delivery."""

import asyncio
import json
from dataclasses import replace

import httpx
import pytest

from companion.model import ModelClient
from companion_onebot_bridge import continuous
from companion_onebot_bridge.event_normalizer import normalize, normalize_recall
from tests.conftest import run
from tests.test_input_interrupt_bridge import model_response
from tests.test_onebot_capture import capture_approval, source_window
from tests.test_onebot_direct import config, raw_event
from tests.test_onebot_window import reply_approval
from tests.test_private_companion import companion_options


def recall_frame(message_id=1, **changes):
    return json.dumps({"time": raw_event()["time"], "self_id": 10002, "post_type": "notice",
                       "notice_type": "friend_recall", "user_id": 10001,
                       "message_id": message_id, **changes})


def test_recall_uses_canonical_inbound_id_and_isolates_protocol_epoch():
    cfg = config()
    incoming = normalize(json.dumps(raw_event(-123)), cfg)
    recall = normalize_recall(recall_frame(-123), cfg)
    assert recall["target_event_id"] == incoming["event_id"]
    assert recall["target_source_event_id"] == incoming["source_event_id"]
    assert normalize_recall(recall_frame("-123"), cfg) == recall
    assert normalize_recall(recall_frame(-123), replace(cfg, protocol_epoch="another-epoch"))[
        "target_event_id"] != recall["target_event_id"]
    assert normalize_recall(recall_frame(-123, user_id=10003), cfg) is None
    assert normalize_recall(recall_frame(-123, self_id=10003), cfg) is None


@pytest.mark.parametrize("change", [{"message_id": 0}, {"group_id": 10004},
                                    {"message_id": True}, {"time": True}])
def test_recall_rejects_noncanonical_notice(change):
    with pytest.raises(ValueError):
        normalize_recall(recall_frame(**change), config())


def test_pending_recall_is_signed_persistent_and_survives_window_stop(core, tmp_path):
    async def source(ws):
        await ws.wait_closed()

    async def scenario():
        async with source_window(core, tmp_path, source, live=True, approved=True,
                                 scope_options=companion_options(core)) as (
            window, client, protocol, _, sends,
        ):
            receiver = continuous.OwnerSource(window, protocol, asyncio.Event())
            receiver.put_recall(recall_frame(), core.clock.now, ("127.0.0.1", 12345))
            receiver.put_recall(recall_frame(), core.clock.now, ("127.0.0.1", 12345))
            row = window.db.execute("SELECT * FROM automatic_recalls").fetchone()
            assert row["state"] == "PENDING"
            receipt = json.loads(row["record_json"])
            assert continuous.verify_recall(window, receipt) == receipt
            tampered = receipt | {"target_event_id": "evt_" + "0" * 64}
            with pytest.raises(ValueError, match="AUTHENTICATED_RECALL_REQUIRED"):
                continuous.verify_recall(window, tampered)
            resumed = continuous.OwnerSource(window, protocol, asyncio.Event())
            resumed.put_recall(recall_frame(), core.clock.now, ("127.0.0.1", 12345))
            assert window.db.execute("SELECT COUNT(*) FROM automatic_recalls").fetchone()[0] == 1
            await window.stop_live(client)
            assert window.db.execute("SELECT COUNT(*) FROM automatic_recalls").fetchone()[0] == 1
            assert await continuous.flush_recalls(window, client)
            saved = window.db.execute("SELECT * FROM automatic_recalls").fetchone()
            assert saved["state"] == "ACCEPTED"
            assert json.loads(saved["response_json"])["state"] == "PENDING_TARGET"
            assert not await continuous.flush_recalls(window, client)
            assert window.status()["state"] == "STOPPED" and sends == []
            assert core.store.rows("SELECT * FROM model_usage") == []

    run(scenario())


def test_notice_before_inbound_prevents_generation_and_does_not_stop_window(
    core, tmp_path, monkeypatch,
):
    async def source(ws):
        await ws.send(recall_frame())
        await ws.send(recall_frame())
        await ws.send(json.dumps(raw_event(1, text="这段内容已经撤回")))
        await ws.wait_closed()

    async def scenario():
        async with source_window(core, tmp_path, source, live=True, approved=True,
                                 scope_options=companion_options(core)) as (
            window, client, protocol, _, sends,
        ):
            pause = asyncio.Event()

            def emitted(kind, **values):
                if kind == "automatic_input_retired":
                    assert values["state"] == "RECALLED"
                    pause.set()

            monkeypatch.setattr(continuous, "emit", emitted)
            result = await asyncio.wait_for(
                continuous.run(window, client, protocol, pause=pause), 5,
            )
            assert result["state"] == "OPEN" and result["new_inputs"] == 1
            assert result["attempt_states"] == {} and sends == []
            assert window.db.execute("SELECT state FROM window_inputs").fetchone()[0] == "RECALLED"
            assert window.db.execute("SELECT body FROM inbox").fetchone()[0] is None
            assert not list((window.spool.directory / "captures").glob("*.json"))
            assert core.store.rows("SELECT * FROM jobs") == []
            assert core.store.rows("SELECT * FROM model_usage") == []
            window.verify()

    run(scenario())


def test_once_capture_routes_recall_and_skips_withdrawn_reference_io(core, tmp_path):
    async def source(ws):
        await ws.send(recall_frame())
        await ws.send(json.dumps(raw_event(1, message=[
            {"type": "text", "data": {"text": "撤回的引用"}},
            {"type": "reply", "data": {"id": "42"}},
        ])))
        await ws.wait_closed()

    def protocol_reply(request):
        assert request.url.path not in {"/get_msg", "/get_forward_msg"}

    async def scenario():
        async with source_window(core, tmp_path, source, live=True, approved=True,
                                 scope_options=companion_options(core),
                                 rich_input_scope="references_v1",
                                 protocol_reply=protocol_reply) as (
            window, client, protocol, _, sends,
        ):
            await window.capture_once(client, protocol)
            await window.approve_captured(client, capture_approval(window))
            row = window.db.execute("SELECT * FROM window_inputs").fetchone()
            state = await window.input_state(client, row)
            assert state["state"] == "RECALLED"
            window.retire_input(row, state)
            assert window.status()["state"] == "OPEN" and sends == []
            assert core.store.rows("SELECT * FROM jobs") == []
            assert core.store.rows("SELECT * FROM model_usage") == []

    run(scenario())


@pytest.mark.parametrize("phase", ["before_delegate", "after_delegate"])
def test_recall_dispatch_boundary_distinguishes_not_sent_from_unknown(core, tmp_path, phase):
    receiver = None
    invoked = []

    async def source(ws):
        await ws.send(json.dumps(raw_event(1, text="待撤回的消息")))
        await ws.wait_closed()

    def remote(request):
        if request.url.path == "/send_private_msg":
            invoked.append(request)
            if phase == "after_delegate":
                receiver.put_recall(recall_frame(), core.clock.now, ("127.0.0.1", 12345))
                raise httpx.ReadTimeout("synthetic post-dispatch uncertainty")

    async def scenario():
        nonlocal receiver
        async with source_window(core, tmp_path, source, live=True, approved=True,
                                 scope_options=companion_options(core), protocol_reply=remote) as (
            window, client, protocol, _, sends,
        ), httpx.AsyncClient(transport=httpx.MockTransport(
            lambda request: model_response("即将发送的回复"),
        )) as model_http:
            core.model = ModelClient(core.settings, model_http)
            await window.capture_once(client, protocol)
            await window.approve_captured(client, capture_approval(window))
            assert await core.run_once()
            receiver = continuous.OwnerSource(window, protocol, asyncio.Event())

            async def before(request):
                if request.url.path == "/send_private_msg" and phase == "before_delegate":
                    receiver.put_recall(recall_frame(), core.clock.now, ("127.0.0.1", 12345))

            protocol.event_hooks["request"].append(before)
            result = await window.send_once(client, protocol, reply_approval(window.plan, core))
            if phase == "before_delegate":
                assert result["state"] == "OPEN" and result["attempt_states"] == {"NOT_SENT": 1}
                assert sends == invoked == []
                assert core.store.one("SELECT state FROM outbox")["state"] == "CANCELLED"
                assert json.loads(window.db.execute("SELECT body FROM results").fetchone()[0])[
                    "result"] == "NOT_SENT"
                row = window.db.execute("SELECT * FROM window_inputs").fetchone()
                window.retire_input(row, await window.input_state(client, row))
                assert window.status()["new_inputs"] == 1
            else:
                assert result["state"] == "STOPPED" and result["attempt_states"] == {"UNKNOWN": 1}
                assert len(invoked) == 1 and sends == []
                assert core.store.one("SELECT state FROM outbox")["state"] == "DELIVERY_UNCERTAIN"
                assert (await continuous.run(window, client, protocol))["state"] == "STOPPED"
                assert len(invoked) == 1
            window.verify(active=False)
            assert core.store.one("SELECT state FROM model_usage")["state"] == "SETTLED"

    run(scenario())
