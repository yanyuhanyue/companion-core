"""A verified owner frame outranks an autonomous share before its actual transport call."""

import asyncio
import json

import httpx

from companion.core import Core
from companion.model import EXTRACT_RULES, RECALL_RULES, ModelClient
from companion_onebot_bridge import capture, continuous
from tests.conftest import run
from tests.test_input_interrupt_bridge import model_response
from tests.test_onebot_capture import capture_approval, source_window
from tests.test_onebot_direct import raw_event
from tests.test_onebot_window import reply_approval
from tests.test_private_companion import companion_options


def test_queued_owner_input_cancels_proactive_before_dispatch_without_unknown(core, tmp_path):
    cancel_requests = []

    async def source(ws):
        await ws.send(json.dumps(raw_event(1, text="我喜欢龙井茶")))
        await ws.wait_closed()

    def answer(request):
        messages = json.loads(request.content)["messages"]
        if messages[0]["content"] == EXTRACT_RULES:
            event_id = json.loads(messages[-1]["content"])[0]["event_id"]
            text = json.dumps({"memories": [{
                "subject": "user", "kind": "preference", "fact_key": "drink",
                "content": "用户喜欢龙井茶", "evidence_event_id": event_id,
                "evidence_quote": "我喜欢龙井茶",
            }]}, ensure_ascii=False)
        elif messages[0]["content"] == RECALL_RULES:
            text = '{"relevant":["m0"]}'
        else:
            text = "我们聊聊龙井茶。"
        return model_response(text)

    async def scenario():
        async with source_window(core, tmp_path, source, live=True, approved=True,
                                 scope_options=companion_options(core)) as (
            window, client, protocol, _, sends,
        ), httpx.AsyncClient(transport=httpx.MockTransport(answer)) as model_http:
            core.model = ModelClient(core.settings, model_http)
            await window.capture_once(client, protocol)
            await window.approve_captured(client, capture_approval(window))
            assert await core.run_once()
            await window.send_once(client, protocol, reply_approval(window.plan, core))
            assert len(sends) == 1 and await core.extract_once()
            with Core(core.settings, core.model, core.clock) as restarted:
                restarted.clock.advance(3601)
                assert restarted.schedule_proactive() and await restarted.run_once()
            assert core.store.one(
                "SELECT state FROM outbox WHERE action_type='qq.private.proactive'",
            )["state"] == "PENDING"
            receiver = continuous.OwnerSource(window, protocol, asyncio.Event())

            async def before(request):
                if request.url.path == "/send_private_msg":
                    raw = raw_event(2, text="等等，先聊我的事情")
                    raw["time"] = int(core.clock.now.timestamp())
                    frame = json.dumps(raw)
                    _, event = capture.input_event(window, frame, core.clock.now)
                    receiver.put(frame, core.clock.now, ("127.0.0.1", 12345), event["event_id"])

            async def cancellation(request):
                if request.url.path.endswith("/cancel-before-send"):
                    cancel_requests.append(json.loads(request.content))

            protocol.event_hooks["request"].append(before)
            client.event_hooks["request"].append(cancellation)
            await continuous.pump_private(window, client, protocol)
            assert len(sends) == 1 and window.status()["state"] == "OPEN"
            assert window.status()["attempt_states"] == {"ACK": 1, "NOT_SENT": 1}
            assert core.store.one(
                "SELECT state FROM outbox WHERE action_type='qq.private.proactive'",
            )["state"] == "CANCELLED"
            assert core.store.one("SELECT reply_revision FROM conversations")["reply_revision"] == 1
            assert len(cancel_requests) == 1
            assert cancel_requests[0]["cause"] == "OWNER_INPUT_PENDING"
            assert cancel_requests[0]["pending_event_id"] == receiver.first().event_id
            assert "reply_revision" not in cancel_requests[0]
            window.verify(active=False)

    run(scenario())
