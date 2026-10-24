"""Private companion composition through authenticated capture and native services."""

import asyncio
import json
from datetime import UTC, timedelta
from functools import partial
from zoneinfo import ZoneInfo

import httpx
import pytest
from cryptography.fernet import Fernet
from PIL import Image

from companion import budget, live_text, private_life
from companion.backup import backup, restore
from companion.contracts import CoreError
from companion.core import Core
from companion.diary import DIARY_RULES
from companion.model import EXTRACT_RULES, RECALL_RULES, ModelClient
from companion.reflection import REFLECTION_RULES
from companion.reply_segments import SegmentedReplyConfig
from companion_onebot_bridge import capture, continuous
from tests.conftest import run
from tests.test_continuous_private_replies import worker
from tests.test_onebot_capture import capture_approval, source_window
from tests.test_onebot_direct import raw_event
from tests.test_onebot_window import reply_approval


def companion_options(core):
    return {
        "automatic_reply": True,
        "companion_scope": "owner_private_v1",
        "companion_approval_sha256": "a" * 64,
        "history_scope": "owner_private",
        "history_approval_sha256": "b" * 64,
        "max_history_turns": 15,
        "max_history_utf8_bytes": 16000,
        "memory_scope": "owner_private",
        "memory_approval_sha256": "c" * 64,
        "max_model_attempts": 100,
        "window_options": {
            "ends_at": (core.clock.now + timedelta(hours=24))
            .astimezone(ZoneInfo("Asia/Shanghai"))
            .isoformat()
        },
        "paid_options": {
            "per_request_calls": 3,
            "window_calls": 100,
            "unlimited_period_spend": True,
            "daily_micro_usd": None,
            "monthly_micro_usd": None,
            "window_micro_usd": None,
            "expires_at": (core.clock.now + timedelta(days=2)).isoformat(),
            "rates_valid_until": (core.clock.now + timedelta(days=2)).isoformat(),
        },
        "core_options": {
            **{name + "_enabled": True for name in private_life.FEATURES},
            "max_recent_messages": 30,
            "extraction_min_events": 1,
            "extraction_idle_seconds": 0,
            "quiet_start": 0,
            "quiet_end": 0,
            "diary_min_source_events": 1,
            "reflection_min_sources": 1,
            "reflection_daily_micro_usd": 50000,
            "reflection_monthly_micro_usd": 100000,
            "context_soft_tokens": 16000,
        },
    }


