"""Project recall can nominate confirmed facts without bypassing relevance review."""

import json

import httpx
import pytest

from companion import live_text
from companion.store import encode
from tests import test_live_memory_recall
from tests.conftest import ingest, run
from tests.test_live_image_input import options as image_options
from tests.test_live_memory_recall import readonly_window
from tests.test_memory_commands import fact


def empty_semantic_search(core, monkeypatch):
    core.settings.max_recent_messages = 0
    # Reproduce the live failure independently of embedding model numeric drift.
    monkeypatch.setattr(core.memory.index, "scores",
                        lambda query, rows: {row["memory_id"]: 0.1 for row in rows})


@pytest.mark.parametrize("query", [
    "你还记得我之前明确确认过的那条项目事实吗？请说说。",
    "之前确认的项目设定是什么？",
    "我让你记住的项目事实是什么？",
    "项目上你记得什么？",
    "请回顾一下我们以前的项目约定。",
    "你之前记下的项目信息有哪些？",
    "你还记得之前确认的项目事实吗？不要猜测。",
    "请回顾之前确认的项目约定，不用展开。",
    "你记得项目事实吗？不要使用列表。",
    "What do you remember about our project?",
    "Recall the previously confirmed project settings.",
])
def test_generic_project_recall_reaches_review_when_semantic_search_is_empty(
    core, monkeypatch, query,
):
    fact(core, 1, "这个工程使用琥珀作为内部验收代号。")
    empty_semantic_search(core, monkeypatch)
    ingest(core, 2, query)
    assert run(core.run_once())
    assert [scenario for _, scenario in core.model.calls] == ["memory_review", "chat"]
    assert "琥珀" in core.model.calls[-1][0][1]["content"]


@pytest.mark.parametrize("query", [
    "不要再回忆之前确认的项目事实。",
    "不谈记忆，项目今晚能不能上线？",
    "别提之前的项目约定，聊聊天气。",
    "之前没确认的项目设定是什么？",
    "项目里的记忆缓存如何设计？",
    "项目之前已确认上线了，今天天气怎样？",
    "我还记得项目约定。今天吃什么？",
    "项目已经确认了。你记得我喜欢喝什么吗？",
    "你记得我喜欢喝什么吗？",
    "今天下午会下雨吗？",
    "Don't recall the project facts.",
    "I remember our project. What is the weather today?",
])
def test_negative_or_unrelated_queries_do_not_add_a_review(core, monkeypatch, query):
    fact(core, 1, "这个工程使用琥珀作为内部验收代号。")
    empty_semantic_search(core, monkeypatch)
    ingest(core, 2, query)
    assert run(core.run_once())
    assert [scenario for _, scenario in core.model.calls] == ["chat"]
    assert "琥珀" not in encode(core.model.calls)


@pytest.mark.parametrize("ineligible", [
    "hypothesis", "conflict", "missing_evidence", "redacted_evidence", "other_subject",
])
def test_fallback_uses_existing_evidence_and_only_confirmed_project_facts(
    core, monkeypatch, ineligible,
):
    memory_id = fact(core, 1, "这个工程使用琥珀作为内部验收代号。")
    if ineligible == "hypothesis":
        core.store.execute("UPDATE memories SET status='HYPOTHESIS' WHERE memory_id=?",
                           (memory_id,))
    elif ineligible == "conflict":
        core.store.execute("UPDATE memories SET needs_review=1 WHERE memory_id=?", (memory_id,))
    elif ineligible == "missing_evidence":
        core.store.execute("DELETE FROM memory_evidence WHERE memory_id=?", (memory_id,))
    elif ineligible == "redacted_evidence":
        core.store.execute("UPDATE events SET redacted=1 WHERE event_id='evt_test_1'")
    else:
        core.store.execute("UPDATE memories SET subject='user' WHERE memory_id=?", (memory_id,))
    empty_semantic_search(core, monkeypatch)
    ingest(core, 2, "项目上你记得什么？")
    assert run(core.run_once())
    assert [scenario for _, scenario in core.model.calls] == ["chat"]
    assert "琥珀" not in encode(core.model.calls)


def test_ordinary_catalog_is_bounded_and_selector_can_reject_every_candidate(core, monkeypatch):
    for number in range(8):
        fact(core, number, f"这个工程的第{number}项确认代号是琥珀{number}。")
    empty_semantic_search(core, monkeypatch)
    core.model.recall = lambda payload: '{"relevant":[]}'
    ingest(core, 100, "项目上你记得什么？")
    assert run(core.run_once())
    assert [scenario for _, scenario in core.model.calls] == ["memory_review", "chat"]
    payload = json.loads(core.model.calls[0][0][-1]["content"])
    assert len(payload["memories"]) == 6
    assert all(row["subject"] == "project" and row["status"] == "CONFIRMED"
               for row in payload["memories"])
    assert "琥珀" not in encode(core.model.calls[-1][0])


