import asyncio
import json
from datetime import timedelta

import httpx
import pytest

from companion import live_text, private_life
from companion.core import Core
from companion.goals import CommitmentRequest, GoalProposal, GoalScope
from companion.model import ModelClient
from companion.transport import digest
from companion_onebot_bridge import continuous
from tests.conftest import run
from tests.test_input_interrupt_bridge import model_response
from tests.test_onebot_capture import capture_approval, source_window
from tests.test_onebot_direct import raw_event
from tests.test_owner_recall_bridge import recall_frame
from tests.test_private_companion import companion_options


@pytest.mark.parametrize("boundary", ["before_begin", "before_delegate", "after_dispatch"])
def test_notice_source_recall_preserves_actual_dispatch_state(core, tmp_path, boundary):
    async def source(ws):
        await ws.send(json.dumps(raw_event(1, text="please read this later")))
        await ws.wait_closed()

    async def scenario():
        async with source_window(core, tmp_path, source, live=True, approved=True,
                                 scope_options=companion_options(core)) as (
            window, client, protocol, _, sends,
        ), httpx.AsyncClient(transport=httpx.MockTransport(
            lambda request: model_response("task feedback"),
        )) as model_http:
            core.model = ModelClient(core.settings, model_http)
            await window.capture_once(client, protocol)
            await window.approve_captured(client, capture_approval(window))
            assert await core.run_once()
            await continuous.pump_private(window, client, protocol)
            assert len(sends) == 1
            owner = core.store.one("SELECT event_id FROM events WHERE actor_id='owner'")["event_id"]
            core.goals.accept_commitment(CommitmentRequest(
                proposal=GoalProposal(topic="read public source", source_event_ids=[owner],
                    scope=GoalScope(operation="read_content",
                        url="https://www.bilibili.com/video/BV1xx411c7mD")),
                accepted_event_id=owner, deadline=core.clock.now + timedelta(seconds=60),
            ))
            core.clock.advance(61)
            assert core.goals.claim() is None
            with Core(core.settings, core.model, core.clock) as independent:
                assert await independent.run_once("notice-boundary-review")
            notice = core.store.one("SELECT o.* FROM outbox o JOIN jobs j "
                                    "ON o.origin_event_id=j.event_id WHERE j.kind='activity'")
            usage_before = core.store.rows("SELECT * FROM model_usage ORDER BY rowid")
            receiver = continuous.OwnerSource(window, protocol, asyncio.Event())
            receipts = []
            leases = []
            cancel_requests = []

            async def cancel(body):
                return await client.post("/internal/v1/live-text/cancel-before-send", json=body)

            async def record(response):
                path = response.request.url.path
                if path == "/internal/v1/deliveries/lease":
                    await response.aread()
                    leases.extend(response.json()["items"])
                    item = leases[-1]
                    body = {"window_sha256": window.plan.sha256,
                            "message_id": item["message_id"], "lease_token": item["lease_token"],
                            "content_sha256": digest(item["content"])}
                    # A valid source or a pending chat cannot revoke a required task notice.
                    assert (await cancel(body)).status_code == 409
                    assert (await cancel(body | {"cause": "OWNER_INPUT_PENDING",
                                                "pending_event_id": owner})).status_code == 409
                if path.endswith("/begin-send") or path.endswith("/send-check"):
                    await response.aread()
                    receipts.append((path, response.status_code, response.json()))

            async def before_begin(request):
                if request.url.path.endswith("/begin-send") and boundary == "before_begin":
                    await revoke()
                if request.url.path.endswith("/cancel-before-send"):
                    cancel_requests.append(json.loads(request.content))

            async def revoke():
                receiver.put_recall(recall_frame(1, time=int(core.clock.now.timestamp())),
                                    core.clock.now, ("127.0.0.1", 12345))
                await continuous.flush_recalls(window, client)
                item = leases[-1]
                body = {"window_sha256": window.plan.sha256,
                        "message_id": item["message_id"], "lease_token": item["lease_token"],
                        "content_sha256": digest(item["content"])}
                assert (await cancel(body | {
                    "lease_token": "lease_" + "f" * 32})).status_code == 409
                key = private_life.DELIVERY + item["message_id"]
                authority = live_text._meta(core.store, key)
                assert authority["window_notice"] == "commitment"
                for change in ({"window_notice": "share"}, {"content_sha256": "0" * 64},
                               {"plan_sha256": "0" * 64}):
                    with core.store.transaction():
                        live_text._put(core.store, key, authority | change)
                    assert (await cancel(body)).status_code == 409
                with core.store.transaction():
                    live_text._put(core.store, key, authority)

            async def recall_before_delegate(request):
                if request.url.path == "/send_private_msg" and boundary == "before_delegate":
                    await revoke()

            async def lose_ack(response):
                if (response.request.url.path == "/send_private_msg"
                    and boundary == "after_dispatch"):
                    await revoke()
                    raise httpx.ReadTimeout("synthetic lost ACK", request=response.request)

            client.event_hooks["response"].append(record)
            client.event_hooks["request"].append(before_begin)
            protocol.event_hooks["request"].append(recall_before_delegate)
            protocol.event_hooks["response"].append(lose_ack)
            await continuous.pump_private(window, client, protocol)
            assert leases[0]["window_notice"] == "commitment" and "reply_revision" not in leases[0]
            current = core.store.one("SELECT * FROM outbox WHERE message_id=?",
                                     (notice["message_id"],))
            assert current["attempt_count"] == 1
            assert receipts[0][0].endswith("/begin-send")
            if boundary == "after_dispatch":
                await window.stop_core(client)
                assert len(sends) == 2 and window.status()["state"] == "STOPPED"
                assert window.status()["attempt_states"] == {"ACK": 1, "UNKNOWN": 1}
                assert current["state"] == "DELIVERY_UNCERTAIN"
                body = {"window_sha256": window.plan.sha256, "message_id": notice["message_id"],
                        "lease_token": leases[0]["lease_token"],
                        "content_sha256": digest(leases[0]["content"])}
                assert (await cancel(body)).status_code == 409
                assert (await cancel(body | {"reply_revision": 0})).status_code == 409
            else:
                assert len(sends) == 1 and window.status()["state"] == "OPEN"
                assert window.status()["attempt_states"] == {"ACK": 1, "NOT_SENT": 1}
                assert current["state"] == "CANCELLED"
                assert [status for _, status, _ in receipts] == (
                    [409] if boundary == "before_begin" else [200, 409])
                # Retry acknowledges the same durable receipt without releasing an attempt.
                assert (await cancel(cancel_requests[-1])).status_code == 200
                assert core.store.one("SELECT attempt_count FROM outbox WHERE message_id=?",
                                      (notice["message_id"],))["attempt_count"] == 1
                window.verify(active=False)
            assert core.store.rows("SELECT * FROM model_usage ORDER BY rowid") == usage_before

    run(scenario())