def test_independent_life_worker_finishes_nightly_memory_diary_reflection_goals(
    core, tmp_path, monkeypatch
):
    from companion import life_worker

    calls, number = [], 0

    async def source(ws):
        nonlocal number
        number += 1
        await ws.send(
            json.dumps(raw_event(number, text=("我喜欢龙井茶" if number == 1 else "请详细解释")))
        )
        await ws.wait_closed()

    def respond(request):
        messages = json.loads(request.content)["messages"]
        rules = messages[0]["content"]
        calls.append(rules)
        data = (
            json.loads(messages[-1]["content"])
            if rules in {EXTRACT_RULES, DIARY_RULES, REFLECTION_RULES}
            else None
        )
        if rules == EXTRACT_RULES:
            text = json.dumps(
                {
                    "memories": [
                        {
                            "subject": "user",
                            "kind": "preference",
                            "fact_key": "drink",
                            "content": "用户喜欢龙井茶",
                            "evidence_event_id": data[0]["event_id"],
                            "evidence_quote": "我喜欢龙井茶",
                        }
                    ]
                },
                ensure_ascii=False,
            )
        elif rules == DIARY_RULES:
            source = next(item for item in data["sources"] if item["kind"] == "owner_message")
            text = json.dumps(
                {
                    "schema_version": 1,
                    "local_date": data["local_date"],
                    "title": "一起聊茶",
                    "worth_saving": True,
                    "sections": [
                        {
                            "kind": "observation",
                            "text": source["text"],
                            "evidence": [
                                {"source_id": source["source_id"], "exact_quote": source["text"]}
                            ],
                        }
                    ],
                },
                ensure_ascii=False,
            )
        elif rules == REFLECTION_RULES:
            source = data["sources"][0]
            text = json.dumps(
                {
                    "suggestions": [
                        {
                            "kind": "next_question",
                            "text": "下次进一步了解龙井茶的制作方法",
                            "evidence": [
                                {"source_id": source["source_id"], "exact_quote": source["text"]}
                            ],
                        }
                    ]
                },
                ensure_ascii=False,
            )
        else:
            text = "我们慢慢聊聊龙井茶。"
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": text}}],
                "usage": {"prompt_tokens": 100, "completion_tokens": 40},
            },
        )

    async def scenario():
        async with source_window(
            core, tmp_path, source, live=True, approved=True, scope_options=companion_options(core)
        ) as (window, client, protocol, _, _):
            async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as model:
                core.model = ModelClient(core.settings, model)
                for _ in range(2):
                    await window.capture_once(client, protocol)
                    await window.approve_captured(client, capture_approval(window))
                    assert await core.run_once()
                    await continuous.pump_private(window, client, protocol)
                assert core.affect.snapshot()["state"]["interaction_tone"] == "detailed"
                local = core.clock.now.astimezone(ZoneInfo("Asia/Shanghai"))
                day = local.date()
                core.clock.now = (local + timedelta(days=1)).replace(hour=4).astimezone(UTC)
                monkeypatch.setattr(life_worker, "Core", partial(Core, clock=core.clock))
                monkeypatch.setattr(
                    life_worker, "ModelClient", lambda settings, _: ModelClient(settings, model)
                )
                tasks = [
                    asyncio.create_task(life_worker.life_worker(core.settings)),
                ]
                try:
                    async with asyncio.timeout(12):
                        while not (row := core.consolidation.status(day)) or row["state"] not in {
                            "COMPLETE",
                            "FAILED",
                            "CANCELLED",
                        }:
                            for task in tasks:
                                if task.done():
                                    task.result()
                            await asyncio.sleep(0.02)
                    assert row["state"] == "COMPLETE", row
                finally:
                    for task in tasks:
                        task.cancel()
                    await asyncio.gather(*tasks, return_exceptions=True)
                assert core.store.one("SELECT COUNT(*) n FROM memories")["n"] == 1
                assert (
                    core.store.one(
                        "SELECT state FROM diary_runs WHERE local_date=?", (day.isoformat(),)
                    )["state"]
                    == "COMMITTED"
                )
                assert core.store.one("SELECT state FROM reflection_runs")["state"] == "COMMITTED"
                goal = core.store.one("SELECT * FROM goals")
                assert goal["kind"] == "self" and goal["reflection_suggestion_id"]
                with Core(core.settings, core.model, core.clock) as restarted:
                    assert restarted.goals.get(goal["goal_id"])["topic"] == goal["topic"]
                    assert restarted.memory.search("龙井茶")
                assert calls.count(DIARY_RULES) == calls.count(REFLECTION_RULES) == 1
                assert all(
                    row["state"] == "SETTLED"
                    for row in core.store.rows("SELECT state FROM model_usage")
                )
                assert live_text.status(core.store, core.settings, core.clock)["approved"]

    run(scenario())


