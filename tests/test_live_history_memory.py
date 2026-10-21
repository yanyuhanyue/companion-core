"""Explicit B1 scopes: native retrieval, loopback capture and synthetic model/send I/O."""

import json
from copy import deepcopy

import httpx
import pytest
from cryptography.fernet import Fernet
from pydantic import ValidationError

from companion import live_text
from companion.backup import backup, restore
from companion.config import LiveTextWindowApproval
from companion.privacy import advance_epoch
from companion.store import after, encode
from companion_onebot_bridge.window import WindowPlan, digest, stored_plan
from tests.conftest import run
from tests.test_live_continuity import continuity_window
from tests.test_live_memory_recall import readonly_window
from tests.test_onebot_window import reply_approval


def combined_options(**changes):
    return {
        "history_scope": "approved_window",
        "history_approval_sha256": "e" * 64,
        "max_history_turns": 2,
        "max_history_utf8_bytes": 4000,
        **changes,
    }


def test_same_job_review_and_chat_use_authenticated_sent_history_and_native_fact(core, tmp_path):
    async def scenario():
        async with readonly_window(
            core,
            tmp_path,
            options=combined_options(),
            core_options={"max_recent_messages": 4},
            queries=[
                "本次临时项目在城西旧书店碰头，验收代号仍用已确认的那个。",
                "刚才这个项目在哪里碰头，长期确认的验收代号又是什么？",
            ],
        ) as (_, _, capture, send, calls, sends, scores, memory_id, _):
            await capture()
            assert await core.run_once()
            assert await send()
            await capture()
            assert await core.run_once()
            assert await send()
            assert len(calls) == 4 and len(sends) == 2
            assert all(ids == [memory_id] for ids in scores)
            review = json.loads(calls[2]["messages"][-1]["content"])
            assert review["recent_dialogue"][0]["role"] == "user"
            assert "城西旧书店" in review["recent_dialogue"][0]["content"]
            chat = calls[3]["messages"]
            assert "城西旧书店" in encode(chat)
            assert "青竹七号" in encode(chat)
            assert chat[-1]["content"] == "刚才这个项目在哪里碰头，长期确认的验收代号又是什么？"
            assert [m["role"] for m in chat] == [
                "system",
                "user",
                "user",
                "assistant",
                "user",
            ]
            stages = core.store.rows("SELECT job_id,scenario,state FROM model_usage ORDER BY rowid")
            assert [r["scenario"] for r in stages] == ["memory_review", "chat"] * 2
            assert all(r["state"] == "SETTLED" for r in stages)
            assert stages[0]["job_id"] == stages[1]["job_id"]
            assert stages[2]["job_id"] == stages[3]["job_id"]
            frozen = core.store.rows(
                "SELECT value FROM runtime_meta WHERE key LIKE ? OR key LIKE ?",
                (live_text.MEMORY + "%", live_text.CONTEXT + "%"),
            )
            assert "城西旧书店" not in encode(frozen) and "青竹七号" not in encode(frozen)

    run(scenario())


@pytest.mark.parametrize("delta,kept", [(-1, False), (0, True)])
def test_utf8_history_boundary_crops_a_whole_pair_before_review(core, tmp_path, delta, kept):
    texts = ["本次临时项目在城西旧书店碰头。", "这个项目的验收代号是什么？"]
    limit = len((texts[0] + "青竹七号。").encode("utf-8")) + delta

    async def scenario():
        async with readonly_window(
            core,
            tmp_path,
            queries=texts,
            options=combined_options(max_history_utf8_bytes=limit),
            core_options={"max_recent_messages": 4},
        ) as (_, _, capture, send, calls, _, _, _, _):
            await capture()
            assert await core.run_once()
            assert await send()
            await capture()
            assert await core.run_once()
            review = json.loads(calls[2]["messages"][-1]["content"])
            assert len(review.get("recent_dialogue", [])) == (2 if kept else 0)
            chat = calls[3]["messages"]
            assert sum(m["role"] == "assistant" for m in chat) == int(kept)
            assert ("城西旧书店" in encode(chat)) == kept
            assert chat[-1]["content"] == texts[-1]

    run(scenario())


