"""Explicit segmented reply scopes and complete delivered history, synthetic only."""

import hashlib
import json
from contextlib import asynccontextmanager

import httpx
import pytest
from pydantic import ValidationError

from companion import dialogue_history, live_text, paid_budget, transport
from companion.config import LiveTextWindowApproval, Settings
from companion.contracts import CoreError, DeliveryResult, LeaseRequest
from companion.model import ModelClient
from companion.reply_segments import SegmentedReplyConfig
from companion.store import encode
from tests.conftest import event, ingest, run
from tests.test_dialogue_context import current_job, sent_history
from tests.test_live_text_handoff import KEY, configure, reference


@asynccontextmanager
async def segmented_window(core, tmp_path, *, reply="第一段合成回答。第二段合成回答。", cap=5):
    approval = configure(core, tmp_path)
    core.settings.segmented_reply = SegmentedReplyConfig(min_chars=4, interval_ms=500)
    core.settings.max_recent_messages = 4
    approval = LiveTextWindowApproval.model_validate(approval.model_dump() | {
        "segmented_reply": core.settings.segmented_reply,
        "max_send_attempts": cap,
        "history_scope": "approved_window", "max_history_turns": 2,
        "max_history_utf8_bytes": 4000,
        "core_config_sha256": live_text.config_sha256(core.settings),
    })
    core.settings.live_text_window = approval
    assert live_text.open_window(core.store, core.settings, core.clock, reference(approval))[
        "approved"
    ]
    calls = []

    def respond(request):
        calls.append(json.loads(request.content))
        return httpx.Response(200, json={
            "choices": [{"message": {"content": reply}}],
            "usage": {"prompt_tokens": 100, "completion_tokens": 20},
        })

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        core.model = ModelClient(core.settings, client)
        yield approval, calls


def capture_input(core, number=1):
    approval = core.settings.live_text_window
    protocol_epoch = "SYNTHETIC_SEGMENTED_CAPTURE"
    source = (f"{core.settings.bot_qq_id}:onebot-direct:FriendMessage:"
              f"{core.settings.owner_qq_id}:epoch:{protocol_epoch}:{number}")
    inbound = event(core, number, text=f"这是第{number}轮合成输入。 ",
                    origin=f"onebot-direct:FriendMessage:{core.settings.owner_qq_id}").model_copy(update={
        "source_event_id": source,
        "event_id": "evt_" + hashlib.sha256(source.encode()).hexdigest(),
    })
    capture = live_text.CaptureReceipt(
        kind="authenticated_onebot_ws", window_sha256=approval.plan_sha256,
        event_id=inbound.event_id, source_event_id=source,
        input_sha256=paid_budget.sha(inbound.payload.text),
        frame_sha256="f" * 64, evidence_sha256="e" * 64, config_sha256="c" * 64,
        identity_sha256=paid_budget.sha([core.settings.owner_qq_id, core.settings.bot_qq_id]),
        protocol_epoch=protocol_epoch, received_at=core.clock(), occurred_at=core.clock(),
    )
    signed = capture.model_dump(mode="json", exclude={"signature"})
    for key in ("received_at", "occurred_at"):
        signed[key] = getattr(capture, key).isoformat()
    capture.signature = KEY.sign(live_text.CAPTURE_SIGNATURE_DOMAIN + encode(signed).encode()).hex()
    live_text.approve_input(core.store, core.settings, core.clock, live_text.InputApproval(
        window_sha256=approval.plan_sha256, event_id=inbound.event_id,
        input_sha256=capture.input_sha256, evidence_sha256="e" * 64, capture=capture,
    ))
    core.ingest(inbound, inbound.event_id)
    return core.store.one("SELECT j.*,e.conversation_id FROM jobs j JOIN events e USING(event_id) "
                          "WHERE j.event_id=?", (inbound.event_id,))


