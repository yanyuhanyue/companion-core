"""Authenticated localhost composition; native BGE, synthetic paid/send transports."""

import json

import httpx
import pytest

from companion import qq_rich
from companion.store import encode
from tests.conftest import run
from tests.test_live_history_memory import combined_options
from tests.test_live_memory_recall import readonly_window
from tests.test_onebot_reference_reader import enriched
from tests.test_qq_rich_input import card

OWNER_FIRST = "本次临时项目的验收代号改说白帆九号。"
OWNER_NOW = "那这个临时项目的代号还是原来的？"
REFERENCE = "RICHDATA_ONLY_NOT_OWNER"


def mixed():
    return [
        {"type": "reply", "data": {"id": "-42"}},
        {"type": "text", "data": {"text": OWNER_FIRST}},
        card(text=REFERENCE),
    ]


def test_references_only_without_owner_or_history_text_freezes_no_facts_and_calls_chat_once(
    core, tmp_path, monkeypatch,
):
    async def scenario():
        async with readonly_window(
            core,
            tmp_path,
            options=combined_options(rich_input_scope="references_v1"),
            core_options={"max_recent_messages": 4},
            raw_messages=[[card(text=REFERENCE)]],
        ) as (_, _, capture, send, calls, sends, scores, _, _):
            queries = []
            original = core.memory.search

            def search(query, *args, **kwargs):
                queries.append(query)
                return original(query, *args, **kwargs)

            monkeypatch.setattr(core.memory, "search", search)
            await capture()
            assert await core.run_once()
            assert queries == scores == []
            assert len(calls) == 1
            assert core.store.one("SELECT scenario,state FROM model_usage") == {
                "scenario": "chat", "state": "SETTLED",
            }
            frozen = json.loads(core.store.one(
                "SELECT value FROM runtime_meta WHERE key LIKE 'live_text_memory:%'",
            )["value"])
            assert frozen["candidates"] == frozen["selected"] == []
            context = json.loads(core.store.one(
                "SELECT value FROM runtime_meta WHERE key LIKE 'live_text_context:%'",
            )["value"])
            assert context["memory_sources"] == context["sources"] == []
            assert REFERENCE in encode(calls[0]["messages"])
            assert "青竹七号" not in encode(calls[0]["messages"])
            assert json.loads(core.store.one(
                "SELECT body_json FROM events WHERE source='qq.onebot'",
            )["body_json"])["text"] == ""
            assert await send()
            assert len(sends) == 1

    run(scenario())


@pytest.mark.parametrize("drift", [False, True])
def test_rich_history_review_chat_and_delivery_preserve_source_and_owner_boundaries(
    core,
    tmp_path,
    drift,
):
    async def scenario():
        async with readonly_window(
            core,
            tmp_path,
            options=combined_options(rich_input_scope="references_v1"),
            core_options={"max_recent_messages": 4, "context_soft_tokens": 1},
            raw_messages=[mixed(), [{"type": "text", "data": {"text": OWNER_NOW}}]],
        ) as (_, _, capture, send, calls, sends, _, memory_id, _):
            queries = []
            original = core.memory.search

            def search(query, *args, **kwargs):
                queries.append(query)
                return original(query, *args, **kwargs)

            core.memory.search = search
            await capture()
            assert await core.run_once()
            assert await send()
            prior = core.store.one(
                "SELECT * FROM events WHERE source='qq.onebot' ORDER BY rowid DESC LIMIT 1"
            )
            await capture()
            assert await core.run_once()
            assert len(calls) == 4
            assert all(REFERENCE not in query and "QQ引用资料" not in query for query in queries)
            assert queries[-1] == "\n".join([OWNER_NOW, OWNER_FIRST, "青竹七号。"])
            review = json.loads(calls[2]["messages"][-1]["content"])
            assert len(review["recent_dialogue"]) == 3
            assert REFERENCE in review["recent_dialogue"][0]["content"]
            assert "非主人话语" in review["recent_dialogue"][0]["content"]
            for payload in calls[2:]:
                assert "白帆九号" in encode(payload) and "青竹七号" in encode(payload)
            messages = calls[-1]["messages"]
            assert "不确定" in messages[0]["content"]
            assert REFERENCE in encode(messages) and "不得" in messages[0]["content"]
            assert messages[-1] == {"role": "user", "content": OWNER_NOW}
            assert core.store.one("SELECT content FROM memories WHERE memory_id=?", (memory_id,))[
                "content"
            ].endswith("青竹七号。")
            usage = core.store.rows("SELECT job_id,scenario,state FROM model_usage ORDER BY rowid")
            assert [row["scenario"] for row in usage] == ["memory_review", "chat"] * 2
            assert all(row["state"] == "SETTLED" for row in usage)
            assert usage[2]["job_id"] == usage[3]["job_id"]
            if drift:
                body = json.loads(prior["body_json"])
                body["rich"]["segments"][1]["fields"][0]["value"] = "changed prior card"
                core.store.execute(
                    "UPDATE events SET body_json=? WHERE event_id=?",
                    (encode(body), prior["event_id"]),
                )
                with pytest.raises((ValueError, httpx.HTTPStatusError)):
                    await send()
            else:
                assert await send()
            assert len(sends) == (1 if drift else 2)

    run(scenario())


