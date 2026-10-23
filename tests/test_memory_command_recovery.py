"""Recover one owner correction across mutation and Outbox crash boundaries."""

import json
from functools import partial

import pytest

from companion import api, live_text
from companion.contracts import CoreError
from companion.core import Core
from tests.conftest import run
from tests.test_m1 import remember
from tests.test_onebot_capture import capture_approval, source_window
from tests.test_onebot_direct import raw_event
from tests.test_private_companion import companion_options


class ProcessExit(BaseException):
    pass


@pytest.mark.parametrize("stage", [
    "before_mutation", "before_commit", "after_commit", "before_outbox", "after_outbox",
])
def test_correction_crash_recovers_same_fact_and_one_receipt(core, tmp_path, monkeypatch, stage):
    mid = remember(core, 1, "我喜欢绿茶", confirmed=True)
    core.store.execute("UPDATE jobs SET state='SUCCEEDED'")
    monkeypatch.setattr(api, "Core", partial(Core, clock=core.clock))

    async def source(ws):
        await ws.send(json.dumps(raw_event(2, text=f"/纠正这一条 {mid} 我喜欢红茶")))
        await ws.wait_closed()

    async def scenario():
        async with source_window(
            core, tmp_path, source, live=True, approved=True, independent_api=True,
            scope_options=companion_options(core),
        ) as (window, client, protocol, _, _):
            await window.capture_once(client, protocol)
            await window.approve_captured(client, capture_approval(window))
            decide, command, enqueue = (core.memory.decide, core.command_reply,
                                        core.expression.enqueue)

            def crash_decide(*args, **kwargs):
                if stage == "before_commit":
                    decide(*args, **kwargs)
                raise ProcessExit()

            async def crash_command(*args):
                await command(*args)
                raise ProcessExit()

            def crash_enqueue(*args, **kwargs):
                if stage == "after_outbox":
                    enqueue(*args, **kwargs)
                raise ProcessExit()

            with monkeypatch.context() as fault:
                if stage in {"before_mutation", "before_commit"}:
                    fault.setattr(core.memory, "decide", crash_decide)
                elif stage == "after_commit":
                    fault.setattr(core, "command_reply", crash_command)
                else:
                    fault.setattr(core.expression, "enqueue", crash_enqueue)
                with pytest.raises(ProcessExit):
                    await core.run_once("before-crash")
            committed = core.store.one(
                "SELECT memory_id FROM memories WHERE supersedes_memory_id=?", (mid,),
            )
            assert bool(committed) == (stage not in {"before_mutation", "before_commit"})
            assert not core.store.rows("SELECT * FROM outbox")
            core.clock.advance(core.settings.lease_seconds + 1)
            with Core(core.settings, core.model, core.clock) as restarted:
                assert await restarted.run_once("after-crash")
                result = restarted.store.one(
                    "SELECT memory_id,status FROM memories WHERE supersedes_memory_id=?", (mid,),
                )
                assert result["status"] == "CONFIRMED"
                if committed:
                    assert result["memory_id"] == committed["memory_id"]
                assert restarted.store.one("SELECT COUNT(*) n FROM memories")["n"] == 2
                assert restarted.store.one("SELECT COUNT(*) n FROM memory_revisions")["n"] == 1
                outbox = restarted.store.rows("SELECT content_json,state FROM outbox")
                assert len(outbox) == 1 and outbox[0]["state"] == "PENDING"
                assert result["memory_id"] in outbox[0]["content_json"]
                assert not restarted.store.rows("SELECT * FROM jobs WHERE state<>'SUCCEEDED'")
                assert not await restarted.run_once("already-complete")
                assert restarted.store.one(
                    "SELECT COUNT(*) n FROM events WHERE event_type='memory.corrected'",
                )["n"] == 1
                assert restarted.store.one("SELECT COUNT(*) n FROM event_dependencies")["n"] == 1
            assert core.model.calls == []

    run(scenario())


@pytest.mark.parametrize("boundary", ["stop", "expiry", "forget", "lease"])
def test_correction_replay_rechecks_authority_and_cannot_recreate_erased_fact(
    core, tmp_path, boundary,
):
    mid = remember(core, 1, "我喜欢绿茶", confirmed=True)
    core.store.execute("UPDATE jobs SET state='SUCCEEDED'")

    async def source(ws):
        await ws.send(json.dumps(raw_event(2, text=f"/纠正这一条 {mid} 我喜欢红茶")))
        await ws.wait_closed()

    async def scenario():
        async with source_window(core, tmp_path, source, live=True, approved=True,
                                 scope_options=companion_options(core)) as (
            window, client, protocol, _, _,
        ):
            await window.capture_once(client, protocol)
            await window.approve_captured(client, capture_approval(window))
            job = core.claim_job("before-boundary")
            if boundary == "forget":
                assert "CONFIRMED" in await core.command_reply(job)
                revised = core.store.one(
                    "SELECT memory_id FROM memories WHERE supersedes_memory_id=?", (mid,),
                )["memory_id"]
                core.memory.decide(revised, "forget", core.clock())
            elif boundary == "stop":
                await window.stop_live(client)
            elif boundary == "expiry":
                core.clock.now = window.plan.end
            else:
                core.clock.advance(core.settings.lease_seconds + 1)
            with pytest.raises(CoreError):
                await core.command_reply(job)
            assert not core.store.rows("SELECT * FROM outbox")
            if boundary == "forget":
                assert core.store.one(
                    "SELECT COUNT(*) n FROM memories WHERE content<>'' OR status<>'FORGOTTEN'",
                )["n"] == 0
            else:
                assert core.store.one("SELECT COUNT(*) n FROM memories")["n"] == 1
            if boundary == "stop":
                assert live_text.stopped(core.store, window.plan.sha256)

    run(scenario())
