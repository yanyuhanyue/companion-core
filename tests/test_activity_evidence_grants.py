"""Non-null captured grants use the original owner registration and Reader protocol."""

import asyncio
import json
import sys
from contextlib import asynccontextmanager
from datetime import datetime
from types import SimpleNamespace

import httpx
import pytest

from companion.config import ObservationAdapterConfig
from companion.contracts import CoreError
from companion.privacy import advance_epoch
from companion.store import after, encode
from source_reader.accounts import source_grant
from tests.conftest import run
from tests.support.activity_evidence import (
    ROOT,
    TOKEN,
    admitted,
    journal_state,
    process,
    read_only,
    settings_json,
)
from tests.support.native_consumer_entry import environment
from tests.test_heybox_reader import POST, URL
from tests.test_plugin_readonly import grant


@asynccontextmanager
async def granted(core, tmp_path):
    options = dict(goals_enabled=True, exploration_enabled=True, observation_enabled=True,
        content_enabled=True, exploration_daily_requests=8, exploration_monthly_requests=30,
        exploration_daily_bytes=1048576, context_soft_tokens=12000,
        observation_adapters={"heybox": ObservationAdapterConfig(
            source="heybox", token=TOKEN, source_grant_required=True)})
    async with environment(core, tmp_path, purposes=("chat", "content", "goals", "exploration"),
        core_options=options, paid_options={"per_request_calls": 2, "window_calls": 5},
    ) as (entry, _):
        source = await entry.admit(1, "请阅读这篇帖子 " + URL)
        job_id = entry.accepted[source["event_id"]]["job_id"]
        assert (await process(tmp_path, "chat", {"settings": settings_json(core),
            "now": core.clock(), "job_id": job_id}))["calls"] == 1
        async with httpx.AsyncClient(base_url=str(entry.client.base_url), trust_env=False,
            headers={"Authorization": "Bearer " + core.settings.control_token.get_secret_value()},
        ) as control:
            authority = {"grant_id": "grant_" + "1" * 32, "epoch": 1, "fingerprint": "a" * 64}
            account = grant(tmp_path, now=core.clock.now, authorization=authority)
            captured = source_grant(account, core.clock.now, "heybox", required=True)
            registered = await control.post("/internal/v2/source-grants",
                json={**captured, "adapter_id": "heybox"})
            assert registered.status_code == 201, registered.text
            proposed = await control.post("/internal/v2/goals", json={"topic": "读取已批准的帖子",
                "source_event_ids": [source["event_id"]],
                "scope": {"operation": "read_content", "url": URL}})
            assert proposed.status_code == 202, proposed.text
            goal_id = proposed.json()["goal_id"]
            accepted = await control.post(f"/internal/v2/goals/{goal_id}/decision",
                                          json={"decision": "ACCEPT"})
            assert accepted.status_code == 200, accepted.text
            assert core.exploration.schedule_goal()
            config = tmp_path / "grant-reader.json"
            config.write_text(encode({"base": str(entry.client.base_url), "now": core.clock(),
                "account": str(account), "token": TOKEN, "body": encode(POST).encode().hex()}),
                "utf-8")
            child = await asyncio.create_subprocess_exec(sys.executable, "-B", "-m",
                "tests.activity_evidence_grant_process", str(config), cwd=ROOT,
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
            try:
                async with asyncio.timeout(45):
                    _, error = await child.communicate()
                assert child.returncode == 0, error.decode(errors="replace")
            finally:
                if child.returncode is None:
                    child.kill()
                    await child.wait()
            attempt = core.store.one("SELECT * FROM reading_attempts")
            assert attempt["state"] == "DONE", core.store.one(
                "SELECT state,last_error_code FROM exploration_requests")
            assert json.loads(attempt["source_grant_json"]) == captured
            core.clock.now = datetime.fromisoformat(after(attempt["lease_until"], 1))
            job = core.store.one("SELECT j.*,e.body_json,e.conversation_id,e.trace_id FROM jobs j "
                                "JOIN events e USING(event_id) WHERE job_id=?", (job_id,))
            step = core.store.one("SELECT * FROM goal_steps WHERE goal_id=?", (goal_id,))
            yield SimpleNamespace(core=core, job=job, goal_id=goal_id,
                admissions=admitted(core, job),
                journal_paths=tuple(journal_state(core)), captured=captured, control=control,
                attempt=attempt, step=step)


def test_actual_captured_source_grant_survives_done_lease_and_unrelated_epoch(core, tmp_path):
    async def scenario():
        async with granted(core, tmp_path) as case:
            result = read_only(case)
            assert result["execution"]["execution"]["state"] == "done"
            advance_epoch(core.store)
            assert read_only(case) == result

    run(scenario())


@pytest.mark.parametrize("drift", ["null", "revision", "observation_binding", "revoke"])
def test_original_valid_captured_grant_drift_is_rejected_before_body(
    core, tmp_path, monkeypatch, drift,
):
    async def scenario():
        async with granted(core, tmp_path) as case:
            if drift in {"null", "revision"}:
                value = None if drift == "null" else {**case.captured, "epoch": 2}
                core.store.execute("UPDATE reading_attempts SET source_grant_json=?",
                                   (encode(value),))
            elif drift == "observation_binding":
                core.store.execute("DELETE FROM source_grant_events WHERE event_id=?",
                                   (case.step["outcome_event_id"],))
            else:
                revoked = await case.control.post(
                    "/internal/v2/source-grants/" + case.captured["grant_id"] + "/revoke")
                assert revoked.status_code == 200, revoked.text
            one, bodies = core.store.one, []

            def guarded(sql, params=()):
                if "SELECT body_json,evidence_json FROM events JOIN observations" in sql:
                    bodies.append(True)
                return one(sql, params)

            monkeypatch.setattr(core.store, "one", guarded)
            with pytest.raises(CoreError):
                read_only(case)
            assert bodies == []

    run(scenario())
