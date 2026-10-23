"""Memory integration through authenticated HTTP and independently opened Core workers."""

import asyncio
import json
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from fastapi.testclient import TestClient

from companion import api, live_text
from companion.core import Core
from companion.model import EXTRACT_RULES, RECALL_RULES, ModelClient
from companion.store import after, encode
from tests import test_input_protocol as protocol_fixtures
from tests.conftest import event, ingest, run
from tests.test_input_protocol import environment, notice
from tests.test_live_memory_recall import readonly_window
from tests.test_m1 import remember
from tests.test_next_life_growth import feedback_days
from tests.test_paid_model_gate import approved_live
from tests.test_private_companion import companion_options
from tests.test_reply_revision_api import admit

FACT = "青禾项目下一步检查配色"


@pytest.fixture(autouse=True)
def window_capacity_for_provenance_cases(monkeypatch):
    def options(core):
        value = companion_options(core)
        value["max_inputs"] = 20
        value["window_options"] = {**value["window_options"], "max_new_inputs": 20}
        return value
    monkeypatch.setattr(protocol_fixtures, "companion_options", options)


def response(text):
    return httpx.Response(200, json={"choices": [{"message": {"content": text}}],
                                   "usage": {"prompt_tokens": 100, "completion_tokens": 20}})


def test_authorized_history_can_exceed_individual_memory_search_query_limit(
    core, tmp_path, monkeypatch,
):
    async def scenario():
        prompts = []

        def provider(request):
            prompts.append(json.loads(request.content)["messages"])
            return response("合成回复")

        async with environment(core, tmp_path, monkeypatch) as (client, config, plan, _):
            async with httpx.AsyncClient(transport=httpx.MockTransport(provider)) as transport:
                with Core(core.settings, ModelClient(core.settings, transport)) as worker:
                    texts = [f"设计记录第{number}段：" + "线条" * 190 for number in range(1, 4)]
                    assert sum(map(len, texts)) > 1000
                    for number, text in enumerate(texts, 1):
                        _, accepted = await admit(client, config, plan, number, text)
                    # Earlier unanswered inputs remain authorized context after supersession.
                    assert await worker.run_once("long-history-chat")
                    assert worker.store.one("SELECT state FROM jobs WHERE job_id=?",
                                            (accepted["job_id"],))["state"] == "SUCCEEDED"
                    assert len(prompts) == 1
                    assert all(text in encode(prompts[-1]) for text in texts)
                    assert not live_text.stopped(core.store, plan.sha256)
    run(scenario())