def test_continuous_history_memory_restart_and_proactive_share(core, tmp_path, monkeypatch):
    calls = []
    send_checks = []

    async def source(ws):
        await ws.send(json.dumps(raw_event(1, text="我喜欢龙井茶")))
        await ws.wait_closed()

    def model_response(request):
        payload = json.loads(request.content)
        messages = payload["messages"]
        calls.append(messages)
        if messages[0]["content"] == EXTRACT_RULES:
            event_id = json.loads(messages[-1]["content"])[0]["event_id"]
            text = json.dumps(
                {
                    "memories": [
                        {
                            "subject": "user",
                            "kind": "preference",
                            "fact_key": "drink",
                            "content": "用户喜欢龙井茶",
                            "evidence_event_id": event_id,
                            "evidence_quote": "我喜欢龙井茶",
                        }
                    ]
                },
                ensure_ascii=False,
            )
        elif messages[0]["content"] == RECALL_RULES:
            text = '{"relevant":["m0"]}'
        else:
            text = "记下了，聊聊龙井茶吧。"
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": text}}],
                "usage": {"prompt_tokens": 100, "completion_tokens": 40},
            },
        )

    async def scenario():
        async with source_window(
            core, tmp_path, source, live=True, approved=True, scope_options=companion_options(core)
        ) as (
            window,
            client,
            protocol,
            _,
            sends,
        ):
            pause, stop_worker = asyncio.Event(), asyncio.Event()

            async def checked(response):
                if response.request.url.path == "/internal/v1/live-text/send-check":
                    await response.aread()
                    send_checks.append((response.status_code, response.json()))

            client.event_hooks["response"].append(checked)

            def emitted(kind, **values):
                if kind == "automatic_input_delivered":
                    pause.set()

            monkeypatch.setattr(continuous, "emit", emitted)
            async with httpx.AsyncClient(transport=httpx.MockTransport(model_response)) as model:
                core.model = ModelClient(core.settings, model)
                task = asyncio.create_task(worker(core, stop_worker))
                try:
                    result = await asyncio.wait_for(
                        continuous.run(window, client, protocol, pause=pause), 15
                    )
                finally:
                    stop_worker.set()
                    await task
                assert result["attempt_states"] == {"ACK": 1}
                assert await core.extract_once()
                assert core.store.one("SELECT content FROM memories")["content"] == "用户喜欢龙井茶"
                assert core.store.one("SELECT COUNT(*) n FROM memory_evidence")["n"] == 1
                with Core(core.settings, core.model, core.clock) as restarted:
                    assert restarted.memory.search("龙井茶")
                    restarted.clock.advance(3601)
                    assert restarted.schedule_proactive()
                    assert await restarted.run_once()
                assert (
                    core.store.one(
                        "SELECT state FROM outbox WHERE action_type='qq.private.proactive'"
                    )["state"]
                    == "PENDING"
                )
                await continuous.pump_private(window, client, protocol)
                assert send_checks[-1][0] == 200, send_checks[-1]
                assert len(sends) == 2 and window.status()["attempt_states"] == {"ACK": 2}
                assert live_text.status(core.store, core.settings, core.clock)["approved"]
                assert all(
                    row["state"] == "SETTLED"
                    for row in core.store.rows("SELECT state FROM model_usage")
                )

    run(scenario())