def test_empty_scoped_search_combines_history_with_one_chat_per_input(core, tmp_path, monkeypatch):
    async def scenario():
        async with readonly_window(
            core,
            tmp_path,
            options=combined_options(),
            core_options={"max_recent_messages": 4},
        ) as (_, _, capture, send, calls, sends, _, _, _):
            # Only semantic scores are substituted; native scoped SQL and all receipts remain real.
            monkeypatch.setattr(
                core.memory.index, "scores", lambda q, rows: {r["memory_id"]: -1 for r in rows}
            )
            for _ in range(2):
                await capture()
                assert await core.run_once()
                assert await send()
            assert len(calls) == len(sends) == 2
            assert [m["role"] for m in calls[-1]["messages"]] == [
                "system",
                "user",
                "assistant",
                "user",
            ]
            assert [r["scenario"] for r in core.store.rows("SELECT scenario FROM model_usage")] == [
                "chat",
                "chat",
            ]

    run(scenario())


@pytest.mark.parametrize("erased", ["history", "corrected_fact_family"])
def test_latest_ledger_replay_of_old_combined_backup_cannot_revive_sources(core, tmp_path, erased):
    from companion.core import Core

    async def scenario():
        async with readonly_window(
            core,
            tmp_path,
            options=combined_options(),
            core_options={"max_recent_messages": 4},
        ) as (window, client, capture, send, calls, sends, _, memory_id, outsider):
            for _ in range(2):
                await capture()
                assert await core.run_once()
                assert await send()
            source = (
                core.store.one("SELECT origin_event_id FROM outbox ORDER BY rowid LIMIT 1")[
                    "origin_event_id"
                ]
                if erased == "history"
                else "evt_test_900"
            )
            key = Fernet.generate_key()
            archive, destination = tmp_path / "before.enc", tmp_path / "restored.db"
            backup(core.settings.database, archive, key)
            await window.stop_live(client)
            if erased == "history":
                with core.store.transaction():
                    core.ledger.expire_events(core.store, [source], core.clock())
            else:
                changed = core.memory.decide(
                    memory_id, "correct", core.clock(), "新的临时项目代号。"
                )
                core.memory.decide(changed["memory_id"], "forget", core.clock())
            restore(archive, destination, key, core.settings.erasure_ledger)
            with Core(
                core.settings.model_copy(update={"database": str(destination)}),
                core.model,
                core.clock,
            ) as recovered:
                assert (
                    recovered.store.one("SELECT redacted FROM events WHERE event_id=?", (source,))[
                        "redacted"
                    ]
                    == 1
                )
                assert all(
                    row["content_json"] == "{}"
                    for row in recovered.store.rows("SELECT content_json FROM outbox")
                )
                if erased == "corrected_fact_family":
                    assert recovered.store.one(
                        "SELECT content,status FROM memories WHERE memory_id=?", (memory_id,)
                    ) == {
                        "content": "",
                        "status": "FORGOTTEN",
                    }
                    assert not recovered.store.one(
                        "SELECT 1 FROM memory_vectors WHERE memory_id=?", (memory_id,)
                    )
                    assert not recovered.store.one(
                        "SELECT 1 FROM memory_revisions WHERE memory_id=?", (memory_id,)
                    )
                assert recovered.store.one(
                    "SELECT content,status FROM memories WHERE memory_id=?", (outsider,)
                ) == {
                    "content": "未批准的另一个项目代号是紫云三号。",
                    "status": "CONFIRMED",
                }
                assert recovered.store.one("SELECT COUNT(*) n FROM model_admissions")["n"] == 4
                assert all(
                    r["state"] == "SETTLED"
                    for r in recovered.store.rows("SELECT state FROM model_usage")
                )
                assert recovered.store.one(
                    "SELECT 1 FROM runtime_meta WHERE key='model_budget_restore_required'"
                )
                assert not recovered.status()["live_text_window"]["approved"]
                assert not await recovered.run_once()
            assert len(calls) == 4 and len(sends) == 2

    run(scenario())