@pytest.mark.parametrize("stage", ["review", "chat"])
def test_every_fact_source_is_tracked_before_await_and_recall_keeps_independent_fact(
    core, tmp_path, monkeypatch, stage,
):
    async def scenario():
        entered, release = asyncio.Event(), asyncio.Event()
        calls = []

        async def provider(request):
            messages = json.loads(request.content)["messages"]
            kind = "review" if messages[0]["content"] == RECALL_RULES else "chat"
            calls.append(kind)
            if kind == stage:
                entered.set()
                await release.wait()
            return response('{"relevant":["m0"]}' if kind == "review" else "合成回答")

        async with environment(core, tmp_path, monkeypatch) as (client, config, plan, _):
            async with httpx.AsyncClient(transport=httpx.MockTransport(provider)) as transport:
                with Core(core.settings, ModelClient(core.settings, transport)) as worker:
                    assert worker.store is not core.store
                    sources = []
                    for number in range(1, 10):
                        source, _ = await admit(client, config, plan, number, "/记住这个 " + FACT)
                        assert await worker.run_once("seed-control")
                        sources.append(source["event_id"])
                    assert calls == []
                    projected = worker.memory_context.recall("*", worker.clock())["facts"][0]
                    assert len(projected["evidence"]) == 8 and projected["evidence_truncated"]
                    assert set(projected["evidence_ids"]) == set(sources)
                    assert sources[0] not in {row["event_id"] for row in projected["evidence"]}
                    question, accepted = await admit(
                        client, config, plan, 10, "青禾项目下一步做什么？",
                    )
                    task = asyncio.create_task(worker.run_once("actual-memory-reply"))
                    try:
                        await asyncio.wait_for(entered.wait(), 10)
                        tracked = {row["event_id"] for row in core.store.rows(
                            "SELECT event_id FROM job_sources WHERE job_id=?",
                            (accepted["job_id"],),
                        )}
                        assert set(sources) | {question["event_id"]} <= tracked
                        recalled = await client.post("/internal/v1/live-text/inputs/recall", json=
                            notice(config, plan, datetime.now(UTC), number=1))
                        assert recalled.status_code == 200, recalled.text
                        release.set()
                        assert await asyncio.wait_for(task, 10)
                    finally:
                        release.set()
                        await asyncio.gather(task, return_exceptions=True)
                    assert core.store.one("SELECT state FROM jobs WHERE job_id=?",
                                          (accepted["job_id"],))["state"] == "CANCELLED"
                    assert not core.store.rows("SELECT * FROM outbox WHERE origin_event_id=?",
                                               (question["event_id"],))
                    assert core.store.one("SELECT redacted FROM events WHERE event_id=?",
                                          (question["event_id"],))["redacted"] == 0
                    assert not core.store.rows("SELECT * FROM event_dependencies WHERE event_id=?",
                                               (question["event_id"],))
                    assert core.store.one("SELECT status FROM memories")["status"] == "CONFIRMED"
                    assert core.store.one("SELECT COUNT(*) n FROM memory_evidence")["n"] == 8
                    assert core.store.rows("SELECT state FROM model_usage") == [
                        {"state": "SETTLED"} for _ in calls
                    ]
                    assert calls == (["review"] if stage == "review" else ["review", "chat"])
                    assert not live_text.stopped(core.store, plan.sha256)
    run(scenario())


def test_continuation_uses_full_episode_proof_including_undisplayed_sources(
    core, tmp_path, monkeypatch,
):
    async def scenario():
        entered, release = asyncio.Event(), asyncio.Event()
        hold = False
        captured = []

        async def provider(request):
            messages = json.loads(request.content)["messages"]
            if messages[0]["content"] == EXTRACT_RULES:
                return response('{"memories":[]}')
            if hold:
                captured.append(messages)
                entered.set()
                await release.wait()
            return response("合成回复")

        async with environment(core, tmp_path, monkeypatch) as (client, config, plan, _):
            async with httpx.AsyncClient(transport=httpx.MockTransport(provider)) as transport:
                with Core(core.settings, ModelClient(core.settings, transport)) as worker:
                    sources = []
                    for number in range(1, 6):
                        source, _ = await admit(
                            client, config, plan, number, f"设计进度第{number}步",
                        )
                        assert await worker.run_once("historical-chat")
                        sources.append(source["event_id"])
                    assert await worker.extract_once()
                    assert core.store.one("SELECT COUNT(*) n FROM episodes")["n"] == 1
                    question, accepted = await admit(client, config, plan, 6, "继续上次的设计")
                    hold = True
                    task = asyncio.create_task(worker.run_once("continuation-chat"))
                    try:
                        await asyncio.wait_for(entered.wait(), 10)
                        message = next(m for m in captured[0]
                                       if m["content"].startswith("近期情景接续"))
                        items = json.loads(message["content"].split("\n", 1)[1])
                        assert set(items[0]["source_event_ids"]) == set(sources)
                        assert sources[0] not in {row["event_id"] for row in items[0]["excerpts"]}
                        tracked = {row["event_id"] for row in core.store.rows(
                            "SELECT event_id FROM job_sources WHERE job_id=?",
                            (accepted["job_id"],),
                        )}
                        assert set(sources) <= tracked
                        recalled = await client.post("/internal/v1/live-text/inputs/recall", json=
                            notice(config, plan, datetime.now(UTC), number=1))
                        assert recalled.status_code == 200, recalled.text
                        release.set()
                        assert await asyncio.wait_for(task, 10)
                    finally:
                        release.set()
                        await asyncio.gather(task, return_exceptions=True)
                    assert not core.store.rows("SELECT * FROM outbox WHERE origin_event_id=?",
                                               (question["event_id"],))
                    assert not live_text.stopped(core.store, plan.sha256)
    run(scenario())


