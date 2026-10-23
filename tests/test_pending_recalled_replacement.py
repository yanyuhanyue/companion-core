"""An interrupted reply stays retired when its buffered replacement is recalled."""

import asyncio
import json

import httpx
import pytest

from companion import budget
from companion.core import Core
from companion.model import ModelClient
from companion.reply_segments import SegmentedReplyConfig
from companion_onebot_bridge import capture, continuous
from tests.conftest import run
from tests.test_animated_owner_images import AdvancingClock
from tests.test_continuous_private_replies import worker
from tests.test_input_interrupt_bridge import model_response
from tests.test_onebot_capture import capture_approval, source_window
from tests.test_onebot_direct import raw_event
from tests.test_owner_recall_bridge import recall_frame
from tests.test_private_companion import companion_options


@pytest.mark.parametrize("case", ["plain", "segmented_first", "segmented_tail"])
def test_recalled_buffered_replacement_cannot_resume_old_reply(core, tmp_path, monkeypatch, case):
    core.clock = AdvancingClock(core.clock.now)
    calls, retired = [], {}
    continue_owner = asyncio.Event()
    connections = 0
    old_parts = ["旧的第一段", "旧的第二段", "旧的第三段"]
    replacement_text, fresh_text = "被撤回的替代输入", "新的独立输入"

    def frame(number, text):
        return json.dumps(raw_event(number, text=text, time=int(core.clock.now.timestamp())))

    async def source(ws):
        nonlocal connections
        connections += 1
        if connections == 1:
            await ws.send(frame(1, "先回答原来的问题"))
        else:
            await continue_owner.wait()
            await ws.send(frame(3, fresh_text))
        await ws.wait_closed()

    def answer(request):
        calls.append(json.loads(request.content)["messages"])
        old = "旧的单条回复" if case == "plain" else "\n".join(old_parts)
        return model_response(old if len(calls) == 1 else "新的独立回复")

    async def scenario():
        options = companion_options(core)
        if case != "plain":
            policy = SegmentedReplyConfig(mode="semantic", interval_ms=0)
            options.update(segmented_reply=policy, max_send_attempts=5)
            options["core_options"]["segmented_reply"] = policy
        async with source_window(core, tmp_path, source, live=True, approved=True,
                                 scope_options=options) as (
            window, client, protocol, _, sends,
        ), httpx.AsyncClient(transport=httpx.MockTransport(answer)) as model_http:
            core.model = ModelClient(core.settings, model_http)
            await window.capture_once(client, protocol)
            await window.approve_captured(client, capture_approval(window))
            assert await core.run_once()
            old_event = window.db.execute("SELECT event_id FROM window_inputs").fetchone()[0]
            old_usage = core.store.one("SELECT * FROM model_usage")
            old_admission = core.store.one("SELECT * FROM model_admissions")
            assert old_usage["state"] == "SETTLED" and old_usage["billed_micro_usd"] > 0
            receiver = continuous.OwnerSource(window, protocol, asyncio.Event())
            prefix_count = int(case == "segmented_tail")
            if prefix_count:
                await continuous.pump_private(window, client, protocol)
                assert [item["message"][0]["data"]["text"] for item in sends] == old_parts[:1]

            async def pending_replacement(request):
                if request.url.path == "/send_private_msg":
                    raw = frame(2, replacement_text)
                    _, event = capture.input_event(window, raw, core.clock.now)
                    receiver.put(raw, core.clock.now, ("127.0.0.1", 12345), event["event_id"])

            protocol.event_hooks["request"].append(pending_replacement)
            await continuous.pump_private(window, client, protocol)
            protocol.event_hooks["request"].remove(pending_replacement)
            attempts_before = {"NOT_SENT": 1} | ({"ACK": 1} if prefix_count else {})
            assert window.status()["state"] == "OPEN"
            assert window.status()["attempt_states"] == attempts_before
            assert len(sends) == prefix_count
            old_deliveries = core.store.rows(
                "SELECT message_id,state,attempt_count,provider_message_id FROM outbox "
                "WHERE origin_event_id=? ORDER BY rowid", (old_event,),
            )
            assert [row["state"] for row in old_deliveries] == {
                "plain": ["CANCELLED"],
                "segmented_first": ["CANCELLED", "CANCELLED", "CANCELLED"],
                "segmented_tail": ["SENT", "CANCELLED", "CANCELLED"],
            }[case]
            assert [row["attempt_count"] for row in old_deliveries] == {
                "plain": [1], "segmented_first": [1, 0, 0], "segmented_tail": [1, 1, 0],
            }[case]
            cancelled = next(row for row in old_deliveries
                             if row["state"] == "CANCELLED" and row["attempt_count"] == 1)
            proof = core.store.one("SELECT value FROM runtime_meta WHERE key=?",
                                   ("input_cancel_before_send:" + cancelled["message_id"],))
            assert json.loads(proof["value"])["cause"] == "OWNER_INPUT_PENDING"

            observed = receiver.first()
            replacement_event = observed.event_id
            receiver.put_recall(recall_frame(2, time=int(core.clock.now.timestamp())),
                                core.clock.now, ("127.0.0.1", 12345))
            assert await continuous.flush_recalls(window, client)
            await continuous.capture_observed(window, client, protocol, observed)
            receiver.consume(observed)
            row = window.db.execute("SELECT * FROM window_inputs WHERE event_id=?",
                                     (replacement_event,)).fetchone()
            await continuous.approve_row(window, client, row)
            rows = window.db.execute("SELECT * FROM window_inputs ORDER BY rowid").fetchall()
            old_state = await window.input_state(client, rows[0])
            replacement_state = await window.input_state(client, rows[1])
            assert old_state["state"] == "SUPERSEDED"
            assert replacement_state["state"] == "RECALLED"
            for value in (old_state, replacement_state):
                assert not value["unsettled_model"] and not value["unsettled_delivery"]
            assert core.store.one("SELECT reply_revision FROM conversations")["reply_revision"] == 1
            assert core.store.one("SELECT state FROM jobs WHERE event_id=?", (old_event,))[
                "state"] == "SUCCEEDED"
            assert not core.store.one("SELECT 1 FROM events WHERE event_id=?", (replacement_event,))
            assert not core.store.one("SELECT 1 FROM jobs WHERE event_id=?", (replacement_event,))
            assert core.store.one("SELECT * FROM model_usage") == old_usage
            assert core.store.one("SELECT * FROM model_admissions") == old_admission
            assert budget.total_monthly(core.store, "2000-01-01") == old_usage["billed_micro_usd"]

            pause, stop = asyncio.Event(), asyncio.Event()

            def emitted(kind, **values):
                if kind == "automatic_input_retired":
                    retired[values["event_id"]] = values["state"]
                    if set(retired) == {old_event, replacement_event}:
                        continue_owner.set()
                elif kind == "automatic_input_delivered":
                    pause.set()

            monkeypatch.setattr(continuous, "emit", emitted)
            with Core(core.settings, core.model, core.clock) as independent:
                task = asyncio.create_task(worker(independent, stop))
                try:
                    result = await asyncio.wait_for(
                        continuous.run(window, client, protocol, pause=pause), 15,
                    )
                finally:
                    stop.set()
                    await task
            assert retired == {old_event: "SUPERSEDED", replacement_event: "RECALLED"}
            assert result["state"] == "OPEN" and result["new_inputs"] == 3
            assert result["attempt_states"] == {"ACK": 1 + prefix_count, "NOT_SENT": 1}
            assert [row[0] for row in window.db.execute(
                "SELECT state FROM window_inputs ORDER BY rowid",
            )] == ["SUPERSEDED", "RECALLED", "ACK"]
            assert [item["message"][0]["data"]["text"] for item in sends] == (
                old_parts[:prefix_count] + ["新的独立回复"])
            assert core.store.one("SELECT reply_revision FROM conversations")["reply_revision"] == 2
            assert core.store.rows(
                "SELECT message_id,state,attempt_count,provider_message_id FROM outbox "
                "WHERE origin_event_id=? ORDER BY rowid", (old_event,),
            ) == old_deliveries
            usages = core.store.rows("SELECT * FROM model_usage ORDER BY rowid")
            assert len(calls) == len(usages) == 2 and usages[0] == old_usage
            assert all(row["state"] == "SETTLED" for row in usages)
            assert budget.total_monthly(core.store, "2000-01-01") == sum(
                row["billed_micro_usd"] for row in usages)
            assert core.store.one("SELECT * FROM model_admissions WHERE request_id=?",
                                  (old_usage["request_id"],)) == old_admission
            assert replacement_text not in json.dumps(calls[-1], ensure_ascii=False)
            window.verify(active=False)

    run(scenario())
