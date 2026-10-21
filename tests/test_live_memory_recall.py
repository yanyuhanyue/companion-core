"""Readonly R5: native retrieval, ASGI Core, loopback capture, synthetic paid transport."""

import hashlib
import json
import sqlite3
from contextlib import asynccontextmanager
from types import SimpleNamespace

import httpx
import numpy as np
import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from pydantic import ValidationError
from websockets.asyncio.server import serve

from companion import budget, live_text
from companion.config import LiveTextWindowApproval
from companion.contracts import CoreError
from companion.model import ModelClient
from companion.privacy import advance_epoch, epoch
from companion.retrieval import INDEX_VERSION
from companion.store import after, encode
from companion_onebot_bridge import network
from companion_onebot_bridge.spool import Spool
from companion_onebot_bridge.window import TestWindow, WindowPlan
from tests.conftest import run
from tests.test_live_text_window import live_fixture
from tests.test_memory_commands import fact
from tests.test_onebot_capture import bootstrap_frames, capture_approval
from tests.test_onebot_delivery import core_client, provider
from tests.test_onebot_direct import raw_event
from tests.test_onebot_window import reply_approval


def scope_facts(core):
    memory_id = fact(core, 900, "本次临时项目的验收代号是青竹七号。")
    outsider = fact(core, 901, "未批准的另一个项目代号是紫云三号。")
    return (
        memory_id,
        outsider,
        [
            live_text.memory_fact(
                core.store,
                core.settings,
                core.clock,
                memory_id,
            )
        ],
    )


@asynccontextmanager
async def readonly_window(
    core,
    tmp_path,
    *,
    respond=None,
    options=None,
    paid=None,
    request_hook=None,
    core_options=None,
    query="本次临时项目的验收代号是什么？",
    queries=None,
    raw_messages=None,
):
    memory_id, outsider, pins = scope_facts(core)
    calls, sends, scores = [], [], []
    original_scores = core.memory.index.scores
    # An existing out-of-scope fact with missing evidence must remain untouched.
    core.store.execute("DELETE FROM memory_evidence WHERE memory_id=?", (outsider,))
    core.memory.observe_trait(
        "未批准私密特征",
        core.store.one("SELECT * FROM events WHERE event_id='evt_test_901'"),
        core.clock(),
    )
    core.store.execute("UPDATE traits SET confidence=1")

    def semantic(query, rows):
        scores.append([r["memory_id"] for r in rows])
        return original_scores(query, rows)

    core.memory.index.scores = semantic
    number = 0

    async def source(ws):
        nonlocal number
        number += 1
        for frame in bootstrap_frames():
            await ws.send(json.dumps(frame))
        event = raw_event(number, text=queries[number - 1] if queries else query)
        if raw_messages:
            event["message"] = raw_messages[number - 1]
        await ws.send(json.dumps(event))
        await ws.wait_closed()

    def model_response(request):
        payload = json.loads(request.content)
        calls.append(payload)
        if respond:
            return respond(request, payload, len(calls))
        last = payload["messages"][-1]["content"]
        if '"operation":"recall"' in last:
            content = json.dumps({"relevant": [m["ref"] for m in json.loads(last)["memories"]]})
        else:
            content = "青竹七号。"
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": content}}],
                "usage": {"prompt_tokens": 100, "completion_tokens": 20},
            },
        )

    async with serve(source, "127.0.0.1", 0) as server:
        cfg, plan = live_fixture(
            core,
            tmp_path,
            core_options={"memory_enabled": True, **(core_options or {})},
            bridge_options={
                "capture_signing_key": Ed25519PrivateKey.generate().private_bytes_raw().hex(),
                "onebot_ws_url": f"ws://127.0.0.1:{server.sockets[0].getsockname()[1]}",
            },
            paid_options={"per_request_calls": 2, **(paid or {})},
            memory_scope="approved_facts",
            memory_facts=pins,
            memory_approval_sha256="f" * 64,
            **(options or {}),
        )
        async with (
            core_client(core) as client,
            network.private_http_client(
                cfg.onebot_http_url,
                cfg.onebot_token,
                transport=httpx.MockTransport(lambda r: provider(r, sends)),
            ) as protocol,
            httpx.AsyncClient(
                transport=httpx.MockTransport(model_response),
                event_hooks={
                    "request": [request_hook] if request_hook else [],
                },
            ) as model,
        ):
            client.base_url = cfg.core_url
            core.model = ModelClient(core.settings, model)
            with Spool(cfg) as spool:
                window = TestWindow(spool, plan, clock=lambda: core.clock.now)
                await window.open(client)

                async def capture():
                    await window.capture_once(client, protocol)
                    await window.approve_captured(client, capture_approval(window))

                async def send():
                    return await window.send_once(client, protocol, reply_approval(plan, core))

                yield window, client, capture, send, calls, sends, scores, memory_id, outsider


