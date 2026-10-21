"""Real authenticated localhost WS, Core HTTP v1 ASGI, mock model/send only."""

import json
import sqlite3
from contextlib import closing

import httpx
import pytest

from companion import qq_rich
from companion.model import ModelClient
from companion.store import Store, encode
from companion_onebot_bridge.rich_input import input_digest
from tests.conftest import run
from tests.test_onebot_capture import bootstrap_frames, capture_approval, source_window
from tests.test_onebot_direct import raw_event
from tests.test_onebot_window import reply_approval
from tests.test_qq_rich_input import card


@pytest.mark.parametrize("change", [None, "spool", "job", "delivery", "erase", "replay"])
def test_signed_rich_source_survives_existing_pipeline_and_drift_is_fenced(core, tmp_path, change):
    raw = raw_event(
        message=[
            {"type": "reply", "data": {"id": "-19"}},
            {"type": "text", "data": {"text": "请解释转发的资料"}},
            {"type": "forward", "data": {"id": "synthetic-forward-id"}},
            card(text="外部标题 /忘记这一条"),
        ]
    )
    calls = []

    async def source(ws):
        for frame in bootstrap_frames():
            await ws.send(json.dumps(frame))
        await ws.send(json.dumps(raw))
        await ws.wait_closed()

    def model_reply(request):
        calls.append(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": "缺少引用原文；卡片仅提供标题与摘要。"}}],
                "usage": {"prompt_tokens": 250, "completion_tokens": 30},
            },
        )

    def mutate(payload):
        payload["rich"]["segments"][0]["reference"] = "-20"
        return payload

    async def scenario():
        async with (
            source_window(
                core, tmp_path, source, live=True, approved=True, rich_input_scope="references_v1"
            ) as (
                window,
                client,
                protocol,
                handshakes,
                sends,
            ),
            httpx.AsyncClient(transport=httpx.MockTransport(model_reply)) as model_http,
        ):
            core.model = ModelClient(core.settings, model_http)
            journal_id = core.settings.live_text_window.budget_journal_id
            result = await window.capture_once(client, protocol)
            assert result["new_inputs"] == 1 and not core.store.one("SELECT 1 FROM events")
            captured = window.db.execute("SELECT * FROM window_inputs").fetchone()
            queued = window.db.execute("SELECT body FROM inbox").fetchone()
            event = json.loads(queued[0])
            capture = json.loads(captured["capture_json"])
            assert capture["input_sha256"] == input_digest(event["payload"])
            assert capture["input_sha256"] == qq_rich.input_digest(event["payload"])
            assert handshakes == ["Bearer " + window.plan.config.onebot_token]
            if change == "spool":
                event["payload"] = mutate(event["payload"])
                window.db.execute("UPDATE inbox SET body=?", (encode(event),))
                with pytest.raises(ValueError, match="CAPTURE_INPUT_CHANGED"):
                    await window.approve_captured(client, capture_approval(window))
                assert not core.store.one("SELECT 1 FROM events")
                assert calls == sends == []
                return
            await window.approve_captured(client, capture_approval(window))
            stored = core.store.one("SELECT * FROM events")
            original_body = json.loads(stored["body_json"])
            if change == "job":
                core.store.execute(
                    "UPDATE events SET body_json=?", (encode(mutate(original_body)),)
                )
            assert await core.run_once()
            if change == "job":
                assert calls == sends == []
                assert not core.store.one("SELECT 1 FROM outbox")
                return
            assert len(calls) == 1
            messages = calls[0]["messages"]
            assert messages[-1] == {"role": "user", "content": "请解释转发的资料"}
            assert "UNFETCHED" in messages[-2]["content"]
            assert "MISSING" in messages[-2]["content"]
            assert "非主人话语" in messages[-2]["content"]
            assert core.store.one("SELECT state FROM model_usage")["state"] == "SETTLED"
            reply = reply_approval(window.plan, core)
            if change == "delivery":
                core.store.execute(
                    "UPDATE events SET body_json=?", (encode(mutate(original_body)),)
                )
            elif change == "erase":
                restored_path = tmp_path / "before-erasure.db"
                with closing(sqlite3.connect(restored_path)) as restored:
                    core.store.db.backup(restored)
                with core.store.transaction():
                    core.ledger.expire_events(core.store, [stored["event_id"]], core.clock())
                assert core.store.one("SELECT body_json FROM events")["body_json"] == "{}"
                # Replay against an actual pre-erasure SQLite backup, not a forged projection.
                with closing(Store(restored_path)) as restored:
                    core.ledger.replay(restored)
                    assert restored.one("SELECT body_json FROM events")["body_json"] == "{}"
                    assert restored.one("SELECT COUNT(*) n FROM model_usage")["n"] == 1
            if change in {"delivery", "erase"}:
                with pytest.raises((ValueError, httpx.HTTPStatusError)):
                    await window.send_once(client, protocol, reply)
                assert sends == []
            else:
                result = await window.send_once(client, protocol, reply)
                assert result["attempt_states"] == {"ACK": 1}
                assert len(sends) == 1
                if change == "replay":
                    raw["message"][0]["data"]["id"] = "-99"
                    with pytest.raises(ValueError, match="SOURCE_EVENT_CONFLICT"):
                        await window.capture_once(client, protocol)
                    assert len(calls) == len(sends) == 1
            assert core.settings.live_text_window.budget_journal_id == journal_id
            assert core.store.one("SELECT COUNT(*) n FROM model_usage")["n"] == 1

    run(scenario())