@pytest.mark.parametrize("delta,kept", [(-1, False), (0, True)])
def test_rich_history_byte_limit_counts_and_crops_all_three_messages(core, tmp_path, delta, kept):
    first, _ = enriched(mixed())
    reference = qq_rich.context(first["payload"])["content"]
    actual = len((reference + OWNER_FIRST + "青竹七号。").encode("utf-8"))

    async def scenario():
        async with readonly_window(
            core,
            tmp_path,
            options=combined_options(
                rich_input_scope="references_v1", max_history_utf8_bytes=actual + delta
            ),
            core_options={"max_recent_messages": 4},
            raw_messages=[mixed(), [{"type": "text", "data": {"text": OWNER_NOW}}]],
        ) as (_, _, capture, send, calls, _, _, _, _):
            await capture()
            captured = json.loads(core.store.one(
                "SELECT body_json FROM events WHERE source='qq.onebot' ORDER BY rowid DESC LIMIT 1",
            )["body_json"])
            # Count the actual signed reference, including its failed-read coverage.
            assert qq_rich.context(captured)["content"] == reference
            assert await core.run_once()
            assert await send()
            await capture()
            assert await core.run_once()
            review = json.loads(calls[2]["messages"][-1]["content"])
            assert len(review.get("recent_dialogue", [])) == (3 if kept else 0)
            assert (REFERENCE in encode(calls[-1]["messages"])) == kept
            assert calls[-1]["messages"][-1] == {"role": "user", "content": OWNER_NOW}

    run(scenario())


@pytest.mark.parametrize("failure", ["unknown", "budget"])
def test_rich_combined_unknown_and_budget_never_fall_back_to_unaccounted_chat(
    core,
    tmp_path,
    failure,
):
    def unknown(request, payload, number):
        raise httpx.ReadTimeout("SYNTHETIC_RICH_UNKNOWN", request=request)

    async def scenario():
        async with readonly_window(
            core,
            tmp_path,
            options=combined_options(rich_input_scope="references_v1"),
            core_options={"max_recent_messages": 4},
            raw_messages=[mixed()],
            respond=unknown if failure == "unknown" else None,
            paid={"max_input_tokens": 1} if failure == "budget" else None,
        ) as (_, _, capture, _, calls, sends, _, _, _):
            await capture()
            assert await core.run_once()
            assert not core.store.rows("SELECT * FROM outbox") and not sends
            usage = core.store.rows("SELECT scenario,state,billed_micro_usd FROM model_usage")
            if failure == "unknown":
                assert len(calls) == len(usage) == 1
                assert usage[0]["scenario"] == "memory_review" and usage[0]["state"] == "UNKNOWN"
                assert usage[0]["billed_micro_usd"] > 0
            else:
                assert not calls and not usage
            assert not await core.run_once()

    run(scenario())