def send_next(core):
    leased = transport.lease_deliveries(
        core.store, core.settings, LeaseRequest(worker_id="segment-test", limit=5), core.clock(),
    )["items"]
    assert len(leased) == 1
    item = leased[0]
    transport.begin_send(core.store, item["message_id"], item["lease_token"], core.clock(),
                         core.settings, clock=core.clock)
    row = core.store.one("SELECT * FROM outbox WHERE message_id=?", (item["message_id"],))
    checked = live_text.send_check(core.store, core.settings, core.clock, live_text.SendCheck(
        window_sha256=core.settings.live_text_window.plan_sha256,
        event_id=row["origin_event_id"], message_id=item["message_id"],
        reply_sha256=paid_budget.sha(item["content"]["text"]), lease_token=item["lease_token"],
    ))
    assert checked["approved"]
    transport.delivery_result(core.store, core.settings, item["message_id"], DeliveryResult(
        lease_token=item["lease_token"], result="SENT", provider_message_id="synthetic-sent",
    ), core.clock())
    return item


def test_segmented_scope_requires_explicit_matching_send_limit(core, tmp_path):
    approval = configure(core, tmp_path)
    value = approval.model_dump(mode="json")
    policy = {"max_segments": 3, "min_chars": 12, "interval_ms": 500}
    settings = Settings.model_validate(core.settings.model_dump() | {
        "segmented_reply": policy,
    })
    scoped = LiveTextWindowApproval.model_validate(value | {
        "segmented_reply": policy, "max_send_attempts": 5,
    })
    assert settings.segmented_reply == scoped.segmented_reply == SegmentedReplyConfig(**policy)
    with pytest.raises(ValidationError):
        LiveTextWindowApproval.model_validate(value | {"segmented_reply": policy})
    plain = LiveTextWindowApproval.model_validate(value | {"max_send_attempts": 5})
    assert plain.segmented_reply is None and plain.max_send_attempts == 5


def test_owner_image_scope_can_explicitly_enable_segmented_replies(core, tmp_path):
    value = configure(core, tmp_path).model_dump(mode="json")
    scoped = LiveTextWindowApproval.model_validate(value | {
        "image_scope": "owner_image_v1", "rich_input_scope": "references_v1",
        "image_approval_sha256": "a" * 64,
        "segmented_reply": {"max_segments": 3, "min_chars": 12, "interval_ms": 500},
        "max_send_attempts": 5,
    })
    assert scoped.segmented_reply.max_segments == 3


def test_live_guard_refuses_segmentation_not_present_in_the_approved_scope(core, tmp_path):
    approval = configure(core, tmp_path)
    core.settings.segmented_reply = SegmentedReplyConfig()
    core.settings.live_text_window = approval.model_copy(update={
        "core_config_sha256": live_text.config_sha256(core.settings),
    })
    with pytest.raises(CoreError, match="LIVE_TEXT_REPLY_SCOPE_CHANGED"):
        live_text.guard(core.store, core.settings, core.clock, opening=True)


def test_legacy_approval_and_settings_keep_their_serialized_shape(core, tmp_path):
    approval = configure(core, tmp_path)
    assert "segmented_reply" not in core.settings.model_dump(mode="json")
    assert {"segmented_reply", "max_send_attempts"}.isdisjoint(approval.model_dump(mode="json"))


def test_segmented_receipts_share_one_model_result_and_history_is_one_complete_turn(core, tmp_path):
    async def scenario():
        async with segmented_window(core, tmp_path) as (approval, calls):
            job = capture_input(core)
            assert await core.run_once()
            rows = core.store.rows("SELECT * FROM outbox ORDER BY segment_index")
            assert len(rows) == 2
            assert len(calls) == 1
            usage = core.store.rows("SELECT * FROM model_usage")
            assert len(usage) == 1 and usage[0]["state"] == "SETTLED"
            receipts = [json.loads(core.store.one("SELECT value FROM runtime_meta WHERE key=?",
                ("live_text_reply:" + row["message_id"],))["value"]) for row in rows]
            assert {r["request_id"] for r in receipts} == {usage[0]["request_id"]}
            assert [r["reply_segments"]["index"] for r in receipts] == [0, 1]
            assert all(r["reply_segments"]["total_chars"] == len("第一段合成回答。第二段合成回答。")
                       for r in receipts)
            assert live_text.remaining_send_slots(core.store, approval) == 3
            send_next(core)
            core.clock.advance(0.5)
            send_next(core)
            next_job = capture_input(core, 2)
            sources = live_text.history_sources(core.store, core.settings, core.clock, next_job)
            assert len(sources) == 1
            assert sources[0]["event_id"] == job["event_id"]
            assert sources[0]["reply_text"] == "第一段合成回答。第二段合成回答。"
            pinned = live_text._history_fingerprint(
                core.store, core.settings, core.clock, approval, sources[0],
            )
            assert pinned["message_ids"] == [row["message_id"] for row in rows]
            assert pinned["reply_receipts_sha256"] == paid_budget.sha(receipts)
            assert await core.run_once()
            assert [m for m in calls[-1]["messages"] if m["role"] == "assistant"] == [
                {"role": "assistant", "content": "第一段合成回答。第二段合成回答。"},
            ]
            assert len(calls) == 2

    run(scenario())