@pytest.mark.parametrize("background_inflight", [False, True])
def test_five_images_with_memory_and_history_deliver_five_fragments(
    core, tmp_path, background_inflight
):
    from tests.test_live_image_input import options as image_options
    from tests.test_live_image_input import png
    from tests.test_onebot_images import image_segment

    calls, downloads, number = [], [], 0
    started, release = asyncio.Event(), asyncio.Event()
    pieces = [f"第{index}张图片让我想起你说过的龙井茶。" for index in range(1, 6)]

    async def source(ws):
        nonlocal number
        number += 1
        message = [{"type": "text", "data": {"text": "我喜欢龙井茶"}}]
        if number == 2:
            message += [image_segment() for _ in range(5)]
        await ws.send(json.dumps(raw_event(number, message=message)))
        await ws.wait_closed()

    def respond(request):
        payload = json.loads(request.content)
        messages = payload["messages"]
        calls.append(messages)
        if messages[0]["content"] == EXTRACT_RULES:
            event = json.loads(messages[-1]["content"])[0]
            text = json.dumps(
                {
                    "memories": [
                        {
                            "subject": "user",
                            "kind": "preference",
                            "fact_key": "drink",
                            "content": "用户喜欢龙井茶",
                            "evidence_event_id": event["event_id"],
                            "evidence_quote": "我喜欢龙井茶",
                        }
                    ]
                },
                ensure_ascii=False,
            )
        elif messages[0]["content"] == RECALL_RULES:
            text = '{"relevant":["m0"]}'
        elif isinstance(messages[0]["content"], list):
            text = "五张图片依次展示茶杯、茶叶和绿色背景。"
        else:
            text = "".join(pieces) if number == 2 else "我们一起聊聊龙井茶。"
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": text}}],
                "usage": {"prompt_tokens": 100, "completion_tokens": 40},
            },
        )

    def cdn(request):
        downloads.append(request)
        return httpx.Response(200, content=png(), headers={"Content-Type": "image/png"})

    async def resolve(*_):
        return ["1.1.1.1"]

    async def scenario():
        options, images = companion_options(core), image_options()
        options["image_scope"] = images["image_scope"]
        options["image_approval_sha256"] = images["image_approval_sha256"]
        policy = SegmentedReplyConfig(max_segments="unlimited", interval_ms=0)
        options.update(segmented_reply=policy, max_send_attempts=20)
        options["window_options"].update(max_attempts=20, max_visible_replies=20)
        options["core_options"].update(images["core_options"], segmented_reply=policy)
        options["core_options"]["content"] = images["core_options"]["content"].model_copy(
            update={"max_images_per_event": "unlimited"}
        )
        options["paid_options"]["image_input"] = images["paid_options"]["image_input"].model_copy(
            update={"max_images_per_request": "unlimited"}
        )
        async with source_window(
            core,
            tmp_path,
            source,
            live=True,
            approved=True,
            scope_options=options,
            rich_input_scope="references_v1",
        ) as (window, client, protocol, _, sends):

            async def overlapping_provider(request):
                if json.loads(request.content)["messages"][-1]["content"] == "background synthetic":
                    started.set()
                    await release.wait()
                return respond(request)

            async with httpx.AsyncClient(
                transport=httpx.MockTransport(overlapping_provider)
            ) as model:
                core.model = ModelClient(core.settings, model)
                core.content.options = core.settings.content
                background = None

                async def accepted(response):
                    if (
                        background
                        and response.is_success
                        and response.request.url.path == "/internal/v1/inbox/events"
                    ):
                        release.set()

                client.event_hooks["response"].append(accepted)
                for _ in range(2):
                    await window.capture_once(client, protocol)
                    await window.approve_captured(
                        client,
                        capture_approval(window),
                        image_transport=httpx.MockTransport(cdn),
                        image_resolver=resolve,
                    )
                    if background:
                        await background
                    assert await core.run_once()
                    rows = core.store.rows("SELECT state,last_error_code FROM jobs")
                    assert all(row["state"] == "SUCCEEDED" for row in rows), rows
                    for _ in range(5 if number == 2 else 1):
                        await continuous.pump_private(window, client, protocol)
                    if number == 1:
                        assert await core.extract_once()
                        if background_inflight:
                            messages = [{"role": "user", "content": "background synthetic"}]
                            request_id = budget.reserve(
                                core.store,
                                core.settings,
                                None,
                                "reflection",
                                messages,
                                core.clock(),
                                clock=core.clock,
                            )
                            background = asyncio.create_task(
                                budget.complete(
                                    core.model,
                                    core.store,
                                    core.settings,
                                    request_id,
                                    messages,
                                    "reflection",
                                    core.clock,
                                )
                            )
                            await started.wait()
                assert len(downloads) == 5 and len(calls) == 5 + int(background_inflight)
                assert len(sends) == 6
                assert [item["message"][0]["data"]["text"] for item in sends[1:]] == pieces
                chat = calls[-1]
                assert len(chat[-1]["content"]) == 6
                assert {"role": "user", "content": "我喜欢龙井茶"} in chat
                assert {"role": "assistant", "content": "我们一起聊聊龙井茶。"} in chat
                assert any("用户喜欢龙井茶" in item["content"] for item in chat[:-1])
                assert window.status()["attempt_states"] == {"ACK": 6}
                assert core.store.rows("PRAGMA foreign_key_check") == []

    run(scenario())