def test_real_bge_only_sees_approved_rows_and_two_settled_stages_share_one_reply(core, tmp_path):
    async def scenario():
        async with readonly_window(core, tmp_path) as (
            window,
            _,
            capture,
            send,
            calls,
            sends,
            scores,
            memory_id,
            outsider,
        ):
            before = epoch(core.store)
            await capture()
            assert await core.run_once()
            assert len(calls) == 2
            assert all(ids == [memory_id] for ids in scores)
            assert "紫云三号" not in encode(calls)
            assert "未批准私密特征" not in encode(calls)
            assert core.store.one(
                "SELECT status,confidence FROM memories WHERE memory_id=?", (outsider,)
            ) == {"status": "CONFIRMED", "confidence": 1.0}
            assert core.store.one("SELECT 1 FROM memory_vectors WHERE memory_id=?", (memory_id,))
            assert not core.store.one("SELECT 1 FROM memory_vectors WHERE memory_id=?", (outsider,))
            assert [
                r["scenario"]
                for r in core.store.rows("SELECT scenario FROM model_usage ORDER BY rowid")
            ] == ["memory_review", "chat"]
            assert all(
                r["state"] == "SETTLED" for r in core.store.rows("SELECT state FROM model_usage")
            )
            assert core.store.one("SELECT COUNT(*) n FROM model_admissions")["n"] == 2
            assert (
                len(
                    json.loads(
                        core.store.one(
                            "SELECT value FROM runtime_meta WHERE key LIKE 'live_text_reply:%'"
                        )["value"]
                    )["request_ids"]
                )
                == 2
            )
            assert len(calls[-1]["messages"]) == 3
            assert "青竹七号" in calls[-1]["messages"][1]["content"]
            assert await send()
            assert len(sends) == 1
            assert window.status()["model_attempts"] == 2
            assert epoch(core.store) == before
            snapshots = encode(
                core.store.rows(
                    "SELECT value FROM runtime_meta WHERE key LIKE 'live_text_memory:%' "
                    "OR key LIKE 'live_text_context:%'"
                )
            )
            assert "青竹七号" not in snapshots

    run(scenario())


def test_bound_memory_apis_and_idle_extraction_cannot_bypass_scope(core, tmp_path):
    async def scenario():
        async with readonly_window(core, tmp_path) as (
            _,
            client,
            _,
            _,
            calls,
            _,
            _,
            memory_id,
            _,
        ):
            before = epoch(core.store)
            response = await client.post(
                "/internal/v1/memories/search",
                json={
                    "query": "*",
                    "requester_actor_id": "owner",
                },
            )
            assert response.status_code == 403
            response = await client.post(
                f"/internal/v1/memories/{memory_id}/decisions",
                json={
                    "actor_id": "owner",
                    "decision": "forget",
                },
            )
            assert response.status_code == 403
            assert not await core.extract_once()
            assert epoch(core.store) == before
            assert calls == []
            assert not core.store.one(
                "SELECT 1 FROM runtime_meta WHERE key='memory_extraction_lease'"
            )

    run(scenario())


@pytest.mark.parametrize(
    "change",
    [
        {"memory_approval_sha256": None},
        {"memory_scope": "none"},
        {
            "history_scope": "approved_window",
            "max_history_turns": 1,
            "max_history_utf8_bytes": 1000,
        },
    ],
)
def test_r5_scope_is_explicit_and_cannot_implicitly_include_recent_history(core, tmp_path, change):
    _, _, pins = scope_facts(core)
    cfg, plan = live_fixture(core, tmp_path)
    value = core.settings.live_text_window.model_dump()
    value.update(
        memory_scope="approved_facts",
        memory_facts=pins,
        memory_approval_sha256="f" * 64,
        capture_public_key="a" * 64,
    )
    value.update(change)
    with pytest.raises(ValidationError):
        LiveTextWindowApproval.model_validate(value)
    malformed = {
        **plan.value,
        "live_text": {
            **plan.value["live_text"],
            **change,
            "memory_scope": "approved_facts",
            "memory_facts": pins,
        },
    }
    with pytest.raises(ValueError):
        WindowPlan(malformed, cfg, {})