def mutate(core, kind):
    event_id = core.store.one("SELECT origin_event_id FROM outbox ORDER BY rowid LIMIT 1")[
        "origin_event_id"
    ]
    if kind == "fact":
        core.store.execute("UPDATE memories SET content='漂移事实' WHERE fact_key IS NOT NULL")
    elif kind == "expired_evidence":
        core.store.execute(
            "UPDATE events SET ingested_at=? WHERE event_id='evt_test_900'",
            (after(core.clock(), -86400 * 3650),),
        )
    elif kind == "history":
        body = json.loads(
            core.store.one("SELECT body_json FROM events WHERE event_id=?", (event_id,))[
                "body_json"
            ]
        )
        body["text"] = "历史原件已漂移"
        core.store.execute(
            "UPDATE events SET body_json=? WHERE event_id=?", (encode(body), event_id)
        )
    elif kind == "PENDING":
        core.store.execute("UPDATE outbox SET state='PENDING' WHERE origin_event_id=?", (event_id,))
    elif kind == "reply":
        core.store.execute(
            "UPDATE outbox SET content_json=? WHERE origin_event_id=?",
            (encode({"text": "漂移回复"}), event_id),
        )
    elif kind == "epoch":
        advance_epoch(core.store)
    elif kind in {"history_approval", "memory_approval"}:
        core.settings.live_text_window = core.settings.live_text_window.model_copy(
            update={kind + "_sha256": "a" * 64},
        )
    else:
        row = core.store.one(
            "SELECT * FROM runtime_meta WHERE key=?", (live_text.INPUT + event_id,)
        )
        value = json.loads(row["value"])
        if kind == "signature":
            value["capture"]["signature"] = "0" * 128
        elif kind == "window":
            value["window_sha256"] = "a" * 64
        else:
            raise AssertionError(kind)
        core.store.execute(
            "UPDATE runtime_meta SET value=? WHERE key=?", (encode(value), row["key"])
        )


@pytest.mark.parametrize("stage", ["memory_review", "chat"])
@pytest.mark.parametrize(
    "kind",
    [
        "history",
        "fact",
        "expired_evidence",
        "signature",
        "PENDING",
        "window",
        "epoch",
        "history_approval",
        "memory_approval",
    ],
)
def test_both_source_scopes_recheck_in_final_http_hook_before_new_io(core, tmp_path, stage, kind):
    async def scenario():
        async with readonly_window(
            core,
            tmp_path,
            options=combined_options(),
            core_options={"max_recent_messages": 4},
        ) as (_, _, capture, send, calls, sends, _, _, _):
            await capture()
            assert await core.run_once()
            assert await send()
            await capture()

            async def hook(request):
                reviewing = (
                    '"operation":"recall"' in json.loads(request.content)["messages"][-1]["content"]
                )
                if reviewing == (stage == "memory_review"):
                    mutate(core, kind)

            core.model.client.event_hooks["request"].append(hook)
            assert await core.run_once()
            assert len(calls) == (2 if stage == "memory_review" else 3)
            assert len(sends) == 1
            assert core.store.one("SELECT COUNT(*) n FROM outbox")["n"] == 1
            assert (
                core.store.one("SELECT state FROM jobs ORDER BY rowid DESC LIMIT 1")["state"]
                == "FAILED"
            )
            assert not await core.run_once()

    run(scenario())


@pytest.mark.parametrize("stage", ["memory_review", "chat"])
def test_history_drift_after_received_response_keeps_cost_but_never_enqueues(core, tmp_path, stage):
    def respond(request, payload, number):
        reviewing = '"operation":"recall"' in payload["messages"][-1]["content"]
        if number > 2 and reviewing == (stage == "memory_review"):
            mutate(core, "history")
        return httpx.Response(
            200,
            json={
                "choices": [
                    {"message": {"content": '{"relevant":["m0"]}' if reviewing else "合成回复"}}
                ],
                "usage": {"prompt_tokens": 100, "completion_tokens": 20},
            },
        )

    async def scenario():
        async with readonly_window(
            core,
            tmp_path,
            options=combined_options(),
            core_options={"max_recent_messages": 4},
            respond=respond,
        ) as (_, _, capture, send, calls, sends, _, _, _):
            await capture()
            assert await core.run_once()
            assert await send()
            await capture()
            assert await core.run_once()
            assert len(calls) == (3 if stage == "memory_review" else 4) and len(sends) == 1
            assert core.store.one("SELECT COUNT(*) n FROM outbox")["n"] == 1
            usage = core.store.rows("SELECT state,billed_micro_usd FROM model_usage")
            assert all(r["state"] == "SETTLED" and r["billed_micro_usd"] > 0 for r in usage)

    run(scenario())


