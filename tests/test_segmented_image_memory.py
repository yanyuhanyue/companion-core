"""Joint original-image/memory evidence must survive every real fragment boundary."""

import json

import httpx
import pytest

from companion import image_spool
from companion.reply_segments import SegmentedReplyConfig
from companion.store import encode
from companion_onebot_bridge.network import private_http_client
from companion_onebot_bridge.window import digest
from tests.conftest import run
from tests.support.onebot import provider
from tests.test_image_memory_context import PREFERENCE, image_memory_window

PARTS = [
    "图1是红色耳机，我会结合你已确认的轻便需求考虑。",
    "图2是蓝色耳机，图片本身无法证明哪款隔音更好。",
    "如果你补充重量和隔音参数，我可以继续帮你比较。",
]
REPLY = "".join(PARTS)


def scope(cap=5):
    policy = SegmentedReplyConfig(min_chars=12, interval_ms=500)
    return {
        "segmented_reply": policy, "max_send_attempts": cap,
        "core_options": {"segmented_reply": policy},
        "window_options": {"max_attempts": cap, "max_visible_replies": cap},
    }


def approval(window, row):
    return {
        "window_sha256": window.plan.sha256, "event_id": row["origin_event_id"],
        "message_id": row["message_id"],
        "reply_sha256": digest(json.loads(row["content_json"])["text"]),
        "evidence_sha256": "f" * 64,
    }


@pytest.mark.parametrize("no_candidates", [False, True])
def test_multi_image_memory_reply_sends_all_parts_from_one_final_generation(
    core, tmp_path, no_candidates,
):
    async def scenario():
        async with image_memory_window(
            core, tmp_path, no_candidates=no_candidates, scope_options=scope(), reply=REPLY,
        ) as values:
            window, client, protocol, sends, calls, downloads, _, _, _, _, _ = values
            assert await core.run_once()
            rows = core.store.rows("SELECT * FROM outbox ORDER BY segment_index")
            assert [json.loads(row["content_json"])["text"] for row in rows] == PARTS
            assert len(downloads) == 2 and len(calls) == (2 if no_candidates else 3)
            final = calls[-1]["messages"]
            assert len(final[-1]["content"]) == 3  # Owner text plus both original images.
            assert (PREFERENCE in encode(final)) == (not no_candidates)
            assert not list(image_spool.directory(core.store).glob("*.raw"))
            usage_ids = [row["request_id"] for row in core.store.rows(
                "SELECT request_id FROM model_usage ORDER BY rowid",
            )]
            for index, row in enumerate(rows):
                receipt = json.loads(core.store.one(
                    "SELECT value FROM runtime_meta WHERE key=?",
                    ("live_text_reply:" + row["message_id"],),
                )["value"])
                assert receipt["request_ids"] == usage_ids
                assert receipt["reply_segments"]["index"] == index
                if index:
                    waiting = await window.send_once(client, protocol, approval(window, row))
                    assert waiting["delivery_wait"] == "SEGMENT_NOT_READY"
                    assert len(sends) == index
                    core.clock.advance(0.5)
                await window.send_once(client, protocol, approval(window, row))
                assert window.count("window_inputs", "state='ACK'") == int(index == 2)
            assert [item["message"][0]["data"]["text"] for item in sends] == PARTS
            assert window.status()["attempt_states"] == {"ACK": 3}
            assert len(calls) == len(usage_ids)
            assert len(core.store.rows("SELECT * FROM memories")) == 2

    run(scenario())


@pytest.mark.parametrize("failure", ["memory_drift", "image_evidence_drift", "unknown_send"])
def test_joint_evidence_loss_or_unknown_send_stops_remaining_fragments(core, tmp_path, failure):
    async def scenario():
        async with image_memory_window(
            core, tmp_path, scope_options=scope(), reply=REPLY,
        ) as values:
            window, client, protocol, sends, calls, _, _, _, memory_id, _, _ = values
            assert await core.run_once()
            rows = core.store.rows("SELECT * FROM outbox ORDER BY segment_index")
            await window.send_once(client, protocol, approval(window, rows[0]))
            core.clock.advance(0.5)
            if failure == "memory_drift":
                core.store.execute(
                    "UPDATE memories SET content='已改变' WHERE memory_id=?", (memory_id,),
                )
            elif failure == "image_evidence_drift":
                core.store.execute("UPDATE observations SET source_sha256=?", ("0" * 64,))

            def uncertain(request):
                response = provider(request, sends)
                if request.url.path == "/send_private_msg":
                    raise httpx.ReadTimeout("synthetic lost acknowledgement", request=request)
                return response

            async with private_http_client(
                str(protocol.base_url).rstrip("/"), window.plan.config.onebot_token,
                transport=httpx.MockTransport(uncertain),
            ) as failing_protocol:
                if failure == "unknown_send":
                    result = await window.send_once(
                        client, failing_protocol, approval(window, rows[1]),
                    )
                    assert result["state"] == "STOPPED"
                    assert window.count("window_attempts", "state='UNKNOWN'") == 1
                else:
                    with pytest.raises((ValueError, httpx.HTTPStatusError)):
                        await window.send_once(client, protocol, approval(window, rows[1]))
            with pytest.raises(ValueError, match="WINDOW_STOPPED"):
                await window.send_once(client, protocol, approval(window, rows[2]))
            assert len(sends) == (2 if failure == "unknown_send" else 1)
            assert len(calls) == 3 and window.count("window_inputs", "state='ACK'") == 0

    run(scenario())


def test_joint_reply_keeps_full_text_when_remaining_send_budget_cannot_fit_parts(core, tmp_path):
    async def scenario():
        async with image_memory_window(
            core, tmp_path, scope_options=scope(cap=2), reply=REPLY,
        ) as values:
            window, client, protocol, sends, calls, *_ = values
            assert await core.run_once()
            rows = core.store.rows("SELECT * FROM outbox")
            assert len(rows) == 1 and json.loads(rows[0]["content_json"])["text"] == REPLY
            await window.send_once(client, protocol, approval(window, rows[0]))
            assert len(sends) == 1 and len(calls) == 3

    run(scenario())
