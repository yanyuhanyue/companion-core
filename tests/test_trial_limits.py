"""One numeric profile, independent counters, and the original fail-closed window."""

import json
import os
import subprocess
import sys
from pathlib import Path

import httpx
import pytest
from pydantic import ValidationError

from companion.trial_limits import TrialLimits
from companion_onebot_bridge.capture import capacity
from companion_onebot_bridge.spool import Spool
from companion_onebot_bridge.window import TestWindow, WindowPlan
from scripts.trial_limits import load_limits
from tests.conftest import run
from tests.test_live_continuity import continuity_window
from tests.test_live_text_window import live_clients, live_fixture
from tests.test_onebot_window import plan_fixture, stage

ROOT = Path(__file__).resolve().parents[1]


def limits(**changes):
    return TrialLimits.model_validate({
        "max_inputs": 16, "max_model_attempts": 21, "max_send_attempts": 20,
        "max_visible_replies": 18, **changes,
    })


def configured(core, tmp_path, value, *, paid_options=None):
    projected = value.project()
    return live_fixture(core, tmp_path, window_options=projected["bridge_plan"],
                        paid_options={**projected["paid_model"], **(paid_options or {})},
                        **projected["live_text_window"])


def test_profile_projects_independent_limits_without_creating_authority(tmp_path):
    projected = limits(max_input_chars=1000, max_reply_chars=1500).project()
    assert projected["bridge_plan"]["max_new_inputs"] == 16
    assert projected["bridge_plan"]["max_attempts"] == 20
    assert projected["bridge_plan"]["max_visible_replies"] == 18
    assert projected["live_text_window"]["max_model_attempts"] == 21
    assert projected["live_text_window"]["max_send_attempts"] == 18
    assert projected["live_text_window"]["max_input_chars"] == 1000
    assert projected["live_text_window"]["max_reply_chars"] == 1500
    assert projected["paid_model"] == {"window_calls": 21}
    assert set(projected) == {"bridge_plan", "live_text_window", "paid_model"}
    profile = load_limits(ROOT / "trial-limits.example.toml")
    assert profile.max_inputs == profile.max_model_attempts == profile.max_send_attempts == 15


@pytest.mark.parametrize("field,value", [
    ("max_inputs", 0), ("max_inputs", True), ("max_model_attempts", 1.5),
    ("max_model_attempts", 2**31), ("max_send_attempts", -1),
    ("max_visible_replies", 21), ("max_input_chars", 0), ("max_reply_chars", 20001),
])
def test_profile_rejects_invalid_or_inconsistent_limits(field, value):
    data = limits().model_dump()
    with pytest.raises(ValidationError):
        TrialLimits.model_validate({**data, field: value})