@pytest.mark.parametrize("first_response_delay", [0.0, capture.MAX_CLOCK_DRIFT_SECONDS + 0.1])
def test_real_api_lifespan_and_independent_chat_worker_keep_two_turn_history(
    core, tmp_path, monkeypatch, first_response_delay,
):
    from companion import __main__, api
    from companion import core as core_module
    from tests.test_owner_recall_bridge_media import advance_clock

    calls, instances = [], []
    next_input = asyncio.Event()

    async def source(ws):
        await ws.send(json.dumps(raw_event(1, text="今天讨论龙井茶")))
        await next_input.wait()
        await ws.send(json.dumps(raw_event(2, text="接着刚才的话题聊")))
        await ws.wait_closed()

    def construct(*args, **kwargs):
        instance = Core(*args, clock=core.clock, **kwargs)
        instances.append(instance)
        return instance

    async def respond(request):
        calls.append(json.loads(request.content)["messages"])
        if len(calls) == 1:
            await asyncio.sleep(first_response_delay)
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": "我们接着讨论龙井茶。"}}],
                "usage": {"prompt_tokens": 100, "completion_tokens": 20},
            },
        )

    async def scenario():
        monkeypatch.setattr(api, "Core", construct)
        monkeypatch.setattr(core_module, "Core", construct)
        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as model:
            monkeypatch.setattr(
                __main__, "ModelClient", lambda settings, _: ModelClient(settings, model)
            )
            async with source_window(
                core,
                tmp_path,
                source,
                live=True,
                approved=True,
                scope_options=companion_options(core),
                independent_api=True,
            ) as (window, client, protocol, _, sends):
                pause = asyncio.Event()

                def emitted(kind, **values):
                    if kind == "automatic_input_delivered":
                        if len(sends) == 1:
                            next_input.set()
                        else:
                            pause.set()

                monkeypatch.setattr(continuous, "emit", emitted)
                clock_stop = asyncio.Event()
                clock_task = asyncio.create_task(advance_clock(core, clock_stop))
                task = asyncio.create_task(__main__.worker(core.settings))
                try:
                    result = await asyncio.wait_for(
                        continuous.run(window, client, protocol, pause=pause), 15
                    )
                    assert result["attempt_states"] == {"ACK": 2}
                finally:
                    clock_stop.set()
                    task.cancel()
                    await asyncio.gather(task, clock_task, return_exceptions=True)
        assert len(instances) == 2 and all(instance is not core for instance in instances)
        assert instances[0].store is not instances[1].store
        assert len(calls) == len(sends) == 2
        assert {"role": "user", "content": "今天讨论龙井茶"} in calls[1]
        assert {"role": "assistant", "content": "我们接着讨论龙井茶。"} in calls[1]
        assert core.store.one("SELECT COUNT(*) n FROM model_usage WHERE state='SETTLED'")["n"] == 2

    run(scenario())


@pytest.mark.parametrize("boundary", ["stop", "expiry"])
def test_background_memory_cannot_commit_after_window_boundary(core, tmp_path, boundary):
    async def source(ws):
        await ws.send(json.dumps(raw_event(1, text="我喜欢龙井茶")))
        await ws.wait_closed()

    def respond(request):
        messages = json.loads(request.content)["messages"]
        if messages[0]["content"] == EXTRACT_RULES:
            event = json.loads(messages[-1]["content"])[0]
            if boundary == "stop":
                live_text.stop_window(
                    core.store,
                    live_text.WindowReference(
                        window_sha256=core.settings.live_text_window.plan_sha256
                    ),
                    core.clock,
                )
            else:
                core.clock.now = core.settings.live_text_window.ends_at
            text = json.dumps(
                {
                    "memories": [
                        {
                            "subject": "user",
                            "kind": "preference",
                            "fact_key": "drink",
                            "content": "用户喜欢龙井茶",
                            "evidence_event_id": event["event_id"],
                            "evidence_quote": "我喜欢龙井茶",
                        }
                    ]
                },
                ensure_ascii=False,
            )
        else:
            text = "记下了。"
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": text}}],
                "usage": {"prompt_tokens": 100, "completion_tokens": 20},
            },
        )

    async def scenario():
        async with source_window(
            core, tmp_path, source, live=True, approved=True, scope_options=companion_options(core)
        ) as (window, client, protocol, _, _):
            async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as model:
                core.model = ModelClient(core.settings, model)
                await window.capture_once(client, protocol)
                await window.approve_captured(client, capture_approval(window))
                assert await core.run_once()
                await window.send_once(client, protocol, reply_approval(window.plan, core))
                assert not await core.extract_once()
                assert not core.store.one("SELECT 1 FROM memories")
                assert (
                    core.store.one("SELECT COUNT(*) n FROM model_usage WHERE state='SETTLED'")["n"]
                    == 2
                )
                with pytest.raises(CoreError):
                    private_life.ensure_active(core.store, core.settings, core.clock)
                assert not core.schedule_proactive()
                assert core.diary.schedule_due() == []
                assert core.consolidation.schedule_due() == []

    run(scenario())


