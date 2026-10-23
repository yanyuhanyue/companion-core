"""Synthetic recall outputs over authenticated HTTP and independently opened Core workers.

Production evidence establishes recall_invalid and settled usage, not the original
response shape. Every invalid response below is an explicitly synthetic test case.
"""

import json
from datetime import UTC, datetime

import httpx
import pytest

from companion import budget, live_text
from companion.core import Core
from companion.model import RECALL_RULES, REVIEW_RULES, ModelClient
from companion.store import encode
from tests.conftest import run
from tests.test_input_protocol import environment, notice
from tests.test_reply_revision_api import admit

FACT = "我用🤔表示在考虑青禾项目的蓝色配色"


def response(text, *, review=False, usage=True):
    body = {"choices": [{"message": {"content": text}}]}
    if usage:
        body["usage"] = {"prompt_tokens": 2540 if review else 100,
                         "completion_tokens": 58 if review else 20}
    return httpx.Response(200, json=body)


@pytest.mark.parametrize(("output", "reason"), [
    ("synthetic non-JSON output", "schema"),
    ('{}', "schema"),
    ('{"relevant":null}', "schema"),
    ('{"relevant":"m0"}', "schema"),
    ('{"relevant":[{}]}', "schema"),
    ('{"relevant":[],"reason":"synthetic extra field"}', "schema"),
    ('```json\n{"relevant":[]}\n```', "schema"),
    ('{"relevant":["m0","m0"]}', "duplicate_reference"),
    ('{"relevant":["m999"]}', "unknown_reference"),
])
def test_paid_invalid_recall_continues_owner_emoji_chat_without_candidates_or_retry(
    core, tmp_path, monkeypatch, output, reason,
):
    async def scenario():
        calls = []

        def provider(request):
            messages = json.loads(request.content)["messages"]
            review = messages[0]["content"] == RECALL_RULES
            calls.append(("review" if review else "chat", messages))
            return response(output if review else "在想什么呢？", review=review)

        async with environment(core, tmp_path, monkeypatch) as (client, config, plan, _):
            async with httpx.AsyncClient(transport=httpx.MockTransport(provider)) as transport:
                with Core(core.settings, ModelClient(core.settings, transport)) as worker:
                    assert worker.store is not core.store
                    await admit(client, config, plan, 1, "/记住这个 " + FACT)
                    assert await worker.run_once("seed-owner-fact")
                    assert calls == []
                    before = core.store.rows("SELECT * FROM memories")
                    original, accepted = await admit(client, config, plan, 2, "🤔")
                    assert await worker.run_once("actual-emoji-chat")
                    stored = core.store.one("SELECT body_json FROM events WHERE event_id=?",
                                            (original["event_id"],))
                    assert json.loads(stored["body_json"])["text"] == "\U0001f914"
                    assert core.store.one("SELECT state FROM jobs WHERE job_id=?",
                                          (accepted["job_id"],)) == {"state": "SUCCEEDED"}
                    assert [kind for kind, _ in calls] == ["review", "chat"]
                    assert FACT not in encode(calls[-1][1])
                    assert "核对暂不可用，不能断言没有历史记录" in encode(calls[-1][1])
                    assert core.store.rows("SELECT * FROM memories") == before
                    review = core.store.one(
                        "SELECT * FROM model_usage WHERE scenario='memory_review'",
                    )
                    assert (review["state"], review["input_tokens"], review["output_tokens"]) == (
                        "SETTLED", 2540, 58,
                    )
                    assert review["billed_micro_usd"] == budget.cost(
                        core.settings, 2540, 58, scenario="memory_review",
                    ) > 0
                    invalid = core.store.one(
                        "SELECT safe_detail_json FROM audit_log "
                        "WHERE action='memory.recall_invalid'",
                    )
                    assert json.loads(invalid["safe_detail_json"]) == {
                        "reason": reason, "request_id": review["request_id"],
                    }
                    paused = core.store.one(
                        "SELECT safe_detail_json FROM audit_log "
                        "WHERE action='memory.recall_paused'",
                    )
                    assert json.loads(paused["safe_detail_json"])["code"] == "MEMORY_REVIEW_INVALID"
                    assert not live_text.stopped(core.store, plan.sha256)
                    assert core.store.one("SELECT 1 FROM outbox WHERE origin_event_id=?",
                                          (original["event_id"],))
                    assert not await worker.run_once("no-automatic-review-retry")
                with Core(core.settings, ModelClient(core.settings, transport)) as restarted:
                    assert not await restarted.run_once("restart-does-not-repeat-paid-review")
                    assert restarted.store.one(
                        "SELECT * FROM model_usage WHERE scenario='memory_review'",
                    ) == review
                assert [kind for kind, _ in calls] == ["review", "chat"]
    run(scenario())


