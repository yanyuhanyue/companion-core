"""Only diary work refused before a model reservation can defer safely."""

import json
from datetime import timedelta
from zoneinfo import ZoneInfo

import httpx
import pytest

from companion import budget
from companion.core import Core
from companion.model import ModelClient, ModelResponse
from tests.conftest import run
from tests.test_diary import DAY, seed_day
from tests.test_onebot_capture import source_window
from tests.test_private_companion import companion_options


@pytest.mark.parametrize("outcome", ["success", "exhausted", "unknown"])
def test_received_empty_diary_preserves_bounded_retry(core, tmp_path, outcome):
    draft_model = seed_day(core)
    calls = []

    async def source(ws):
        await ws.wait_closed()

    async def provider(request):
        calls.append(request)
        text = " "
        if len(calls) > 1 and outcome == "success":
            payload = json.loads(request.content)
            text = (await draft_model.complete(payload["messages"], "diary")).text
        body = {"choices": [{"message": {"content": text}}]}
        if outcome != "unknown":
            body["usage"] = {"prompt_tokens": 140, "completion_tokens": 80}
        return httpx.Response(200, json=body)

    async def scenario():
        async with source_window(
            core, tmp_path, source, live=True, approved=True,
            scope_options=companion_options(core),
        ):
            async with httpx.AsyncClient(transport=httpx.MockTransport(provider)) as transport:
                core.diary.model = ModelClient(core.settings, transport)
                core.diary.schedule_day(DAY)
                assert await core.diary.generate_once("empty-first")
                first = core.store.one("SELECT * FROM model_usage")
                row = core.store.one("SELECT * FROM diary_runs")
                assert row["last_error_code"] == "MODEL_EMPTY_RESPONSE"
                assert row["state"] == ("FAILED" if outcome == "unknown" else "RETRY_WAIT")
                assert first["state"] == ("UNKNOWN" if outcome == "unknown" else "SETTLED")
                for _ in range(core.settings.diary_max_attempts):
                    core.clock.advance(3600)
                    await core.diary.generate_once("empty-recovery")
                usages = core.store.rows("SELECT * FROM model_usage ORDER BY rowid")
                assert usages[0] == first
                expected = 1 if outcome == "unknown" else (
                    2 if outcome == "success" else core.settings.diary_max_attempts
                )
                assert len(calls) == len(usages) == expected
                assert len({row["request_id"] for row in usages}) == expected
                assert core.store.one("SELECT state FROM diary_runs")["state"] == (
                    "COMMITTED" if outcome == "success" else "FAILED"
                )
                assert not core.store.rows("SELECT * FROM outbox")

    run(scenario())


@pytest.mark.parametrize("recovery", ["settled", "unknown", "stop", "day_reset"])
def test_diary_admission_refusal_defers_without_losing_attempts_or_replaying_unknown(
    core,
    tmp_path,
    monkeypatch,
    recovery,
):
    draft_model = seed_day(core)

    async def source(ws):
        await ws.wait_closed()

    async def scenario():
        options = companion_options(core)
        # Keep the same approval valid across a daily quota boundary.
        options["window_options"]["ends_at"] = (
            (core.clock.now + timedelta(hours=48))
            .astimezone(
                ZoneInfo("Asia/Shanghai"),
            )
            .isoformat()
        )
        if recovery == "day_reset":
            options["paid_options"]["daily_calls"] = 1
        async with source_window(
            core, tmp_path, source, live=True, approved=True, scope_options=options
        ) as (window, client, _, _, _):
            original = core.diary.repo.claim
            competing = []

            def claim_then_compete(worker_id):
                claimed = original(worker_id)
                if claimed:
                    request = budget.reserve(
                        core.store,
                        core.settings,
                        None,
                        "memory_extract",
                        [{"role": "user", "content": "synthetic competitor"}],
                        core.clock(),
                        clock=core.clock,
                    )
                    competing.append(request)
                    if recovery == "day_reset":
                        budget.settle(
                            core.store, core.settings, request, ModelResponse("", 10, 1, 0)
                        )
                return claimed

            core.diary.schedule_day(DAY)
            # Claim attempts are visible even when more than max_attempts never dispatch.
            core.store.execute("UPDATE diary_runs SET attempts=max_attempts")
            monkeypatch.setattr(core.diary.repo, "claim", claim_then_compete)
            assert await core.diary.generate_once("admission-race")
            row = core.store.one("SELECT * FROM diary_runs")
            assert row["state"] == "RETRY_WAIT" and row["model_request_id"] is None
            assert row["attempts"] == row["max_attempts"] + 1
            assert row["last_error_code"] == (
                "MODEL_DAILY_BUDGET_EXHAUSTED"
                if recovery == "day_reset"
                else "MODEL_PROVIDER_CONCURRENCY_EXHAUSTED"
            )
            assert draft_model.calls == []
            monkeypatch.setattr(core.diary.repo, "claim", original)
            if recovery != "day_reset":
                budget.settle(
                    core.store,
                    core.settings,
                    competing[0],
                    None if recovery == "unknown" else ModelResponse("", 10, 1, 0),
                )
            if recovery == "stop":
                await window.stop_live(client)
            core.clock.advance(31)
            if recovery == "day_reset":
                assert not await core.diary.generate_once("before-reset")
                core.clock.advance(24 * 3600)

            async def provider(request):
                payload = json.loads(request.content)
                result = await draft_model.complete(payload["messages"], "diary")
                return httpx.Response(
                    200,
                    json={
                        "choices": [{"message": {"content": result.text}}],
                        "usage": {"prompt_tokens": 140, "completion_tokens": 80},
                    },
                )

            async with httpx.AsyncClient(transport=httpx.MockTransport(provider)) as model:
                with Core(core.settings, ModelClient(core.settings, model), core.clock) as worker:
                    worker.diary.schedule_day(DAY)
                    worked = await worker.diary.generate_once("after-recovery")
                    assert worked is (recovery in {"settled", "day_reset"})
                    row = worker.store.one("SELECT state,attempts FROM diary_runs")
                    if worked:
                        assert row["state"] == "COMMITTED"
                        assert row["attempts"] == core.settings.diary_max_attempts + 2
                        assert len(draft_model.calls) == 1
                    else:
                        assert draft_model.calls == []
                        assert row["state"] == ("CANCELLED" if recovery == "stop" else "RETRY_WAIT")
                    if recovery == "unknown":
                        assert (
                            worker.store.one("SELECT state FROM model_usage")["state"] == "UNKNOWN"
                        )

    run(scenario())
