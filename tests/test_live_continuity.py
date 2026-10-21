"""Window-scoped continuity over actual loopback capture; no QQ or provider I/O."""

import json
from contextlib import asynccontextmanager

import httpx
import pytest
from cryptography.fernet import Fernet
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from websockets.asyncio.server import serve

from companion import live_text
from companion.backup import backup, restore
from companion.config import LiveTextWindowApproval
from companion.contracts import Candidate
from companion.core import Core
from companion.model import ModelClient
from companion.privacy import advance_epoch
from companion.store import after, encode, uid
from companion_onebot_bridge import network
from companion_onebot_bridge.spool import Spool
from companion_onebot_bridge.window import TestWindow
from tests.conftest import run
from tests.test_dialogue_context import sent_history
from tests.test_live_text_window import live_fixture
from tests.test_onebot_capture import bootstrap_frames, capture_approval
from tests.test_onebot_delivery import core_client, provider
from tests.test_onebot_direct import raw_event
from tests.test_onebot_window import reply_approval


@asynccontextmanager
async def continuity_window(
    core, tmp_path, *, turns=4, byte_limit=16000, texts=None, soft_limit=3200,
):
    texts = texts or ["选了茉莉绿茶", "现在说的这个呢？", "刚才聊到哪了？", "还是那个吗？", "继续"]
    calls, sends = [], []
    number = 0

    async def source(ws):
        nonlocal number
        number += 1
        for frame in bootstrap_frames():
            await ws.send(json.dumps(frame))
        await ws.send(json.dumps(raw_event(number, text=texts[number - 1])))
        await ws.wait_closed()

    def respond(request):
        calls.append(json.loads(request.content))
        return httpx.Response(200, json={
            "choices": [{"message": {"content": f"合成回答{len(calls)}"}}],
            "usage": {"prompt_tokens": 100, "completion_tokens": 20},
        })

    async with serve(source, "127.0.0.1", 0) as server:
        cfg, plan = live_fixture(
            core, tmp_path, core_options={"max_recent_messages": turns * 2,
                                          "context_soft_tokens": soft_limit},
            bridge_options={
                "capture_signing_key": Ed25519PrivateKey.generate().private_bytes_raw().hex(),
                "onebot_ws_url": f"ws://127.0.0.1:{server.sockets[0].getsockname()[1]}",
            },
            history_scope="approved_window" if turns else "none", max_history_turns=turns,
            max_history_utf8_bytes=byte_limit if turns else 0,
        )
        async with (
            core_client(core) as client,
            network.private_http_client(cfg.onebot_http_url, cfg.onebot_token,
                                        transport=httpx.MockTransport(
                                            lambda r: provider(r, sends))) as protocol,
            httpx.AsyncClient(transport=httpx.MockTransport(respond)) as model,
        ):
            client.base_url = cfg.core_url
            core.model = ModelClient(core.settings, model)
            with Spool(cfg) as spool:
                window = TestWindow(spool, plan, clock=lambda: core.clock.now)
                await window.open(client)

                async def capture_input():
                    await window.capture_once(client, protocol)
                    await window.approve_captured(client, capture_approval(window))

                async def finish():
                    assert await core.run_once("continuity-test")
                    return await window.send_once(client, protocol, reply_approval(plan, core))

                yield window, client, calls, sends, capture_input, finish


def test_five_authenticated_turns_use_only_approved_window_and_keep_one_attempt(core, tmp_path):
    sent_history(core, 999, "窗口前的旧输入", "窗口前的旧回复")

    async def scenario():
        async with continuity_window(core, tmp_path) as (
            window, client, calls, sends, capture_input, finish,
        ):
            for number in range(1, 6):
                await capture_input()
                status = await finish()
                prompt = calls[-1]["messages"]
                assert len(prompt) == number * 2
                assert [m["role"] for m in prompt] == ["system"] + [
                    role for _ in range(number - 1) for role in ("user", "assistant")
                ] + ["user"]
                assert "窗口前" not in encode(prompt)
                if number > 1:
                    assert prompt[1]["content"] == "选了茉莉绿茶"
                    assert prompt[2]["content"] == "合成回答1"
                assert status["attempt_states"] == {"ACK": number}
                assert status["model_attempts"] == number
            assert len(calls) == len(sends) == 5
            assert not await core.run_once()
            assert window.status()["state"] == "STOPPED"
            assert core.store.one("SELECT COUNT(*) n FROM model_usage")["n"] == 5
            assert core.store.one("SELECT COUNT(*) n FROM outbox")["n"] == 6  # plus old history
            assert window.status()["phone_receipts"] is None
            assert (await client.get("/internal/v1/ops/status")).status_code == 200

    run(scenario())


