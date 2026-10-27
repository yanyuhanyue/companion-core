"""Real native authority and delivery entry points around concurrent read-only probes."""

import asyncio

import httpx
import pytest

from companion_onebot_bridge.owner_send import OwnerOneBot
from tests.conftest import run
from tests.support.native_dispatch import attempts, dispatch, staged

PROBES = {"/get_login_info", "/get_status", "/get_version_info"}


def preparation_calls(monkeypatch):
    calls = []
    prepare = OwnerOneBot.prepare_delivery

    async def tracked(driver, item):
        calls.append(item["message_id"])
        return await prepare(driver, item)

    monkeypatch.setattr(OwnerOneBot, "prepare_delivery", tracked)
    return calls


@pytest.mark.parametrize("phase", ["capture", "send"])
@pytest.mark.parametrize("change", ["stop", "parent_cancel"])
def test_native_stop_or_cancel_joins_every_probe_without_send(
    core, tmp_path, monkeypatch, phase, change,
):
    prepared = preparation_calls(monkeypatch)

    async def scenario():
        async with staged(core, tmp_path) as (owner, client, protocol, guard, sends):
            entered, release = asyncio.Event(), asyncio.Event()
            tasks, finished = [], []

            async def hold(request):
                if request.url.path not in PROBES:
                    return
                tasks.append(asyncio.current_task())
                if len(tasks) == 3:
                    entered.set()
                try:
                    await release.wait()
                finally:
                    finished.append(asyncio.current_task())

            protocol.event_hooks["request"].append(hold)
            operation = (owner.probe(client, protocol) if phase == "capture" else
                         dispatch(owner, client, protocol, guard))
            task = asyncio.create_task(operation)
            try:
                await asyncio.wait_for(entered.wait(), 10)
                assert not sends
                if change == "stop":
                    owner.stop()
                    release.set()
                    with pytest.raises(ValueError):
                        await asyncio.wait_for(task, 10)
                else:
                    task.cancel()
                    with pytest.raises(asyncio.CancelledError):
                        await asyncio.wait_for(task, 10)
                assert len(finished) == 3 and all(child.done() for child in tasks)
                assert not sends and not guard.dispatched
                assert attempts(owner) == {} and not prepared
                assert guard.current_item is None and guard.prepared_message is None
                assert not owner.db.execute("SELECT 1 FROM results").fetchone()
            finally:
                release.set()
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
    run(scenario())


def test_native_delivery_waits_for_all_three_results_then_uses_original_send_fence(
    core, tmp_path, monkeypatch,
):
    prepared = preparation_calls(monkeypatch)

    async def scenario():
        async with staged(core, tmp_path) as (owner, client, protocol, guard, sends):
            entered, release = asyncio.Event(), asyncio.Event()
            completed, tasks = set(), []
            original = protocol._transport

            class Observed(httpx.AsyncBaseTransport):
                async def handle_async_request(self, request):
                    if request.url.path in PROBES:
                        tasks.append(asyncio.current_task())
                        if len(tasks) == 3:
                            entered.set()
                        await release.wait()
                        response = await original.handle_async_request(request)
                        completed.add(request.url.path)
                        return response
                    assert completed == PROBES
                    assert guard.dispatched and attempts(owner) == {"IO_STARTED": 1}
                    return await original.handle_async_request(request)

                async def aclose(self):
                    await original.aclose()

            protocol._transport = Observed()
            task = asyncio.create_task(dispatch(owner, client, protocol, guard))
            try:
                await asyncio.wait_for(entered.wait(), 10)
                assert not sends and not guard.dispatched and attempts(owner) == {}
                assert not prepared and guard.current_item is None
                release.set()
                assert await asyncio.wait_for(task, 10)
                assert all(child.done() for child in tasks)
                assert len(sends) == 1 and attempts(owner) == {"ACK": 1}
                assert len(prepared) == 1
            finally:
                release.set()
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
    run(scenario())


@pytest.mark.parametrize("phase", ["capture", "send"])
@pytest.mark.parametrize("path", sorted(PROBES))
def test_each_probe_revalidates_its_request_after_async_authority_check(
    core, tmp_path, monkeypatch, phase, path,
):
    prepared = preparation_calls(monkeypatch)

    async def scenario():
        async with staged(core, tmp_path) as (owner, client, protocol, guard, sends):
            inflight, dispatched = {}, []
            original_check, original_transport = owner.check, protocol._transport

            async def track(request):
                inflight[asyncio.current_task()] = request

            async def check(*args, **kwargs):
                result = await original_check(*args, **kwargs)
                request = inflight.get(asyncio.current_task())
                if request is not None and request.url.path == path:
                    request.headers["Authorization"] = "changed-synthetic-credential"
                return result

            class Observed(httpx.AsyncBaseTransport):
                async def handle_async_request(self, request):
                    dispatched.append(request.url.path)
                    return await original_transport.handle_async_request(request)

                async def aclose(self):
                    await original_transport.aclose()

            owner.check, protocol._transport = check, Observed()
            protocol.event_hooks["request"].append(track)
            with pytest.raises(ValueError, match="OWNER_PROTOCOL_REQUEST_CHANGED"):
                if phase == "capture":
                    await owner.probe(client, protocol)
                else:
                    await dispatch(owner, client, protocol, guard)
            assert path not in dispatched and not sends and not guard.dispatched
            assert attempts(owner) == {} and not prepared
            assert guard.current_item is None and guard.prepared_message is None
            assert not owner.db.execute("SELECT 1 FROM results").fetchone()
    run(scenario())