def test_unknown_review_never_dispatches_chat_or_enqueues_a_reply(core, tmp_path):
    def fail_review(request, payload, number):
        raise httpx.ReadTimeout("synthetic unknown", request=request)

    async def scenario():
        async with readonly_window(core, tmp_path, respond=fail_review) as (
            _,
            _,
            capture,
            _,
            calls,
            sends,
            _,
            _,
            _,
        ):
            await capture()
            assert await core.run_once()
            assert len(calls) == 1 and sends == []
            assert core.store.one("SELECT scenario,state FROM model_usage") == {
                "scenario": "memory_review",
                "state": "UNKNOWN",
            }
            assert not core.store.rows("SELECT * FROM outbox")
            assert not await core.run_once()

    run(scenario())


@pytest.mark.parametrize(
    "mutation", ["content", "evidence", "status", "needs_review", "epoch", "snapshot"]
)
def test_source_or_frozen_review_drift_after_receive_prevents_chat(core, tmp_path, mutation):
    def mutate(request, payload, number):
        if mutation == "content":
            core.store.execute("UPDATE memories SET content='漂移'")
        elif mutation == "evidence":
            core.store.execute("UPDATE events SET redacted=1 WHERE event_id='evt_test_900'")
        elif mutation == "status":
            core.store.execute("UPDATE memories SET status='HYPOTHESIS'")
        elif mutation == "needs_review":
            core.store.execute("UPDATE memories SET needs_review=1")
        elif mutation == "epoch":
            advance_epoch(core.store)
        else:
            core.store.execute("DELETE FROM runtime_meta WHERE key LIKE 'live_text_memory:%'")
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": '{"relevant":["m0"]}'}}],
                "usage": {"prompt_tokens": 100, "completion_tokens": 20},
            },
        )

    async def scenario():
        async with readonly_window(core, tmp_path, respond=mutate) as (
            _,
            _,
            capture,
            _,
            calls,
            sends,
            _,
            _,
            _,
        ):
            await capture()
            assert await core.run_once()
            assert len(calls) == 1 and sends == []
            assert core.store.one("SELECT state FROM model_usage")["state"] == "SETTLED"
            assert core.store.rows("SELECT * FROM outbox") == []

    run(scenario())


def test_two_stage_requests_keep_the_original_five_total_call_cap(core, tmp_path):
    async def scenario():
        async with readonly_window(core, tmp_path) as (
            _,
            _,
            capture,
            send,
            calls,
            sends,
            _,
            _,
            _,
        ):
            for _ in range(2):
                await capture()
                assert await core.run_once()
                await send()
            await capture()
            assert await core.run_once()
            assert len(calls) == 5 and len(sends) == 2
            assert core.store.one("SELECT COUNT(*) n FROM model_admissions")["n"] == 5
            assert core.store.one("SELECT COUNT(*) n FROM outbox")["n"] == 2
            assert core.status()["live_text_window"]["approved"] is False

    run(scenario())


def test_duplicate_review_and_chat_stages_are_rejected(core, tmp_path):
    async def scenario():
        async with readonly_window(core, tmp_path) as (
            _,
            _,
            capture,
            _,
            _,
            _,
            _,
            _,
            _,
        ):
            await capture()
            assert await core.run_once()
            job = core.store.one("SELECT job_id FROM jobs WHERE event_id NOT LIKE 'evt_test_%'")
            for stage in ("memory_review", "chat"):
                with pytest.raises(CoreError):
                    budget.reserve(
                        core.store,
                        core.settings,
                        job["job_id"],
                        stage,
                        [{"role": "user", "content": "duplicate"}],
                        core.clock(),
                        clock=core.clock,
                    )
            assert core.store.one("SELECT COUNT(*) n FROM model_usage")["n"] == 2

    run(scenario())