def test_expired_fact_is_absent_from_real_http_search_and_independent_worker(core, tmp_path):
    core.clock.now = datetime.now(UTC)
    mid = remember(core, 1, FACT, confirmed=True)
    core.store.execute("UPDATE jobs SET state='SUCCEEDED'")
    core.store.execute("UPDATE events SET memory_processed=1")
    core.store.execute("UPDATE memories SET valid_to=? WHERE memory_id=?",
                       (after(core.clock(), -1), mid))
    approved_live(core, tmp_path)
    calls = []

    def provider(request):
        calls.append(json.loads(request.content)["messages"])
        assert FACT not in encode(calls[-1])
        return response("当前没有可用记录")

    with TestClient(api.create_app(core.settings)) as client:
        headers = {"Authorization": "Bearer " + core.settings.bridge_token.get_secret_value()}
        result = client.post("/internal/v1/memories/search", headers=headers,
                             json={"query": "*", "requester_actor_id": "owner"})
        assert result.status_code == 200 and result.json() == {"items": []}
        current = event(core, 2, "这个项目还有记录吗？").model_dump(mode="json")
        result = client.post("/internal/v1/inbox/events", json=current,
                             headers={**headers, "X-Idempotency-Key": "expiry-question"})
        assert result.status_code == 202

        async def work():
            async with httpx.AsyncClient(transport=httpx.MockTransport(provider)) as transport:
                with Core(core.settings, ModelClient(core.settings, transport)) as worker:
                    assert worker.store is not core.store
                    assert await worker.run_once()
        run(work())
    assert len(calls) == 1


def test_fact_validity_is_rechecked_after_review_await_without_refunding_usage(core, tmp_path):
    core.clock.now = datetime.now(UTC)
    mid = remember(core, 1, FACT, confirmed=True)
    core.store.execute("UPDATE jobs SET state='SUCCEEDED'")
    core.store.execute("UPDATE events SET memory_processed=1")
    core.store.execute("UPDATE memories SET valid_to=? WHERE memory_id=?",
                       (after(core.clock(), 1), mid))
    approved_live(core, tmp_path)
    accepted = ingest(core, 2, "青禾项目下一步做什么？")
    calls = []

    def provider(request):
        calls.append(json.loads(request.content)["messages"])
        assert calls[-1][0]["content"] == RECALL_RULES
        core.clock.advance(2)
        return response('{"relevant":["m0"]}')

    async def work():
        async with httpx.AsyncClient(transport=httpx.MockTransport(provider)) as transport:
            with Core(core.settings, ModelClient(core.settings, transport), core.clock) as worker:
                assert await worker.run_once()
    run(work())
    assert len(calls) == 1
    assert core.store.one("SELECT state FROM jobs WHERE job_id=?", (accepted["job_id"],)) == {
        "state": "CANCELLED",
    }
    assert core.store.rows("SELECT state FROM model_usage") == [{"state": "SETTLED"}]
    assert not core.store.rows("SELECT * FROM outbox")


@pytest.mark.parametrize("mode", ["enabled", "disabled", "transient"])
def test_expression_hook_uses_actual_owner_sources_and_short_feedback_wins(
    core, tmp_path, monkeypatch, mode,
):
    core.settings.expression_learning_enabled = mode != "disabled"
    core.settings.affect_enabled = True
    core.clock.now = datetime.now(UTC) - timedelta(days=7)
    feedback_days(core)
    core.store.execute("UPDATE jobs SET state='SUCCEEDED'")
    historical = {row["event_id"] for row in core.store.rows("SELECT event_id FROM events")}
    calls = []

    def provider(request):
        calls.append(json.loads(request.content)["messages"])
        return response("合成回复")

    async def scenario():
        async with environment(core, tmp_path, monkeypatch) as (client, config, plan, _):
            source, accepted = await admit(client, config, plan, 100,
                                           "简短一点" if mode == "transient" else "今天聊点什么")
            async with httpx.AsyncClient(transport=httpx.MockTransport(provider)) as transport:
                with Core(core.settings, ModelClient(core.settings, transport)) as worker:
                    assert await worker.run_once()
            prompt = encode(calls[-1])
            assert ("主人长期表达偏好参考" in prompt) is (mode == "enabled")
            if mode == "transient":
                assert "当前交互参考" in prompt
            tracked = {row["event_id"] for row in core.store.rows(
                "SELECT event_id FROM job_sources WHERE job_id=?", (accepted["job_id"],),
            )}
            assert source["event_id"] in tracked
            assert (historical <= tracked) is (mode == "enabled")
            assert not live_text.stopped(core.store, plan.sha256)
    run(scenario())