@pytest.mark.parametrize("cap,count", [(1, 1), (2, 2)])
def test_send_budget_falls_back_to_full_text_and_allows_already_reserved_exact_capacity(
    core, tmp_path, cap, count,
):
    async def scenario():
        async with segmented_window(core, tmp_path, cap=cap) as (approval, calls):
            capture_input(core)
            assert await core.run_once()
            rows = core.store.rows("SELECT * FROM outbox ORDER BY segment_index")
            assert len(rows) == count
            assert "".join(json.loads(row["content_json"])["text"] for row in rows) == (
                "第一段合成回答。第二段合成回答。"
            )
            assert live_text.remaining_send_slots(core.store, approval) == 0
            for _ in rows:
                send_next(core)
                core.clock.advance(0.5)
            assert len(calls) == 1
            assert live_text.remaining_send_slots(core.store, approval) == 0
            job = capture_input(core, 2)
            assert await core.run_once()
            assert len(core.store.rows("SELECT * FROM outbox")) == count
            assert core.store.one("SELECT state FROM jobs WHERE job_id=?", (job["job_id"],))[
                "state"
            ] == "FAILED"

    run(scenario())


@pytest.mark.parametrize("segment_index", [0, 1])
def test_any_changed_segment_receipt_invalidates_delivery_and_history(
    core, tmp_path, segment_index,
):
    async def scenario():
        async with segmented_window(core, tmp_path) as (approval, _):
            capture_input(core)
            assert await core.run_once()
            rows = core.store.rows("SELECT * FROM outbox ORDER BY segment_index")
            assert len(rows) == 2
            send_next(core)
            core.clock.advance(0.5)
            send_next(core)
            job = capture_input(core, 2)
            sources = live_text.history_sources(core.store, core.settings, core.clock, job)
            assert len(sources) == 1
            key = "live_text_reply:" + rows[segment_index]["message_id"]
            receipt = json.loads(core.store.one("SELECT value FROM runtime_meta WHERE key=?",
                                               (key,))["value"])
            receipt["reply_segments"]["group_sha256"] = "f" * 64
            core.store.execute("UPDATE runtime_meta SET value=? WHERE key=?",
                               (encode(receipt), key))
            assert live_text._history_fingerprint(
                core.store, core.settings, core.clock, approval, sources[0],
            ) is None
            assert live_text.history_sources(core.store, core.settings, core.clock, job) == []
            with pytest.raises(CoreError, match="LIVE_TEXT_REPLY_RECEIPT_CHANGED"):
                live_text.check_delivery(
                    core.store, core.settings, core.clock, rows[0]["message_id"],
                )

    run(scenario())


@pytest.mark.parametrize("state", ["PENDING", "LEASED", "SENDING", "FAILED", "CANCELLED",
                                      "DELIVERY_UNCERTAIN", "RETRY_WAIT"])
def test_partial_group_is_absent_before_history_limit_and_complete_group_is_one_turn(core, state):
    sent_history(core, 1, "更早的完整输入。", "更早的完整回答。")
    ingest(core, 2, "较新的分条输入。")
    job = core.store.one("SELECT j.*,e.conversation_id FROM jobs j JOIN events e USING(event_id) "
                          "WHERE j.event_id='evt_test_2'")
    parts = ["这是第一条。", "这是第二条。", "这是第三条。"]
    ids = transport.enqueue_reply_parts(core.store, job, parts, 500, core.clock())
    core.store.execute("UPDATE jobs SET state='SUCCEEDED' WHERE job_id=?", (job["job_id"],))
    core.store.execute("UPDATE outbox SET state='SENT' WHERE origin_event_id=?", (job["event_id"],))
    core.store.execute("UPDATE outbox SET state=? WHERE message_id=?", (state, ids[1]))
    current = current_job(core)
    sources = dialogue_history.recent_sent_sources(
        core.store, current["conversation_id"], current["event_id"], 1,
    )
    assert len(sources) == 1 and sources[0]["event_id"] == "evt_test_1"
    assert "reply_segments" not in sources[0]
    core.store.execute("UPDATE outbox SET state='SENT' WHERE message_id=?", (ids[1],))
    sources = dialogue_history.recent_sent_sources(
        core.store, current["conversation_id"], current["event_id"], 1,
    )
    assert len(sources) == 1 and sources[0]["reply_text"] == "".join(parts)
    assert [part["message_id"] for part in sources[0]["reply_segments"]] == ids
    assert dialogue_history.turn_messages(sources[0])[-1] == {
        "role": "assistant", "content": "".join(parts),
    }