@pytest.mark.parametrize("turns,byte_limit,kept", [(0, 0, 0), (1, 16000, 1), (4, 1, 0)])
def test_scope_and_utf8_bound_drop_only_complete_turns(core, tmp_path, turns, byte_limit, kept):
    async def scenario():
        async with continuity_window(core, tmp_path, turns=turns, byte_limit=byte_limit) as (
            _, _, calls, _, capture_input, finish,
        ):
            for _ in range(3):
                await capture_input()
                await finish()
            assert len(calls[-1]["messages"]) == 2 + kept * 2
            assert calls[-1]["messages"][-1] == {"role": "user", "content": "刚才聊到哪了？"}

    run(scenario())


def mutate_source(core, kind):
    source = core.store.one("SELECT * FROM events ORDER BY rowid LIMIT 1")
    event_id = source["event_id"]
    if kind == "privacy":
        advance_epoch(core.store)
    elif kind in {"PENDING", "FAILED", "DELIVERY_UNCERTAIN"}:
        core.store.execute("UPDATE outbox SET state=? WHERE origin_event_id=?", (kind, event_id))
    elif kind == "UNKNOWN":
        core.store.execute("UPDATE model_usage SET state='UNKNOWN'")
    elif kind == "redacted":
        core.store.execute("UPDATE events SET redacted=1 WHERE event_id=?", (event_id,))
    elif kind in {"SUPERSEDED", "FORGOTTEN"}:
        text = json.loads(source["body_json"])["text"]
        memory_id = core.memory.add(Candidate(
            subject="user", kind="preference", fact_key="饮料", content=text,
            evidence_event_id=event_id, evidence_quote=text,
        ), core.clock(), confirmed=True)
        assert memory_id is not None
        core.store.execute("UPDATE memories SET status=? WHERE memory_id=?", (kind, memory_id))
    elif kind == "reply_text":
        core.store.execute("UPDATE outbox SET content_json=?", (encode({"text": "被替换的回答"}),))
    elif kind == "input_text":
        body = json.loads(source["body_json"])
        body["text"] = "被替换的输入"
        core.store.execute("UPDATE events SET body_json=? WHERE event_id=?",
                           (encode(body), event_id))
    elif kind == "actor":
        core.store.execute("UPDATE events SET actor_id='companion' WHERE event_id=?", (event_id,))
    elif kind == "source":
        core.store.execute("UPDATE events SET source_event_id='other-identity' WHERE event_id=?",
                           (event_id,))
    elif kind == "conversation":
        conversation = core.store.one("SELECT * FROM conversations LIMIT 1")
        conversation["conversation_id"] = uid("conv")
        conversation["platform_session_key"] = "synthetic-different-conversation"
        core.store.insert("conversations", **conversation)
        core.store.execute("UPDATE events SET conversation_id=? WHERE event_id=?",
                           (conversation["conversation_id"], event_id))
    elif kind == "action":
        core.store.execute("UPDATE outbox SET action_type='qq.private.proactive'")
    else:
        key = live_text.INPUT + event_id
        receipt = json.loads(core.store.one("SELECT value FROM runtime_meta WHERE key=?", (key,))[
            "value"
        ])
        if kind == "unsigned":
            receipt["capture"]["signature"] = None
        elif kind == "no_capture":
            receipt["capture"] = None
        elif kind == "capture_identity":
            receipt["capture"]["identity_sha256"] = "a" * 64
        elif kind == "window":
            receipt["window_sha256"] = "a" * 64
        elif kind == "receipt_epoch":
            receipt["privacy_epoch"] += 1
        core.store.execute("UPDATE runtime_meta SET value=? WHERE key=?", (encode(receipt), key))


@pytest.mark.parametrize("kind", [
    "privacy", "PENDING", "FAILED", "DELIVERY_UNCERTAIN", "UNKNOWN", "redacted",
    "SUPERSEDED", "FORGOTTEN", "input_text", "reply_text", "actor", "source",
    "unsigned", "no_capture", "window", "receipt_epoch", "conversation", "action",
    "capture_identity",
])
def test_selected_history_changed_in_final_preflight_causes_zero_new_io(core, tmp_path, kind):
    async def scenario():
        async with continuity_window(core, tmp_path) as (
            _, _, calls, sends, capture_input, finish,
        ):
            await capture_input()
            await finish()
            await capture_input()
            changed = []

            async def mutate(_request):
                mutate_source(core, kind)
                changed.append(kind)

            core.model.client.event_hooks["request"].append(mutate)
            assert await core.run_once()
            assert len(calls) == len(sends) == 1
            assert changed == [kind]
            assert core.store.one("SELECT COUNT(*) n FROM outbox")["n"] == 1
            assert core.store.one("SELECT state FROM jobs ORDER BY rowid DESC LIMIT 1")[
                "state"
            ] == "FAILED"
            frozen = json.loads(core.store.one(
                "SELECT value FROM runtime_meta WHERE key LIKE ? ORDER BY rowid DESC LIMIT 1",
                (live_text.CONTEXT + "%",),
            )["value"])
            assert len(frozen["sources"]) == 1

    run(scenario())