@pytest.mark.parametrize("selection", [None, [], ["m0"]])
def test_emoji_with_no_memory_or_valid_selection_keeps_native_success_path(
    core, tmp_path, monkeypatch, selection,
):
    async def scenario():
        calls = []

        def provider(request):
            messages = json.loads(request.content)["messages"]
            review = messages[0]["content"] == RECALL_RULES
            calls.append(("review" if review else "chat", messages))
            return response(encode({"relevant": selection}) if review else "在想什么呢？",
                            review=review)

        async with environment(core, tmp_path, monkeypatch) as (client, config, plan, _):
            async with httpx.AsyncClient(transport=httpx.MockTransport(provider)) as transport:
                with Core(core.settings, ModelClient(core.settings, transport)) as worker:
                    if selection is not None:
                        await admit(client, config, plan, 1, "/记住这个 " + FACT)
                        assert await worker.run_once("seed-owner-fact")
                    source, accepted = await admit(client, config, plan, 2, "🤔")
                    assert await worker.run_once("emoji-with-available-context")
                    assert core.store.one("SELECT state FROM jobs WHERE job_id=?",
                                          (accepted["job_id"],))["state"] == "SUCCEEDED"
                    assert [kind for kind, _ in calls] == (
                        ["chat"] if selection is None else ["review", "chat"]
                    )
                    assert (FACT in encode(calls[-1][1])) is bool(selection)
                    assert "核对暂不可用" not in encode(calls[-1][1])
                    assert calls[-1][1][-1]["content"] == "🤔"
                    assert not core.store.rows(
                        "SELECT 1 FROM audit_log WHERE action IN "
                        "('memory.recall_invalid','memory.recall_paused')",
                    )
                    assert core.store.one("SELECT 1 FROM outbox WHERE origin_event_id=?",
                                          (source["event_id"],))
                    assert not live_text.stopped(core.store, plan.sha256)
    run(scenario())


def test_invalid_recall_with_unknown_usage_does_not_start_chat_or_refund(
    core, tmp_path, monkeypatch,
):
    async def scenario():
        calls = []

        def provider(request):
            messages = json.loads(request.content)["messages"]
            calls.append(messages)
            assert messages[0]["content"] == RECALL_RULES
            return response("synthetic non-JSON output", review=True, usage=False)

        async with environment(core, tmp_path, monkeypatch) as (client, config, plan, _):
            async with httpx.AsyncClient(transport=httpx.MockTransport(provider)) as transport:
                with Core(core.settings, ModelClient(core.settings, transport)) as worker:
                    await admit(client, config, plan, 1, "/记住这个 " + FACT)
                    assert await worker.run_once("seed-owner-fact")
                    source, accepted = await admit(client, config, plan, 2, "🤔")
                    assert await worker.run_once("unknown-review-accounting")
                    assert core.store.one("SELECT state,last_error_code FROM jobs WHERE job_id=?",
                                          (accepted["job_id"],)) == {
                        "state": "FAILED", "last_error_code": "MEMORY_REVIEW_INVALID",
                    }
                    assert len(calls) == 1
                    usage = core.store.one(
                        "SELECT u.state,u.billed_micro_usd,a.reserved_micro_usd FROM model_usage u "
                        "JOIN model_admissions a USING(request_id)",
                    )
                    assert usage["state"] == "UNKNOWN"
                    assert usage["billed_micro_usd"] == usage["reserved_micro_usd"] > 0
                    assert not core.store.rows(
                        "SELECT 1 FROM audit_log WHERE action='memory.recall_paused'",
                    )
                    assert not core.store.rows("SELECT 1 FROM outbox WHERE origin_event_id=?",
                                               (source["event_id"],))
                    assert live_text.stopped(core.store, plan.sha256)
    run(scenario())


