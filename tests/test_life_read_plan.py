"""Life produces and adjusts native plans across independent Core/Reader processes."""

import asyncio
import json
import os
import subprocess
import sys

from pydantic import SecretStr

from tests.conftest import run
from tests.support.goal_read_plan import TOKEN, URLS
from tests.test_life_decision_context import prepared
from tests.test_onebot_capture import real_core_http
from tests.test_social_owner_authority import close_provider_clients as close_provider_clients


async def life_child(core, tmp_path, phase, number):
    config, output = tmp_path / f"plan-{number}.json", tmp_path / f"plan-{number}-result.json"
    config.write_text(json.dumps({
        "settings": core.settings.model_dump(mode="python"), "now": core.clock(),
        "phase": phase, "result_path": str(output),
    }, default=lambda value: value.get_secret_value() if isinstance(value, SecretStr)
       else str(value)), encoding="utf-8")
    process = await asyncio.to_thread(subprocess.run,
        [sys.executable, "-B", "-m", "tests.life_plan_process", str(config)],
        capture_output=True, timeout=60)
    assert process.returncode == 0, process.stderr.decode(errors="replace")
    result = json.loads(output.read_text(encoding="utf-8"))
    assert result["pid"] != os.getpid()
    return result


async def reader_child(core, port, url):
    process = await asyncio.to_thread(subprocess.run,
        [sys.executable, "-B", "-m", "tests.goal_plan_reader_process",
         "--base", f"http://127.0.0.1:{port}", "--now", core.clock(), "--url", url],
        capture_output=True, timeout=60)
    assert process.returncode == 0, process.stderr.decode(errors="replace")
    result = json.loads(process.stdout.decode().splitlines()[-1])
    assert result["worked"] and result["calls"] == 3, result


def prepare(core, tmp_path):
    prepared(core, tmp_path, text="可以比较这些恒星资料：\n" + "\n".join(URLS))
    core.settings.memory_enabled = True
    core.settings.life_loop.max_daily_decisions = 10
    core.settings.life_loop.min_interval_seconds = 60
    core.settings.observation_adapters["bili"].token = SecretStr(TOKEN)
    core.settings.observation_adapters["bili"].reader_max_requests = 6
    core.settings.exploration_daily_requests = core.settings.exploration_monthly_requests = 40


def test_native_life_proposes_reorders_reads_and_finishes_one_plan(core, tmp_path):
    prepare(core, tmp_path)

    async def scenario():
        async with real_core_http(core) as port:
            first = await life_child(core, tmp_path, "PROPOSE", 1)
            assert first["provider_calls"] == 1, first
            goal = core.store.one("SELECT * FROM goals")
            assert goal and json.loads(goal["scope_json"])["urls"] == URLS, first
            steps = core.store.rows("SELECT * FROM goal_steps ORDER BY position")
            assert [step["state"] for step in steps] == ["RUNNING", "WAITING_DECISION",
                                                          "WAITING_DECISION"]
            original_ids = {step["step_id"] for step in steps}
            for index, (url, phase) in enumerate(zip(
                [URLS[0], URLS[2], URLS[1]], ["REORDER", "CONTINUE", "FINISH"], strict=True,
            ), 2):
                await reader_child(core, port, url)
                parent = core.goals.get(goal["goal_id"])
                assert parent["state"] == ("DONE" if phase == "FINISH" else "WAITING_INPUT")
                core.clock.advance(61)
                result = await life_child(core, tmp_path, phase, index)
                assert result["provider_calls"] == 1, result
                assert all(job["state"] != "FAILED" for job in result["jobs"]), result
            final = core.goals.plan(goal["goal_id"])
            assert final["state"] == "DONE"
            assert {step["step_id"] for step in final["steps"]} == original_ids
            assert [step["scope"]["url"] for step in final["steps"]] == [URLS[0], URLS[2], URLS[1]]
            assert all(step["state"] == "DONE" for step in final["steps"])
            assert len(core.store.rows("SELECT * FROM exploration_requests")) == 3
            interests = result["materials"][0]["decision_context"]["self_interests"]
            assert len(interests) == 1 and interests[0]["independent_sessions"] == 1
            assert len(core.store.rows("SELECT * FROM trait_evidence")) == 1
            restarted = await life_child(core, tmp_path, "FINISH", 5)
            assert restarted["provider_calls"] == 0
            assert len(core.store.rows("SELECT * FROM model_usage")) == 4
            assert not core.store.rows("SELECT * FROM outbox")
    run(scenario())


def test_native_life_waits_then_abandons_without_reading_tail(core, tmp_path):
    prepare(core, tmp_path)

    async def scenario():
        async with real_core_http(core) as port:
            first = await life_child(core, tmp_path, "PROPOSE", 1)
            assert first["provider_calls"] == 1, first
            await reader_child(core, port, URLS[0])
            core.clock.advance(61)
            waited = await life_child(core, tmp_path, "WAIT", 2)
            assert waited["provider_calls"] == 1, waited
            assert not core.store.rows("SELECT * FROM jobs WHERE state='FAILED'"), waited
            idle = await life_child(core, tmp_path, "ABANDON", 3)
            assert idle["provider_calls"] == 0, idle
            core.clock.advance(3601)
            abandoned = await life_child(core, tmp_path, "ABANDON", 4)
            assert abandoned["provider_calls"] == 1, abandoned
            goal = core.store.one("SELECT goal_id,state FROM goals")
            assert goal["state"] == "CANCELLED", abandoned
            assert [step["state"] for step in core.goals.plan(goal["goal_id"])["steps"]] == [
                "DONE", "CANCELLED", "CANCELLED",
            ]
            assert len(core.store.rows("SELECT * FROM exploration_requests")) == 1
            assert not core.store.rows("SELECT * FROM trait_evidence")
            assert len(core.store.rows("SELECT * FROM model_usage")) == 3
    run(scenario())