@pytest.mark.parametrize("kind", [
    "PENDING", "FAILED", "redacted", "actor", "source",
    "unsigned", "no_capture", "window", "receipt_epoch", "conversation", "action",
    "capture_identity",
])
def test_ineligible_previous_source_is_absent_from_actual_prompt(core, tmp_path, kind):
    async def scenario():
        async with continuity_window(core, tmp_path) as (
            _, _, calls, sends, capture_input, finish,
        ):
            await capture_input()
            await finish()
            await capture_input()
            mutate_source(core, kind)
            assert await core.run_once()
            assert len(calls) == 2 and len(sends) == 1
            assert len(calls[-1]["messages"]) == 2
            assert calls[-1]["messages"][-1] == {"role": "user", "content": "现在说的这个呢？"}
            assert core.store.one("SELECT state FROM jobs ORDER BY rowid DESC LIMIT 1")[
                "state"
            ] == "SUCCEEDED"

    run(scenario())


@pytest.mark.parametrize("delta,kept", [(-1, False), (0, True)])
def test_history_utf8_byte_boundary_keeps_a_pair_or_neither(core, tmp_path, delta, kept):
    bound = len("选了茉莉绿茶合成回答1".encode()) + delta

    async def scenario():
        async with continuity_window(core, tmp_path, byte_limit=bound) as (
            _, _, calls, _, capture_input, finish,
        ):
            await capture_input()
            await finish()
            await capture_input()
            await finish()
            assert len(calls[-1]["messages"]) == (4 if kept else 2)

    run(scenario())


def test_sources_removed_by_soft_bound_are_not_frozen_or_reintroduced(core, tmp_path):
    current = "当前完整输入" * 80

    async def scenario():
        async with continuity_window(core, tmp_path, soft_limit=1000,
                                     texts=["旧内容" * 150, current]) as (
            _, _, calls, _, capture_input, finish,
        ):
            await capture_input()
            await finish()
            await capture_input()

            async def change_dropped_source(_request):
                mutate_source(core, "input_text")

            core.model.client.event_hooks["request"].append(change_dropped_source)
            assert await core.run_once()
            assert len(calls) == 2
            assert len(calls[-1]["messages"]) == 2
            assert calls[-1]["messages"][-1] == {"role": "user", "content": current}
            frozen = json.loads(core.store.one(
                "SELECT value FROM runtime_meta WHERE key LIKE ? ORDER BY rowid DESC LIMIT 1",
                (live_text.CONTEXT + "%",),
            )["value"])
            assert frozen["sources"] == []

    run(scenario())


@pytest.mark.parametrize("changes", [
    {"history_scope": "none", "max_history_turns": 1},
    {"history_scope": "approved_window"},
    {"history_scope": "all_history", "max_history_turns": 1, "max_history_utf8_bytes": 500},
    {"history_scope": "approved_window", "max_history_turns": 5, "max_history_utf8_bytes": 500},
    {"history_scope": "approved_window", "max_history_turns": True, "max_history_utf8_bytes": 500},
    {"history_scope": "approved_window", "max_history_turns": 1, "max_history_utf8_bytes": 16001},
])
def test_history_scope_requires_explicit_finite_approval(core, tmp_path, changes):
    live_fixture(core, tmp_path)
    value = core.settings.live_text_window.model_dump()
    with pytest.raises(ValueError):
        LiveTextWindowApproval.model_validate({**value, **changes})


def test_core_restart_keeps_original_window_history_and_attempts(core, tmp_path):
    async def scenario():
        async with continuity_window(core, tmp_path) as (
            _, _, calls, sends, capture_input, finish,
        ):
            await capture_input()
            await finish()
            await capture_input()
            with Core(core.settings, core.model, core.clock) as restarted:
                assert await restarted.run_once()
                assert len(calls) == 2 and len(sends) == 1
                assert calls[-1]["messages"][1:3] == [
                    {"role": "user", "content": "选了茉莉绿茶"},
                    {"role": "assistant", "content": "合成回答1"},
                ]
                assert restarted.status()["live_text_window"]["model_attempts"] == 2
                assert not await restarted.run_once()
                assert len(calls) == 2

    run(scenario())