def test_history_is_explicit_and_current_recall_does_not_reintroduce_revision_text(
    core, tmp_path, monkeypatch,
):
    old, new = "项目配色为青色", "项目配色为紫色"
    calls = []

    def provider(request):
        messages = json.loads(request.content)["messages"]
        calls.append(messages)
        assert old not in encode(messages)
        return response('{"relevant":["m0"]}' if messages[0]["content"] == RECALL_RULES
                        else "现在是紫色")

    async def scenario():
        async with environment(core, tmp_path, monkeypatch) as (client, config, plan, _):
            async with httpx.AsyncClient(transport=httpx.MockTransport(provider)) as transport:
                with Core(core.settings, ModelClient(core.settings, transport)) as worker:
                    await admit(client, config, plan, 1, "/记住这个 " + old)
                    assert await worker.run_once()
                    mid = core.store.one("SELECT memory_id FROM memories")["memory_id"]
                    await admit(client, config, plan, 2, f"/纠正这一条 {mid} {new}")
                    assert await worker.run_once()
                    revised = core.store.one("SELECT memory_id FROM memories WHERE status="
                                             "'CONFIRMED'")["memory_id"]
                    source, _ = await admit(client, config, plan, 3,
                                            f"/你记得什么 历史 {revised}")
                    assert await worker.run_once()
                    reply = core.store.one(
                        "SELECT content_json FROM outbox WHERE origin_event_id=?",
                        (source["event_id"],),
                    )["content_json"]
                    assert old in reply and new in reply and "SUPERSEDED" in reply
                    assert not calls
                    await admit(client, config, plan, 4, "项目配色是什么？")
                    assert await worker.run_once()
                    assert len(calls) == 2
    run(scenario())


def test_strict_fact_scope_does_not_authorize_episode_context(core, tmp_path):
    core.settings.memory_enabled = True
    canary = "没有授予当前窗口的情景原文"
    source = ingest(core, 80, canary, origin="onebot-direct:FriendMessage:10001")
    core.store.execute("UPDATE jobs SET state='SUCCEEDED' WHERE job_id=?", (source["job_id"],))
    core.memory.episodes(core.store.rows("SELECT * FROM events WHERE event_id='evt_test_80'"),
                         core.clock())

    async def scenario():
        async with readonly_window(core, tmp_path) as (
            _, _, capture, _, calls, _, _, _, _,
        ):
            assert core.store.one("SELECT COUNT(*) n FROM episodes")["n"] == 1
            await capture()
            assert await core.run_once()
            assert len(calls) == 2
            assert "近期情景接续" not in encode(calls)
            assert canary not in encode(calls)
    run(scenario())


def test_dropped_expression_reference_does_not_register_its_sources(core):
    core.settings.expression_learning_enabled = True
    core.settings.expression_enabled = True
    feedback_days(core)
    core.store.execute("UPDATE jobs SET state='SUCCEEDED'")
    historical = {row["event_id"] for row in core.store.rows("SELECT event_id FROM events")}
    ingest(core, 100, "今天聊点什么")
    job = core.claim_job("context-fit")
    core.settings.context_soft_tokens = 1
    snapshots = []
    messages = core.context(job, snapshots=snapshots)
    assert "主人长期表达偏好参考" not in encode(messages)
    assert not snapshots
    assert not historical & {row["event_id"] for row in core.store.rows(
        "SELECT event_id FROM job_sources WHERE job_id=?", (job["job_id"],),
    )}