def test_current_companion_without_offer_uses_text_fallback(core, tmp_path, monkeypatch):
    library = tmp_path / "memes/happy"
    library.mkdir(parents=True)
    Image.new("RGB", (20, 20), "yellow").save(library / "happy.png")

    async def source(ws):
        await ws.send(json.dumps(raw_event(1, text="今天完成任务了")))
        await ws.wait_closed()

    async def scenario():
        options = companion_options(core)
        async with source_window(
            core,
            tmp_path,
            source,
            live=True,
            approved=True,
            scope_options=options,
            bridge_options={"meme_directory": str(library.parent)},
        ) as (
            window,
            client,
            protocol,
            _,
            sends,
        ):
            pause, stop_worker = asyncio.Event(), asyncio.Event()

            def emitted(kind, **values):
                if kind == "automatic_input_delivered":
                    pause.set()

            monkeypatch.setattr(continuous, "emit", emitted)
            async with httpx.AsyncClient(
                transport=httpx.MockTransport(
                    lambda request: httpx.Response(
                        200,
                        json={
                            "choices": [{"message": {"content": "完成啦！⬡happy⬡"}}],
                            "usage": {"prompt_tokens": 100, "completion_tokens": 20},
                        },
                    )
                )
            ) as model:
                core.model = ModelClient(core.settings, model)
                task = asyncio.create_task(worker(core, stop_worker))
                try:
                    await asyncio.wait_for(
                        continuous.run(window, client, protocol, pause=pause), 15
                    )
                finally:
                    stop_worker.set()
                    await task
            assert len(sends) == 1
            assert sends[0]["message"] == [{"type": "text", "data": {"text": "完成啦！"}}]

    run(scenario())