def test_remaining_send_slots_include_queued_parts_and_retry_attempts_from_this_window(
    core, tmp_path,
):
    ids = [sent_history(core, number, f"合成输入{number}", state="PENDING")
           for number in range(1, 5)]
    approval = configure(core, tmp_path).model_copy(update={
        "segmented_reply": SegmentedReplyConfig(), "max_send_attempts": 5,
    })
    for number in range(1, 5):
        core.store.insert("runtime_meta", key=live_text.INPUT + f"evt_test_{number}", value=encode({
            "window_sha256": approval.plan_sha256 if number < 4 else "old-window",
        }))
    core.store.execute("UPDATE outbox SET state='SENT',attempt_count=1 WHERE message_id=?",
                       (ids[0],))
    core.store.execute("UPDATE outbox SET state='RETRY_WAIT',attempt_count=1 WHERE message_id=?",
                       (ids[1],))
    core.store.execute("UPDATE outbox SET attempt_count=99 WHERE message_id=?", (ids[3],))
    assert live_text.remaining_send_slots(core.store, approval) == 1
    core.store.execute("UPDATE outbox SET state='SENDING',attempt_count=2 WHERE message_id=?",
                       (ids[1],))
    assert live_text.remaining_send_slots(core.store, approval) == 1
    core.store.execute("UPDATE outbox SET state='RETRY_WAIT',attempt_count=3 WHERE message_id=?",
                       (ids[1],))
    assert live_text.remaining_send_slots(core.store, approval) == -1


@pytest.mark.parametrize("corruption", ["missing", "unbound", "text"])
def test_damaged_group_cannot_be_reclassified_as_single_message_history(core, corruption):
    ingest(core, 1, "合成输入。")
    job = core.store.one("SELECT j.*,e.conversation_id FROM jobs j JOIN events e USING(event_id) "
                          "WHERE j.event_id='evt_test_1'")
    ids = transport.enqueue_reply_parts(core.store, job, ["第一片正文。", "第二片正文。"],
                                        500, core.clock())
    core.store.execute("UPDATE outbox SET state='SENT'")
    core.store.execute("UPDATE jobs SET state='SUCCEEDED'")
    current = current_job(core)
    if corruption == "missing":
        core.store.execute("DELETE FROM outbox WHERE message_id=?", (ids[0],))
    elif corruption == "unbound":
        core.store.execute("UPDATE outbox SET reply_group_id=NULL WHERE message_id=?", (ids[-1],))
    else:
        core.store.execute("UPDATE outbox SET content_json=? WHERE message_id=?",
                           (encode({"text": "被替换的正文。"}), ids[0]))
    assert dialogue_history.recent_sent_sources(
        core.store, current["conversation_id"], current["event_id"], 2,
    ) == []


def test_reply_limit_applies_to_the_full_answer_before_any_segment_is_deliverable(core, tmp_path):
    async def scenario():
        reply = "首" * 250 + "。" + "尾" * 250
        async with segmented_window(core, tmp_path, reply=reply) as (_, calls):
            job = capture_input(core)
            assert await core.run_once()
            assert len(calls) == 1
            assert core.store.one("SELECT state FROM jobs WHERE job_id=?", (job["job_id"],))[
                "state"
            ] == "FAILED"
            assert core.store.rows("SELECT * FROM outbox") == []
            assert core.store.rows(
                "SELECT * FROM runtime_meta WHERE key LIKE 'live_text_reply:%'",
            ) == []

    run(scenario())