@pytest.mark.parametrize("select", [True, False])
def test_live_fallback_keeps_approval_review_and_frozen_context_boundaries(
    core, tmp_path, monkeypatch, select,
):
    empty_semantic_search(core, monkeypatch)

    def response(request, payload, number):
        recall = '"operation":"recall"' in payload["messages"][-1]["content"]
        text = json.dumps({"relevant": ["m0"] if select else []}) if recall else "已核对。"
        return httpx.Response(200, json={
            "choices": [{"message": {"content": text}}],
            "usage": {"prompt_tokens": 100, "completion_tokens": 20},
        })

    async def scenario():
        async with readonly_window(
            core, tmp_path, query="我让你记住的项目事实是什么？", respond=response,
        ) as (_, _, capture, _, calls, _, scores, memory_id, outsider):
            await capture()
            assert await core.run_once()
            assert len(calls) == 2
            assert all(ids == [memory_id] for ids in scores)
            assert "紫云三号" not in encode(calls)
            frozen = json.loads(core.store.one(
                "SELECT value FROM runtime_meta WHERE key LIKE 'live_text_memory:%'",
            )["value"])
            assert [row["memory_id"] for row in frozen["candidates"]] == [memory_id]
            assert [row["memory_id"] for row in frozen["selected"]] == (
                [memory_id] if select else []
            )
            assert bool(frozen["review_request_id"])
            assert ("青竹七号" in encode(calls[-1]["messages"])) is select
            assert core.store.one("SELECT COUNT(*) AS n FROM model_admissions")["n"] == 2
            assert core.store.rows("SELECT scenario,state FROM model_usage ORDER BY rowid") == [
                {"scenario": "memory_review", "state": "SETTLED"},
                {"scenario": "chat", "state": "SETTLED"},
            ]
            # Native scoped searches must not downgrade an unapproved damaged fact.
            assert core.store.one("SELECT status,confidence FROM memories WHERE memory_id=?",
                                  (outsider,)) == {"status": "CONFIRMED", "confidence": 1.0}

    run(scenario())


def test_live_fallback_still_consumes_the_paid_call_budget(core, tmp_path, monkeypatch):
    empty_semantic_search(core, monkeypatch)

    async def scenario():
        async with readonly_window(
            core, tmp_path, query="之前确认的项目设定是什么？", paid={"window_calls": 1},
        ) as (_, _, capture, _, calls, sends, _, _, _):
            await capture()
            assert await core.run_once()
            assert len(calls) == 1 and sends == []
            assert core.store.rows("SELECT scenario,state FROM model_usage") == [
                {"scenario": "memory_review", "state": "SETTLED"},
            ]
            assert core.store.rows("SELECT * FROM outbox") == []

    run(scenario())


@pytest.mark.parametrize("input_limit", [16000, 1000])
def test_selected_fact_reaches_chat_with_deployed_rich_content_configuration(
    core, tmp_path, monkeypatch, input_limit,
):
    empty_semantic_search(core, monkeypatch)

    def scoped_facts(instance):
        # Real captured evidence IDs are 68 characters; short fixture IDs can hide
        # the exact 3,200-byte soft-bound failure seen with these production rules.
        memory_id = fact(instance, "a" * 59, "本次验收项目代号是青竹七号")
        outsider = fact(instance, 901, "未批准的另一个项目代号是紫云三号。")
        return memory_id, outsider, [live_text.memory_fact(
            instance.store, instance.settings, instance.clock, memory_id,
        )]

    monkeypatch.setattr(test_live_memory_recall, "scope_facts", scoped_facts)
    image = image_options()
    scope = {key: value for key, value in image.items()
             if key not in {"core_options", "paid_options"}}
    scope.update(rich_input_scope="references_v1", link_scope="owner_links_v1")

    async def scenario():
        async with readonly_window(
            core, tmp_path,
            query="你还记得我之前明确确认过的那条项目事实吗？请说说。",
            options=scope,
            core_options={**image["core_options"], "context_soft_tokens": 3200,
                          "max_output_tokens": 768},
            paid={**image["paid_options"], "per_request_calls": 3,
                  "max_input_tokens": input_limit, "max_output_tokens": 768},
        ) as (_, _, capture, _, calls, sends, _, memory_id, _):
            await capture()
            assert await core.run_once()
            frozen = json.loads(core.store.one(
                "SELECT value FROM runtime_meta WHERE key LIKE 'live_text_memory:%'",
            )["value"])
            assert [row["memory_id"] for row in frozen["selected"]] == [memory_id]
            if input_limit == 16000:
                assert len(calls) == 2
                assert "青竹七号" in encode(calls[-1]["messages"])
                context = json.loads(core.store.one(
                    "SELECT value FROM runtime_meta WHERE key LIKE 'live_text_context:%'",
                )["value"])
                assert [row["memory_id"] for row in context["memory_sources"]] == [memory_id]
            else:
                assert len(calls) == 1 and sends == []
                assert core.store.rows("SELECT * FROM outbox") == []
                assert core.store.one(
                    "SELECT last_error_code FROM jobs WHERE event_id NOT LIKE 'evt_test_%'",
                )["last_error_code"] == "MODEL_INPUT_LIMIT"

    run(scenario())