@pytest.mark.parametrize("kind", ["history", "fact", "epoch"])
def test_delivery_check_refuses_either_changed_scope_without_sending(core, tmp_path, kind):
    async def scenario():
        async with readonly_window(
            core,
            tmp_path,
            options=combined_options(),
            core_options={"max_recent_messages": 4},
        ) as (window, client, capture, send, calls, sends, _, _, _):
            await capture()
            assert await core.run_once()
            assert await send()
            await capture()
            assert await core.run_once()
            row = core.store.one("SELECT * FROM outbox ORDER BY rowid DESC LIMIT 1")
            lease = "lease_" + "f" * 32
            core.store.execute(
                "UPDATE outbox SET state='SENDING',lease_token=?,lease_until=? WHERE message_id=?",
                (lease, after(core.clock(), 60), row["message_id"]),
            )
            check = {**reply_approval(window.plan, core), "lease_token": lease}
            del check["evidence_sha256"]
            assert (await client.post("/internal/v1/live-text/send-check", json=check)).json()[
                "approved"
            ]
            mutate(core, kind)
            response = await client.post("/internal/v1/live-text/send-check", json=check)
            assert response.status_code >= 400
            assert len(calls) == 4 and len(sends) == 1
            assert (
                core.store.one("SELECT state FROM outbox WHERE message_id=?", (row["message_id"],))[
                    "state"
                ]
                == "SENDING"
            )

    run(scenario())


def test_conflicting_history_and_confirmed_fact_survive_soft_capacity_and_current_input(
    core, tmp_path
):
    texts = ["本次临时项目的验收代号改说白帆九号。", "那这个临时项目的代号还是原来的？"]

    async def scenario():
        async with readonly_window(
            core,
            tmp_path,
            queries=texts,
            options=combined_options(),
            core_options={"max_recent_messages": 4, "context_soft_tokens": 1},
        ) as (_, _, capture, send, calls, _, _, memory_id, _):
            await capture()
            assert await core.run_once()
            assert await send()
            await capture()
            assert await core.run_once()
            for payload in calls[2:]:
                assert "白帆九号" in encode(payload) and "青竹七号" in encode(payload)
            messages = calls[-1]["messages"]
            assert "不确定" in messages[0]["content"] and "投票" in messages[0]["content"]
            assert messages[-1] == {"role": "user", "content": texts[-1]}
            assert core.store.one(
                "SELECT status,content FROM memories WHERE memory_id=?", (memory_id,)
            ) == {
                "status": "CONFIRMED",
                "content": "本次临时项目的验收代号是青竹七号。",
            }

    run(scenario())


def test_unknown_review_preserves_budget_and_never_falls_back_to_chat(core, tmp_path):
    def unknown(request, payload, number):
        raise httpx.ReadTimeout("SYNTHETIC_B1_UNKNOWN", request=request)

    async def scenario():
        async with readonly_window(
            core,
            tmp_path,
            options=combined_options(),
            core_options={"max_recent_messages": 4},
            respond=unknown,
        ) as (_, _, capture, _, calls, sends, _, _, _):
            await capture()
            assert await core.run_once()
            assert len(calls) == 1 and sends == []
            assert core.store.one("SELECT scenario,state FROM model_usage") == {
                "scenario": "memory_review",
                "state": "UNKNOWN",
            }
            assert core.store.rows("SELECT * FROM outbox") == []
            assert not await core.run_once()

    run(scenario())


def test_five_total_attempt_cap_survives_combined_stages_and_fresh_core(core, tmp_path):
    from companion.core import Core

    async def scenario():
        async with readonly_window(
            core,
            tmp_path,
            options=combined_options(),
            core_options={"max_recent_messages": 4},
        ) as (_, _, capture, send, calls, sends, _, _, _):
            for _ in range(2):
                await capture()
                assert await core.run_once()
                assert await send()
            await capture()
            assert await core.run_once()
            assert len(calls) == 5 and len(sends) == 2
            assert core.store.one("SELECT COUNT(*) n FROM model_admissions")["n"] == 5
            restarted = Core(core.settings, core.model, core.clock)
            try:
                assert not await restarted.run_once()
                assert restarted.status()["live_text_window"]["approved"] is False
                assert restarted.store.one("SELECT COUNT(*) n FROM model_usage")["n"] == 5
                assert restarted.store.one("SELECT COUNT(*) n FROM outbox")["n"] == 2
            finally:
                restarted.store.close()
            assert len(calls) == 5

    run(scenario())


def test_combined_budget_too_small_rejects_before_model_http(core, tmp_path):
    async def scenario():
        async with readonly_window(
            core,
            tmp_path,
            options=combined_options(),
            core_options={"max_recent_messages": 4},
            paid={"max_input_tokens": 1},
        ) as (_, _, capture, _, calls, sends, _, _, _):
            await capture()
            assert await core.run_once()
            assert calls == sends == []
            assert not core.store.one("SELECT 1 FROM model_admissions")

    run(scenario())