@pytest.mark.parametrize("change_during_dispatch", [False, True])
def test_native_discovery_goal_read_and_proactive_delivery_keep_source_checks(
    core, tmp_path, change_during_dispatch,
):
    from companion.config import ObservationAdapterConfig
    from companion.exploration import ReadItem, ReadResult
    from tests.test_exploration import URL, discover, read_goal

    async def source(ws):
        await ws.send(json.dumps(raw_event(1, text="一起聊聊手绘游戏")))
        await ws.wait_closed()

    async def scenario():
        options = companion_options(core)
        policy = SegmentedReplyConfig(max_segments="unlimited", interval_ms=0)
        options.update(segmented_reply=policy, max_send_attempts=20)
        options["window_options"].update(max_attempts=20, max_visible_replies=20)
        options["core_options"]["segmented_reply"] = policy
        options["link_scope"] = "owner_links_v1"
        options["core_options"].update(
            {
                "content_enabled": True,
                "observation_enabled": True,
                "observation_adapters": {
                    "bili": ObservationAdapterConfig(
                        source="bilibili", token="synthetic-reader-token-12345"
                    )
                },
                "exploration_daily_requests": 8,
                "exploration_monthly_requests": 30,
                "exploration_daily_bytes": 4_194_304,
            }
        )
        async with source_window(
            core,
            tmp_path,
            source,
            live=True,
            approved=True,
            scope_options=options,
            rich_input_scope="references_v1",
        ) as (window, client, protocol, _, sends):
            async def hook(request):
                messages = json.loads(request.content)["messages"]
                if change_during_dispatch and '"kind":"share"' in messages[-1]["content"]:
                    core.store.execute("UPDATE observations SET source_sha256=? WHERE "
                                       "result_status='FETCHED'", ("0" * 64,))

            async with httpx.AsyncClient(
                event_hooks={"request": [hook]},
                transport=httpx.MockTransport(
                    lambda request: httpx.Response(
                        200,
                        json={
                            "choices": [{"message": {"content": "一起看看吧。"}}],
                            "usage": {"prompt_tokens": 100, "completion_tokens": 20},
                        },
                    )
                )
            ) as model:
                core.model = ModelClient(core.settings, model)
                await window.capture_once(client, protocol)
                await window.approve_captured(client, capture_approval(window))
                assert await core.run_once()
                await continuous.pump_private(window, client, protocol)
                discover(core)
                goal, item = read_goal(core)
                result = core.exploration.complete(
                    "bili",
                    item["request_id"],
                    ReadResult(
                        lease_token=item["lease_token"],
                        status="SUCCESS",
                        used_bytes=1800,
                        items=[
                            ReadItem(
                                title="新游戏",
                                text="简介：游戏采用手绘画面，周末进行试玩。",
                                url=URL,
                            )
                        ],
                    ),
                )
                assert core.goals.get(goal["goal_id"])["state"] == "DONE"
                core.clock.advance(3601)
                assert core.social.flush()
                with Core(core.settings, core.model, core.clock) as independent_worker:
                    assert await independent_worker.run_once("share-wording")
                if change_during_dispatch:
                    assert core.store.one("SELECT last_error_code FROM jobs "
                                          "WHERE kind='activity'")["last_error_code"] == (
                        "NOTICE_SOURCE_CHANGED"
                    )
                    assert len(sends) == 1
                    assert not core.store.rows("SELECT * FROM outbox "
                                               "WHERE action_type='qq.private.proactive'")
                    usage = core.store.one("SELECT state,billed_micro_usd FROM model_usage "
                                           "WHERE scenario='activity'")
                    assert usage["state"] == "UNKNOWN" and usage["billed_micro_usd"] > 0
                    return
                assert core.store.one("SELECT state,last_error_code FROM jobs "
                                      "WHERE kind='activity'") == {
                    "state": "SUCCEEDED", "last_error_code": None,
                }
                await continuous.pump_private(window, client, protocol)
                await continuous.pump_private(window, client, protocol)
                assert len(sends) == 3
                assert sends[-2]["message"] == [{"type": "text", "data": {"text": "一起看看吧。"}}]
                assert sends[-1]["message"] == [{"type": "text", "data": {"text": URL}}]
                assert (
                    core.store.one(
                        "SELECT state FROM outbox WHERE action_type='qq.private.proactive'"
                    )["state"]
                    == "SENT"
                )
                observed = core.store.one(
                    "SELECT * FROM observations WHERE event_id=?", (result["event_ids"][0],)
                )
                assert observed["result_status"] == "FETCHED"
                with core.store.transaction():
                    running = core.exploration.create("bili", "discover", "stop-running")
                    queued = core.exploration.create("bili", "discover", "stop-queued")
                assert core.exploration.lease("bili")["request_id"] == running
                response = await client.post(
                    "/internal/v1/live-text/stop", json={"window_sha256": window.plan.sha256}
                )
                assert response.status_code == 200
                assert (
                    core.store.one(
                        "SELECT state FROM exploration_requests WHERE request_id=?", (running,)
                    )["state"]
                    == "UNKNOWN"
                )
                assert (
                    core.store.one(
                        "SELECT state FROM exploration_requests WHERE request_id=?", (queued,)
                    )["state"]
                    == "CANCELLED"
                )
                assert not private_life.ready(core.store, core.settings, core.clock)

    run(scenario())