def test_cli_uses_one_profile_and_does_not_echo_invalid_private_values(tmp_path):
    command = [sys.executable, "-m", "scripts.trial_limits"]
    result = subprocess.run([*command, str(ROOT / "trial-limits.example.toml")], cwd=ROOT,
                            env={**os.environ, "PYTHONUTF8": "1"}, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == load_limits(ROOT / "trial-limits.example.toml").project()
    profile = tmp_path / "invalid.toml"
    credential = "sk-" + "synthetic" * 4
    profile.write_text('[trial_limits]\nunknown = "' + credential + '"\n', encoding="utf-8")
    result = subprocess.run([*command, str(profile)], cwd=ROOT, capture_output=True, text=True)
    assert result.returncode == 2 and result.stderr.strip() == "INVALID_TRIAL_LIMITS"
    assert credential not in result.stdout + result.stderr


def test_sixteen_inputs_model_calls_and_sends_use_configured_caps(core, tmp_path):
    cfg, plan = configured(core, tmp_path, limits(), paid_options={"daily_calls": 21})

    async def scenario():
        async with live_clients(core, cfg) as (client, protocol, calls, sends):
            with Spool(cfg) as spool:
                window = TestWindow(spool, plan, clock=lambda: core.clock.now)
                await window.open(client)
                for number in range(1, 17):
                    capacity(window)
                    reply = await stage(window, client, core, number)
                    status = await window.send_once(client, protocol, reply)
                    assert status["attempts_reserved"] == number
                assert len(calls) == len(sends) == 16
                assert status["state"] == "STOPPED"
                with pytest.raises(ValueError):
                    await stage(window, client, core, 17)
                assert len(calls) == len(sends) == 16

    run(scenario())


@pytest.mark.parametrize("caps", [
    {"max_inputs": 2, "max_model_attempts": 6, "max_send_attempts": 6, "max_visible_replies": 6},
    {"max_inputs": 6, "max_model_attempts": 2, "max_send_attempts": 6, "max_visible_replies": 6},
    {"max_inputs": 6, "max_model_attempts": 6, "max_send_attempts": 2, "max_visible_replies": 2},
])
def test_each_quota_stops_additional_io_independently(core, tmp_path, caps):
    cfg, plan = configured(core, tmp_path, TrialLimits(**caps))

    async def scenario():
        async with live_clients(core, cfg) as (client, protocol, calls, sends):
            with Spool(cfg) as spool:
                window = TestWindow(spool, plan, clock=lambda: core.clock.now)
                await window.open(client)
                for number in (1, 2):
                    reply = await stage(window, client, core, number)
                    await window.send_once(client, protocol, reply)
                with pytest.raises((ValueError, httpx.HTTPStatusError)):
                    await stage(window, client, core, 3)
                assert len(calls) == len(sends) == 2

    run(scenario())


def test_core_and_bridge_reject_divergent_send_caps(core, tmp_path):
    cfg, plan = configured(core, tmp_path, limits())
    _, _, inventory, _ = plan_fixture(tmp_path)
    changed = {**plan.value, "live_text": {**plan.value["live_text"], "max_send_attempts": 17}}
    with pytest.raises(ValueError, match="INVALID_WINDOW_SEND_LIMIT"):
        WindowPlan(changed, cfg, inventory)


def test_large_trial_profile_does_not_override_daily_budget(core, tmp_path):
    cfg, plan = configured(core, tmp_path, limits(), paid_options={"daily_calls": 1})

    async def scenario():
        async with live_clients(core, cfg) as (client, protocol, calls, sends):
            with Spool(cfg) as spool:
                window = TestWindow(spool, plan, clock=lambda: core.clock.now)
                await window.open(client)
                reply = await stage(window, client, core, 1)
                await window.send_once(client, protocol, reply)
                with pytest.raises((ValueError, httpx.HTTPStatusError)):
                    reply = await stage(window, client, core, 2)
                    await window.send_once(client, protocol, reply)
                assert len(calls) == len(sends) == 1

    run(scenario())


def test_text_limits_above_old_trial_cap_work_through_core_and_bridge(core, tmp_path):
    cfg, plan = configured(core, tmp_path, limits(max_input_chars=1000, max_reply_chars=1000))
    text = "合成输入" * 150
    reply = "合成回答" * 150

    def respond(request):
        return httpx.Response(200, json={
            "choices": [{"message": {"content": reply}}],
            "usage": {"prompt_tokens": 100, "completion_tokens": 20},
        })

    async def scenario():
        async with live_clients(core, cfg, respond=respond) as (client, protocol, calls, sends):
            with Spool(cfg) as spool:
                window = TestWindow(spool, plan, clock=lambda: core.clock.now)
                await window.open(client)
                approval = await stage(window, client, core, 1, text=text)
                await window.send_once(client, protocol, approval)
                assert len(calls) == len(sends) == 1
                assert sends[0]["message"][0]["data"]["text"] == reply

    run(scenario())


@pytest.mark.parametrize("turns", [5, 15])
def test_history_capacity_matches_existing_recent_message_configuration(core, tmp_path, turns):
    async def scenario():
        async with continuity_window(core, tmp_path, turns=turns) as values:
            window, *_ = values
            assert core.settings.max_recent_messages == turns * 2
            assert window.plan.value["live_text"]["max_history_turns"] == turns

    run(scenario())


def test_sixth_captured_reply_can_use_five_complete_history_turns(core, tmp_path):
    profile = TrialLimits(max_inputs=6, max_model_attempts=6, max_send_attempts=6,
                          max_visible_replies=6).project()
    texts = [f"第{index}个已批准的合成输入" for index in range(1, 7)]

    async def scenario():
        async with continuity_window(core, tmp_path, turns=5, texts=texts, scope_options={
            "window_options": profile["bridge_plan"], "paid_options": profile["paid_model"],
            **profile["live_text_window"],
        }) as values:
            _, _, calls, sends, capture, finish = values
            for _ in texts:
                await capture()
                await finish()
            assert len(calls) == len(sends) == 6
            final = calls[-1]["messages"]
            assert len(final) == 12
            assert [message["content"] for message in final if message["role"] == "user"] == texts

    run(scenario())