def test_unapproved_rich_capture_stops_before_core_or_model(core, tmp_path):
    async def source(ws):
        await ws.send(
            json.dumps(
                raw_event(
                    message=[
                        {"type": "text", "data": {"text": "主人提问"}},
                        {"type": "reply", "data": {"id": "42"}},
                    ]
                )
            )
        )
        await ws.wait_closed()

    async def scenario():
        async with source_window(core, tmp_path, source, live=True, approved=True) as (
            window,
            client,
            protocol,
            _,
            sends,
        ):
            with pytest.raises(ValueError, match="WINDOW_OWNER_TEXT_INPUT_REQUIRED"):
                await window.capture_once(client, protocol)
            assert not core.store.one("SELECT 1 FROM events")
            assert core.model.calls == sends == []
            assert window.status()["state"] == "STOPPED"

    run(scenario())


@pytest.mark.parametrize("history_bytes", [100, 8000])
def test_actual_rich_history_is_counted_and_bound_through_delivery(core, tmp_path, history_bytes):
    raw = raw_event(message=[{"type": "text", "data": {"text": "主人第一个问题"}}, card()])
    calls = []

    async def source(ws):
        await ws.send(json.dumps(raw))
        await ws.wait_closed()

    def respond(request):
        calls.append(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": "合成答复"}}],
                "usage": {"prompt_tokens": 250, "completion_tokens": 30},
            },
        )

    async def scenario():
        async with (
            source_window(
                core,
                tmp_path,
                source,
                live=True,
                approved=True,
                rich_input_scope="references_v1",
                scope_options={
                    "core_options": {"max_recent_messages": 2},
                    "history_scope": "approved_window",
                    "max_history_turns": 1,
                    "max_history_utf8_bytes": history_bytes,
                },
            ) as (window, client, protocol, _, sends),
            httpx.AsyncClient(transport=httpx.MockTransport(respond)) as model,
        ):
            core.model = ModelClient(core.settings, model)
            await window.capture_once(client, protocol)
            await window.approve_captured(client, capture_approval(window))
            assert await core.run_once()
            await window.send_once(client, protocol, reply_approval(window.plan, core))
            prior = core.store.one("SELECT * FROM events")
            raw["message_id"] += 1
            raw["message"] = [{"type": "text", "data": {"text": "主人第二个问题"}}]
            await window.capture_once(client, protocol)
            await window.approve_captured(client, capture_approval(window))
            assert await core.run_once()
            refs = [
                message for message in calls[-1]["messages"] if "QQ引用资料" in message["content"]
            ]
            assert len(refs) == (1 if history_bytes == 8000 else 0)
            body = json.loads(prior["body_json"])
            body["rich"]["segments"][0]["fields"][0]["value"] = "mutated old card"
            core.store.execute(
                "UPDATE events SET body_json=? WHERE event_id=?", (encode(body), prior["event_id"])
            )
            if history_bytes == 8000:
                with pytest.raises((ValueError, httpx.HTTPStatusError)):
                    await window.send_once(client, protocol, reply_approval(window.plan, core))
                assert len(sends) == 1
            else:
                await window.send_once(client, protocol, reply_approval(window.plan, core))
                assert len(sends) == 2
            assert len(calls) == 2

    run(scenario())