def test_erasure_and_old_database_replay_clear_history_generated_replies(core, tmp_path):
    async def scenario():
        async with continuity_window(core, tmp_path) as (
            _, _, calls, _, capture_input, finish,
        ):
            await capture_input()
            await finish()
            await capture_input()
            await finish()
            assert len(calls[-1]["messages"]) == 4
            source = core.store.one("SELECT event_id FROM events ORDER BY rowid LIMIT 1")[
                "event_id"
            ]
            key = Fernet.generate_key()
            snapshot, restored = tmp_path / "before-erasure.enc", tmp_path / "restored.db"
            backup(core.settings.database, snapshot, key)
            with core.store.transaction():
                core.ledger.expire_events(core.store, [source], core.clock())
            assert all(row["content_json"] == "{}" for row in core.store.rows(
                "SELECT content_json FROM outbox"
            ))
            assert core.status()["live_text_window"]["approved"] is False
            # New metadata stores only references/digests, never another plaintext copy.
            metadata = encode(core.store.rows("SELECT value FROM runtime_meta WHERE key LIKE ?",
                                              (live_text.CONTEXT + "%",)))
            assert "选了茉莉绿茶" not in metadata and "合成回答" not in metadata
            restore(snapshot, restored, key, core.settings.erasure_ledger)
            with Core(core.settings.model_copy(update={"database": str(restored)}),
                      core.model, core.clock) as reopened:
                assert reopened.store.one("SELECT redacted FROM events WHERE event_id=?",
                                          (source,))["redacted"] == 1
                assert all(row["content_json"] == "{}" for row in reopened.store.rows(
                    "SELECT content_json FROM outbox"
                ))
                assert reopened.status()["live_text_window"]["approved"] is False
                assert reopened.store.one("SELECT COUNT(*) n FROM model_admissions")["n"] == 2
                assert not await reopened.run_once()
                assert len(calls) == 2

    run(scenario())


@pytest.mark.parametrize("change_reply", [False, True])
def test_missing_context_after_model_receive_retains_cost_without_reply(
    core, tmp_path, change_reply,
):
    async def scenario():
        async with continuity_window(core, tmp_path) as (
            _, _, calls, sends, capture_input, finish,
        ):
            await capture_input()
            await finish()
            await capture_input()
            original = core.model.client._transport.handler

            def receive_then_remove(request):
                response = original(request)
                frozen = core.store.one(
                    "SELECT key FROM runtime_meta WHERE key LIKE ? ORDER BY rowid DESC LIMIT 1",
                    (live_text.CONTEXT + "%",),
                )
                core.store.execute("DELETE FROM runtime_meta WHERE key=?", (frozen["key"],))
                if change_reply:
                    mutate_source(core, "reply_text")
                return response

            core.model.client._transport.handler = receive_then_remove
            assert await core.run_once()
            assert len(calls) == 2 and len(sends) == 1
            assert core.store.one("SELECT COUNT(*) n FROM outbox")["n"] == 1
            result = core.store.one(
                "SELECT state,last_error_code FROM jobs ORDER BY rowid DESC LIMIT 1"
            )
            assert result == {
                "state": "FAILED", "last_error_code": "LIVE_TEXT_CONTEXT_REQUIRED",
            }
            usage = core.store.rows("SELECT state,billed_micro_usd FROM model_usage")
            assert len(usage) == 2 and all(
                row["state"] == "SETTLED" and row["billed_micro_usd"] > 0 for row in usage
            )

    run(scenario())


def test_send_check_refuses_missing_snapshot_after_completed_model(core, tmp_path):
    async def scenario():
        async with continuity_window(core, tmp_path) as (
            window, client, calls, sends, capture_input, finish,
        ):
            await capture_input()
            await finish()
            await capture_input()
            assert await core.run_once()
            row = core.store.one("SELECT * FROM outbox ORDER BY rowid DESC LIMIT 1")
            lease = "lease_" + "f" * 32
            core.store.execute(
                "UPDATE outbox SET state='SENDING',lease_token=?,lease_until=? WHERE message_id=?",
                (lease, after(core.clock(), 60), row["message_id"]),
            )
            check = {
                "window_sha256": window.plan.sha256, "event_id": row["origin_event_id"],
                "message_id": row["message_id"], "reply_sha256": reply_approval(window.plan, core)[
                    "reply_sha256"
                ], "lease_token": lease,
            }
            assert (await client.post("/internal/v1/live-text/send-check", json=check)).json()[
                "approved"
            ] is True
            core.store.execute("DELETE FROM runtime_meta WHERE key=?", (
                live_text.CONTEXT + core.store.one("SELECT job_id FROM jobs WHERE event_id=?",
                                                  (row["origin_event_id"],))["job_id"],
            ))
            response = await client.post("/internal/v1/live-text/send-check", json=check)
            assert response.status_code == 503
            assert response.json()["error"] == "LIVE_TEXT_CONTEXT_REQUIRED"
            assert len(calls) == 2 and len(sends) == 1
            assert core.store.one("SELECT state FROM outbox WHERE message_id=?",
                                  (row["message_id"],))["state"] == "SENDING"

    run(scenario())