def test_no_candidates_dispatches_only_chat_without_a_review(core, tmp_path, monkeypatch):
    async def scenario():
        async with readonly_window(core, tmp_path) as (
            _,
            _,
            capture,
            send,
            calls,
            sends,
            _,
            _,
            _,
        ):
            # Substitute only relevance scores; the actual scoped SQL/FTS search still runs.
            monkeypatch.setattr(
                core.memory.index,
                "scores",
                lambda query, rows: {row["memory_id"]: -1 for row in rows},
            )
            await capture()
            assert await core.run_once()
            assert len(calls) == 1
            assert core.store.one("SELECT scenario,state FROM model_usage") == {
                "scenario": "chat",
                "state": "SETTLED",
            }
            assert len(calls[0]["messages"]) == 2
            await send()
            assert len(sends) == 1

    run(scenario())


@pytest.mark.parametrize("stage", ["memory_review", "chat"])
def test_source_drift_in_http_hook_is_rechecked_before_actual_model_io(core, tmp_path, stage):
    async def hook(request):
        reviewing = '"operation":"recall"' in json.loads(request.content)["messages"][-1]["content"]
        if reviewing == (stage == "memory_review"):
            core.store.execute(
                "UPDATE memories SET confidence=0.8 WHERE memory_id IN "
                "(SELECT memory_id FROM memory_evidence WHERE event_id='evt_test_900')"
            )

    async def scenario():
        async with readonly_window(core, tmp_path, request_hook=hook) as (
            _,
            _,
            capture,
            _,
            calls,
            sends,
            _,
            _,
            _,
        ):
            await capture()
            assert await core.run_once()
            assert len(calls) == (0 if stage == "memory_review" else 1)
            assert sends == [] and not core.store.rows("SELECT * FROM outbox")

    run(scenario())


@pytest.mark.parametrize(
    "mutation",
    [
        "source",
        "epoch",
        "memory_snapshot",
        "chat_snapshot",
        "review_payload",
        "chat_payload",
        "selected",
    ],
)
def test_final_core_send_check_rechecks_both_stages_and_sources(core, tmp_path, mutation):
    async def scenario():
        async with readonly_window(core, tmp_path) as (
            window,
            client,
            capture,
            _,
            calls,
            sends,
            _,
            memory_id,
            _,
        ):
            await capture()
            assert await core.run_once()
            row = core.store.one("SELECT * FROM outbox")
            lease = "lease_" + "f" * 32
            core.store.execute(
                "UPDATE outbox SET state='SENDING',lease_token=?,lease_until=?",
                (lease, after(core.clock(), 60)),
            )
            check = {**reply_approval(window.plan, core), "lease_token": lease}
            del check["evidence_sha256"]
            assert (await client.post("/internal/v1/live-text/send-check", json=check)).json()[
                "approved"
            ]
            if mutation == "source":
                core.store.execute(
                    "UPDATE memories SET content='变更事实' WHERE memory_id=?", (memory_id,)
                )
            elif mutation == "epoch":
                advance_epoch(core.store)
            elif mutation.endswith("snapshot"):
                prefix = live_text.MEMORY if mutation.startswith("memory") else live_text.CONTEXT
                core.store.execute("DELETE FROM runtime_meta WHERE key LIKE ?", (prefix + "%",))
            else:
                prefix = live_text.CONTEXT if mutation == "chat_payload" else live_text.MEMORY
                meta = core.store.one(
                    "SELECT * FROM runtime_meta WHERE key LIKE ?", (prefix + "%",)
                )
                value = json.loads(meta["value"])
                key = {
                    "review_payload": "review_payload_sha256",
                    "chat_payload": "payload_sha256",
                }.get(mutation, "selected")
                value[key] = "a" * 64 if key.endswith("sha256") else []
                core.store.execute(
                    "UPDATE runtime_meta SET value=? WHERE key=?", (encode(value), meta["key"])
                )
            response = await client.post("/internal/v1/live-text/send-check", json=check)
            assert response.status_code >= 400
            assert len(calls) == 2 and sends == []
            assert (
                core.store.one("SELECT state FROM outbox WHERE message_id=?", (row["message_id"],))[
                    "state"
                ]
                == "SENDING"
            )

    run(scenario())