@pytest.mark.parametrize(
    "change",
    [
        {"history_approval_sha256": None},
        {"memory_approval_sha256": None},
        {"history_scope": "none", "max_history_turns": 0, "max_history_utf8_bytes": 0},
    ],
)
def test_scopes_cannot_inherit_or_reassign_each_others_approval(core, tmp_path, change):
    async def scenario():
        async with readonly_window(
            core,
            tmp_path,
            options=combined_options(),
            core_options={"max_recent_messages": 4},
        ) as (window, _, _, _, calls, sends, _, _, _):
            value = core.settings.live_text_window.model_dump(mode="json")
            with pytest.raises(ValidationError):
                LiveTextWindowApproval.model_validate({**value, **change})
            plan = deepcopy(window.plan.value)
            plan["live_text"].update(change)
            with pytest.raises(ValueError, match="HISTORY_APPROVAL|MEMORY_SCOPE"):
                WindowPlan(plan, window.plan.config, {})
            assert calls == sends == []

    run(scenario())


def test_old_history_only_plan_without_new_field_remains_byte_exact_readable(core, tmp_path):
    async def scenario():
        async with continuity_window(core, tmp_path) as (window, _, calls, sends, _, _):
            value = deepcopy(window.plan.value)
            del value["live_text"]["history_approval_sha256"]
            inventory = {
                key: value[key]
                for key in (
                    "identity_sha256",
                    "protocol_epoch",
                    "config_sha256",
                    "config_version",
                    "image_ids",
                )
            }
            raw = encode(value)
            historical = WindowPlan(value, window.plan.config, inventory)
            assert historical.sha256 == digest(json.loads(raw))
            assert "history_approval_sha256" not in historical.value["live_text"]
            archived = stored_plan(raw, window.plan.config)
            assert archived.sha256 == historical.sha256 and encode(archived.value) == raw
            assert calls == sends == []

    run(scenario())


@pytest.mark.parametrize("history", [False, True])
def test_fsynced_forget_with_rolled_back_db_blocks_the_next_model_http(
    core,
    tmp_path,
    monkeypatch,
    history,
):
    async def scenario():
        async with readonly_window(
            core,
            tmp_path,
            options=combined_options() if history else {},
            core_options={"max_recent_messages": 4 if history else 0},
        ) as (_, _, capture, send, calls, sends, _, memory_id, _):
            await capture()
            assert await core.run_once()
            assert await send()
            await capture()

            async def forget_then_crash(_request):
                def crash_apply(*_):
                    raise RuntimeError("SYNTHETIC_AFTER_FSYNC_BEFORE_DB_COMMIT")

                with monkeypatch.context() as patch:
                    patch.setattr(core.ledger, "apply", crash_apply)
                    with pytest.raises(RuntimeError):
                        with core.store.transaction():
                            core.ledger.append(core.store, memory_id, core.clock())
                assert (
                    core.store.one("SELECT status FROM memories WHERE memory_id=?", (memory_id,))[
                        "status"
                    ]
                    == "CONFIRMED"
                )
                assert not core.store.one("SELECT 1 FROM privacy_erasure_ledger")

            core.model.client.event_hooks["request"].append(forget_then_crash)
            assert await core.run_once()
            assert len(calls) == 2 and len(sends) == 1
            assert core.store.one("SELECT COUNT(*) n FROM outbox")["n"] == 1

    run(scenario())


def test_foreign_window_is_excluded_before_reading_its_stage_metadata(core, tmp_path):
    async def scenario():
        async with readonly_window(
            core,
            tmp_path,
            options=combined_options(),
            core_options={"max_recent_messages": 4},
        ) as (_, _, capture, send, calls, _, _, _, _):
            await capture()
            assert await core.run_once()
            assert await send()
            await capture()
            mutate(core, "window")
            # A foreign scope's stage metadata is not current-window evidence.
            row = core.store.one(
                "SELECT * FROM runtime_meta WHERE key LIKE ?", (live_text.MEMORY + "%",)
            )
            value = json.loads(row["value"])
            del value["history_sources"]
            core.store.execute(
                "UPDATE runtime_meta SET value=? WHERE key=?", (encode(value), row["key"])
            )
            assert await core.run_once()
            assert len(calls) == 4
            assert "recent_dialogue" not in json.loads(calls[2]["messages"][-1]["content"])
            assert [m["role"] for m in calls[-1]["messages"]] == ["system", "user", "user"]

    run(scenario())