@pytest.mark.parametrize("drift", ["content", "source", "usage", "privacy", "unknown"])
def test_private_companion_refuses_changed_or_uncertain_delivery(core, tmp_path, drift):
    from companion.privacy import advance_epoch
    from companion.store import encode
    from companion.transport import digest

    async def source(ws):
        await ws.send(json.dumps(raw_event(1)))
        await ws.wait_closed()

    async def scenario():
        async with source_window(
            core, tmp_path, source, live=True, approved=True, scope_options=companion_options(core)
        ) as (window, client, protocol, _, sends):
            async with httpx.AsyncClient(
                transport=httpx.MockTransport(
                    lambda request: httpx.Response(
                        200,
                        json={
                            "choices": [{"message": {"content": "合成回答。"}}],
                            "usage": {"prompt_tokens": 100, "completion_tokens": 20},
                        },
                    )
                )
            ) as model:
                core.model = ModelClient(core.settings, model)
                await window.capture_once(client, protocol)
                await window.approve_captured(client, capture_approval(window))
                assert await core.run_once()
                if drift == "content":
                    value = {"text": "替换的合成正文。"}
                    core.store.execute(
                        "UPDATE outbox SET content_json=?,content_sha256=?",
                        (encode(value), digest(value)),
                    )
                elif drift == "source":
                    core.store.execute(
                        "UPDATE events SET body_json=? WHERE source='qq.onebot'",
                        (encode({"text": "替换的输入"}),),
                    )
                elif drift == "usage":
                    core.store.execute("UPDATE model_admissions SET payload_sha256=?", ("0" * 64,))
                elif drift == "unknown":
                    core.store.execute("UPDATE model_usage SET state='UNKNOWN'")
                else:
                    with core.store.transaction():
                        advance_epoch(core.store)
                with pytest.raises((CoreError, ValueError, httpx.HTTPStatusError)):
                    await continuous.pump_private(window, client, protocol)
                assert not sends
                assert not core.store.one("SELECT 1 FROM outbox WHERE state='SENT'")

    run(scenario())


def test_signed_memory_commands_continue_chat_and_erasure_survives_old_backup(core, tmp_path):
    number, text = 0, "/记住这个：我喜欢龙井茶"

    async def source(ws):
        nonlocal number
        number += 1
        await ws.send(json.dumps(raw_event(number, text=text)))
        await ws.wait_closed()

    async def scenario():
        nonlocal text
        async with source_window(
            core, tmp_path, source, live=True, approved=True, scope_options=companion_options(core)
        ) as (window, client, protocol, _, sends):
            async with httpx.AsyncClient(
                transport=httpx.MockTransport(
                    lambda request: httpx.Response(
                        200,
                        json={
                            "choices": [{"message": {"content": "接着聊吧。"}}],
                            "usage": {"prompt_tokens": 100, "completion_tokens": 20},
                        },
                    )
                )
            ) as model:
                core.model = ModelClient(core.settings, model)

                async def turn():
                    await window.capture_once(client, protocol)
                    await window.approve_captured(client, capture_approval(window))
                    assert await core.run_once()
                    row = core.store.one(
                        "SELECT state,last_error_code FROM jobs ORDER BY rowid DESC"
                    )
                    assert row["state"] == "SUCCEEDED", row
                    await continuous.pump_private(window, client, protocol)

                await turn()
                memory = core.store.one("SELECT * FROM memories")
                assert memory["status"] == "CONFIRMED"
                key = Fernet.generate_key()
                archive, restored = tmp_path / "before-forget.enc", tmp_path / "restored.db"
                backup(core.settings.database, str(archive), key)
                text = "/忘记这一条 " + memory["memory_id"]
                await turn()
                assert not core.memory.search("龙井茶")
                text = "现在继续聊天"
                await turn()
                assert len(sends) == 3
                assert live_text.status(core.store, core.settings, core.clock)["approved"]
                restore(str(archive), str(restored), key, core.settings.erasure_ledger)
                settings = core.settings.model_copy(update={"database": str(restored)})
                with Core(settings, core.model, core.clock) as recovered:
                    assert not recovered.memory.search("龙井茶")
                    assert recovered.store.rows("PRAGMA foreign_key_check") == []

    run(scenario())
