"""Bridge evidence failure must not prevent authenticated Core revocation."""

import json
from functools import partial

import httpx
import pytest

from companion import api, budget, live_text
from companion.config import ObservationAdapterConfig
from companion.contracts import CoreError
from companion.core import Core
from companion.model import ModelClient, ModelResponse
from companion_onebot_bridge import continuous
from tests.conftest import run
from tests.test_onebot_capture import capture_approval, source_window
from tests.test_onebot_direct import raw_event
from tests.test_private_companion import companion_options


def damage(window, fault):
    if fault == "missing":
        window.plan.journal.unlink()
    elif fault == "head":
        with window.db:
            window.db.execute("UPDATE test_window SET journal_head=?", ("f" * 64,))
    else:
        window.plan.journal.write_bytes(b"{broken-json\n")


@pytest.mark.parametrize("fault", ["broken", "missing", "head"])
def test_journal_failure_revokes_through_api_lifespan_and_independent_worker(
    core, tmp_path, monkeypatch, fault,
):
    monkeypatch.setattr(api, "Core", partial(Core, clock=core.clock))
    calls = []

    async def source(ws):
        await ws.send(json.dumps(raw_event(1)))
        await ws.wait_closed()

    def provider(request):
        calls.append(request)
        return httpx.Response(200, json={
            "choices": [{"message": {"content": "合成回复"}}],
            "usage": {"prompt_tokens": 100, "completion_tokens": 20},
        })

    async def scenario():
        options = companion_options(core)
        options["link_scope"] = "owner_links_v1"
        options["core_options"].update(
            content_enabled=True, observation_enabled=True,
            exploration_daily_requests=8, exploration_monthly_requests=30,
            exploration_daily_bytes=4_194_304,
            observation_adapters={"bili": ObservationAdapterConfig(
                source="bilibili", token="synthetic-reader-token-12345",
            )},
        )
        async with source_window(
            core, tmp_path, source, live=True, approved=True, independent_api=True,
            scope_options=options, rich_input_scope="references_v1",
        ) as (window, client, protocol, _, sends):
            await window.capture_once(client, protocol)
            await window.approve_captured(client, capture_approval(window))
            async with httpx.AsyncClient(transport=httpx.MockTransport(provider)) as model:
                with Core(core.settings, ModelClient(core.settings, model), core.clock) as worker:
                    assert worker.store is not core.store
                    assert await worker.run_once("stop-recovery-worker")
                    running = worker.exploration.create("bili", "discover", "running")
                    queued = worker.exploration.create("bili", "discover", "queued")
                    assert worker.exploration.lease("bili")["request_id"] == running
                    messages = [{"role": "user", "content": "synthetic background extraction"}]
                    reserved = budget.reserve(worker.store, worker.settings, None,
                                              "memory_extract", messages, worker.clock(),
                                              clock=worker.clock)
                    damage(window, fault)
                    before = (window.plan.journal.read_bytes()
                              if window.plan.journal.exists() else None)
                    with pytest.raises((ValueError, FileNotFoundError)):
                        await continuous.run(window, client, protocol)
                    assert window.core_stop_ack is True
                    assert window.stop_path.read_bytes() == b"OWNER_STOP\n"
                    assert live_text.stopped(worker.store, window.plan.sha256)
                    assert (window.plan.journal.read_bytes()
                            if window.plan.journal.exists() else None) == before
                    assert worker.store.one("SELECT state FROM outbox")["state"] == "CANCELLED"
                    assert worker.store.one(
                        "SELECT state FROM exploration_requests WHERE request_id=?", (running,),
                    )["state"] == "UNKNOWN"
                    assert worker.store.one(
                        "SELECT state FROM exploration_requests WHERE request_id=?", (queued,),
                    )["state"] == "CANCELLED"
                    assert worker.exploration.lease("bili") is None
                    assert not await worker.run_once("after-stop")
                    with pytest.raises(CoreError):
                        budget.reserve(worker.store, worker.settings, None, "memory_extract",
                                       messages, worker.clock(), clock=worker.clock)
                    # Revocation keeps existing custody and permits late known settlement.
                    budget.settle(worker.store, worker.settings, reserved,
                                  ModelResponse("", 10, 1, 0))
                    assert worker.store.one(
                        "SELECT state FROM model_usage WHERE request_id=?", (reserved,),
                    )["state"] == "SETTLED"
                    assert len(calls) == 1 and sends == []

    run(scenario())


@pytest.mark.parametrize("entry", ["stop_live", "stop_core"])
def test_direct_revocation_does_not_require_readable_journal(core, tmp_path, entry):
    async def source(ws):
        await ws.wait_closed()

    async def scenario():
        async with source_window(core, tmp_path, source, live=True, approved=True,
                                 scope_options={"automatic_reply": True}) as (
            window, client, _, _, _,
        ):
            damage(window, "broken")
            with pytest.raises(ValueError):
                await getattr(window, entry)(client)
            assert window.core_stop_ack is True
            assert live_text.stopped(core.store, window.plan.sha256)

    run(scenario())


def test_damaged_journal_and_unreachable_core_report_unconfirmed(core, tmp_path):
    async def source(ws):
        await ws.wait_closed()

    def disconnected(request):
        raise httpx.ConnectError("synthetic private failure")

    async def scenario():
        async with source_window(core, tmp_path, source, live=True, approved=True,
                                 scope_options={"automatic_reply": True}) as (
            window, client, protocol, _, _,
        ):
            damage(window, "broken")
            client._transport = httpx.MockTransport(disconnected)
            with pytest.raises(ValueError, match="LIVE_TEXT_CORE_STOP_UNCONFIRMED"):
                await continuous.run(window, client, protocol)
            assert window.core_stop_ack is False
            assert window.stop_path.read_bytes() == b"OWNER_STOP\n"
            assert not live_text.stopped(core.store, window.plan.sha256)
            assert window.plan.journal.read_bytes() == b"{broken-json\n"

    run(scenario())


def test_durable_stop_ack_survives_core_outage_and_mutated_plan_cannot_retarget(core, tmp_path):
    async def source(ws):
        await ws.wait_closed()

    def disconnected(request):
        pytest.fail("A verified durable STOP receipt must not require another HTTP call")

    async def scenario():
        async with source_window(core, tmp_path, source, live=True, approved=True,
                                 scope_options={"automatic_reply": True}) as (
            window, client, _, _, _,
        ):
            original = window.plan.sha256
            window.plan.sha256 = "f" * 64
            with pytest.raises(ValueError, match="WINDOW_IMMUTABLE_OR_MISSING"):
                await window.stop_core(client)
            assert live_text.stopped(core.store, original) and window.core_stop_ack
            window.plan.sha256 = original
            result = await window.stop_live(client)
            assert result["core_stop_confirmed"] is True
            client._transport = httpx.MockTransport(disconnected)
            await window.stop_core(client)
            assert window.core_stop_ack is True

    run(scenario())