@pytest.mark.parametrize("interruption", ["recall", "supersede"])
def test_invalid_review_cannot_override_recall_or_supersession(
    core, tmp_path, monkeypatch, interruption,
):
    async def scenario():
        calls = []

        async def provider(request):
            messages = json.loads(request.content)["messages"]
            calls.append(messages)
            assert messages[0]["content"] == RECALL_RULES
            if interruption == "recall":
                recalled = await client.post("/internal/v1/live-text/inputs/recall", json=
                    notice(config, plan, datetime.now(UTC), number=1))
                assert recalled.status_code == 200, recalled.text
            else:
                await admit(client, config, plan, 3, "现在换个话题")
            return response("synthetic non-JSON output", review=True)

        async with environment(core, tmp_path, monkeypatch) as (client, config, plan, _):
            async with httpx.AsyncClient(transport=httpx.MockTransport(provider)) as transport:
                with Core(core.settings, ModelClient(core.settings, transport)) as worker:
                    await admit(client, config, plan, 1, "/记住这个 " + FACT)
                    assert await worker.run_once("seed-owner-fact")
                    source, accepted = await admit(client, config, plan, 2, "🤔")
                    assert await worker.run_once("interrupted-invalid-review")
                    assert core.store.one("SELECT state FROM jobs WHERE job_id=?",
                                          (accepted["job_id"],))["state"] == "CANCELLED"
                    assert len(calls) == 1
                    usage = core.store.one("SELECT state,billed_micro_usd FROM model_usage")
                    assert usage["state"] == "SETTLED" and usage["billed_micro_usd"] > 0
                    assert not core.store.rows("SELECT 1 FROM outbox WHERE origin_event_id=?",
                                               (source["event_id"],))
                    assert not core.store.rows(
                        "SELECT 1 FROM audit_log WHERE action='memory.recall_paused'",
                    )
                    assert not live_text.stopped(core.store, plan.sha256)
    run(scenario())


def test_invalid_memory_write_review_still_rejects_new_fact(core, tmp_path, monkeypatch):
    async def scenario():
        calls = []

        def provider(request):
            messages = json.loads(request.content)["messages"]
            calls.append(messages)
            assert messages[0]["content"] == REVIEW_RULES
            return response("synthetic non-JSON output", review=True)

        async with environment(core, tmp_path, monkeypatch) as (client, config, plan, _):
            async with httpx.AsyncClient(transport=httpx.MockTransport(provider)) as transport:
                with Core(core.settings, ModelClient(core.settings, transport)) as worker:
                    await admit(client, config, plan, 1, "/记住这个 " + FACT)
                    assert await worker.run_once("seed-owner-fact")
                    before = core.store.rows("SELECT * FROM memories")
                    source, accepted = await admit(client, config, plan, 2,
                                                  "/记住这个 " + FACT.replace("蓝色", "红色"))
                    assert await worker.run_once("invalid-write-review")
                    assert core.store.one("SELECT state,last_error_code FROM jobs WHERE job_id=?",
                                          (accepted["job_id"],)) == {
                        "state": "FAILED", "last_error_code": "MEMORY_REVIEW_INVALID",
                    }
                    assert len(calls) == 1
                    assert core.store.rows("SELECT * FROM memories") == before
                    assert not core.store.rows("SELECT 1 FROM memory_evidence WHERE event_id=?",
                                               (source["event_id"],))
                    assert core.store.rows("SELECT state FROM model_usage") == [
                        {"state": "SETTLED"},
                    ]
                    assert not core.store.rows(
                        "SELECT 1 FROM audit_log WHERE action='memory.recall_paused'",
                    )
    run(scenario())