def test_only_actual_chat_references_survive_the_soft_bound(core, tmp_path, monkeypatch):
    async def scenario():
        async with readonly_window(
            core,
            tmp_path,
            query="项目代号" + "是" * 450,
            core_options={"context_soft_tokens": 1000},
        ) as (
            _,
            _,
            capture,
            send,
            calls,
            _,
            _,
            _,
            _,
        ):
            monkeypatch.setattr(
                core.memory.index,
                "scores",
                lambda query, rows: {row["memory_id"]: 1.0 for row in rows},
            )
            await capture()
            assert await core.run_once()
            assert len(calls) == 2
            assert len(calls[-1]["messages"]) == 2
            context = json.loads(
                core.store.one(
                    "SELECT value FROM runtime_meta WHERE key LIKE ?", (live_text.CONTEXT + "%",)
                )["value"]
            )
            assert context["memory_sources"] == []
            await send()

    run(scenario())


@pytest.mark.parametrize("change", ["expired", "unconfirmed", "conflict", "approval_drift"])
def test_source_eligibility_cannot_be_replaced_with_an_approval_hash(core, tmp_path, change):
    async def scenario():
        async with readonly_window(core, tmp_path) as (
            _,
            _,
            capture,
            _,
            calls,
            sends,
            _,
            memory_id,
            _,
        ):
            if change == "expired":
                core.store.execute(
                    "UPDATE events SET ingested_at=? WHERE event_id='evt_test_900'",
                    (after(core.clock(), -86400 * 10000),),
                )
            elif change == "unconfirmed":
                core.store.execute(
                    "UPDATE memories SET status='HYPOTHESIS' WHERE memory_id=?", (memory_id,)
                )
            elif change == "conflict":
                core.store.execute(
                    "UPDATE memories SET needs_review=1 WHERE memory_id=?", (memory_id,)
                )
            else:
                core.settings.live_text_window = core.settings.live_text_window.model_copy(
                    update={"memory_approval_sha256": "1" * 64},
                )
            with pytest.raises((ValueError, httpx.HTTPStatusError)):
                await capture()
            assert calls == sends == []

    run(scenario())


@pytest.mark.parametrize(
    "result", ['{"relevant":["unapproved"]}', '{"relevant":["m0","m0"]}', "invalid"]
)
def test_invalid_settled_review_never_falls_back_to_chat(core, tmp_path, result):
    def respond(request, payload, number):
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": result}}],
                "usage": {"prompt_tokens": 100, "completion_tokens": 20},
            },
        )

    async def scenario():
        async with readonly_window(core, tmp_path, respond=respond) as (
            _,
            _,
            capture,
            _,
            calls,
            sends,
            _,
            _,
            _,
        ):
            await capture()
            assert await core.run_once()
            assert len(calls) == 1 and sends == []
            assert core.store.one("SELECT scenario,state FROM model_usage") == {
                "scenario": "memory_review",
                "state": "SETTLED",
            }
            assert core.store.rows("SELECT * FROM outbox") == []

    run(scenario())


def test_candidate_vector_cache_does_not_consume_one_sql_variable_per_memory(core, monkeypatch):
    ids = [fact(core, number, f"第{number}个合成项目代号是青竹。") for number in range(4)]
    rows = core.store.rows("SELECT * FROM memories ORDER BY rowid")
    for row in rows:
        core.store.insert(
            "memory_vectors",
            memory_id=row["memory_id"],
            model_version=INDEX_VERSION,
            content_hash=hashlib.sha256(row["content"].encode()).hexdigest(),
            vector=np.asarray([1.0], dtype="<f4").tobytes(),
        )
    monkeypatch.setattr(
        core.memory.index,
        "warmup",
        lambda: SimpleNamespace(
            query_embed=lambda query: iter([np.asarray([1.0], dtype="<f4")]),
        ),
    )
    original = core.store.db.setlimit(sqlite3.SQLITE_LIMIT_VARIABLE_NUMBER, 2)
    try:
        assert core.memory.index.scores("合成查询", rows[:3]) == {key: 1.0 for key in ids[:3]}
    finally:
        core.store.db.setlimit(sqlite3.SQLITE_LIMIT_VARIABLE_NUMBER, original)
