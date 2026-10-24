"""Real HTTP acceptance, process-restarted native wording and synthetic delivery ACK."""

import asyncio
import json
import os
import sys
from datetime import timedelta
from pathlib import Path

import httpx
import pytest
from pydantic import SecretStr

from companion.store import encode
from tests.conftest import event, run
from tests.test_onebot_capture import real_core_http
from tests.test_paid_model_gate import approved_live


@pytest.mark.parametrize("first_text,delivery", [("[SKIP]", "SENT"), (" ", "DELIVERY_UNCERTAIN")])
def test_required_wording_retries_in_new_process_without_replaying_paid_request(
    core,
    tmp_path,
    first_text,
    delivery,
):
    core.settings.goals_enabled = core.settings.exploration_enabled = True
    core.settings.control_token = SecretStr("synthetic-notice-owner-" * 3)
    approved_live(core, tmp_path)
    config = core.settings.model_dump(mode="json")
    config.update(
        model_key=core.settings.model_key.get_secret_value(),
        bridge_token=core.settings.bridge_token.get_secret_value(),
        control_token=core.settings.control_token.get_secret_value(),
    )
    settings_path = tmp_path / "worker-settings.json"
    settings_path.write_text(encode(config), encoding="utf-8")
    workspace = Path(__file__).resolve().parents[1]

    async def worker(text):
        script = (
            "import sys; from tests.support.required_notice_process import run_worker; "
            "run_worker(sys.argv[1], sys.argv[2], sys.argv[3])"
        )
        child = await asyncio.create_subprocess_exec(
            sys.executable,
            "-B",
            "-c",
            script,
            str(settings_path),
            core.clock(),
            text,
            cwd=workspace,
            env={**os.environ, "HF_HUB_OFFLINE": "1", "PYTHONDONTWRITEBYTECODE": "1"},
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(child.communicate(), 20)
            assert child.returncode == 0, stderr.decode("utf-8", errors="replace")
            result = json.loads(stdout.decode("utf-8").splitlines()[-1])
            assert result["pid"] != os.getpid()
            assert child.pid in {result["pid"], result["parent_pid"]}
            return result["calls"]
        finally:
            if child.returncode is None:
                child.kill()
                await child.communicate()

    async def scenario():
        async with (
            real_core_http(core) as port,
            httpx.AsyncClient(
                base_url=f"http://127.0.0.1:{port}",
                trust_env=False,
                headers={
                    "Authorization": "Bearer " + core.settings.bridge_token.get_secret_value()
                },
            ) as client,
        ):
            accepted = await client.post(
                "/internal/v1/inbox/events",
                json=event(core, 1, "请稍后读这篇资料").model_dump(mode="json"),
                headers={"X-Idempotency-Key": "required-notice-source"},
            )
            assert accepted.status_code == 202, accepted.text
            core.store.execute(
                "UPDATE jobs SET state='CANCELLED',"
                "last_error_code='SYNTHETIC_BACKGROUND_ONLY' WHERE kind='chat'"
            )
            commitment = await client.post(
                "/internal/v2/commitments",
                json={
                    "proposal": {
                        "topic": "阅读资料",
                        "source_event_ids": ["evt_test_1"],
                        "scope": {"operation": "read_content", "url": "https://example.org/a"},
                    },
                    "accepted_event_id": "evt_test_1",
                    "deadline": (core.clock.now + timedelta(seconds=60)).isoformat(),
                },
                headers={
                    "Authorization": "Bearer " + core.settings.control_token.get_secret_value()
                },
            )
            assert commitment.status_code == 202, commitment.text
            core.clock.advance(61)
            assert core.goals.claim() is None
            job = core.store.one("SELECT * FROM jobs WHERE kind='activity'")
            assert await worker(first_text) == ["EXPLAINED_FAILURE"]
            first = core.store.one("SELECT * FROM model_usage")
            assert first["state"] == "SETTLED" and first["billed_micro_usd"] > 0
            assert not core.store.rows("SELECT * FROM outbox")
            assert await worker("还未到下一次尝试时间") == []
            core.clock.advance(6)
            assert await worker("这次没有在约定时间内完成阅读。") == ["EXPLAINED_FAILURE"]
            usages = core.store.rows("SELECT * FROM model_usage ORDER BY rowid")
            assert len(usages) == 2 and usages[0] == first
            assert all(row["state"] == "SETTLED" and row["billed_micro_usd"] > 0 for row in usages)
            admissions = core.store.rows("SELECT * FROM model_admissions ORDER BY rowid")
            assert len({row["request_id"] for row in admissions}) == 2
            assert {row["logical_request_id"] for row in admissions} == {job["job_id"]}
            assert [row["attempt_number"] for row in admissions] == [1, 2]
            assert core.store.one("SELECT state FROM commitments")["state"] == "IN_PROGRESS"
            lease = await client.post(
                "/internal/v1/deliveries/lease", json={"worker_id": "synthetic"}
            )
            assert lease.status_code == 200, lease.text
            (item,) = lease.json()["items"]
            path = "/internal/v1/deliveries/" + item["message_id"]
            begun = await client.post(
                path + "/begin-send", json={"lease_token": item["lease_token"]}
            )
            assert begun.status_code == 200, begun.text
            acknowledged = await client.post(
                path + "/result",
                json={
                    "lease_token": item["lease_token"],
                    "result": delivery,
                },
            )
            assert acknowledged.status_code == 200, acknowledged.text
            state = core.store.one("SELECT state FROM commitments")["state"]
            assert state == ("EXPLAINED_FAILURE" if delivery == "SENT" else "IN_PROGRESS")
            assert await worker("不得再次产生通知") == []
            assert core.store.rows("SELECT * FROM model_usage ORDER BY rowid") == usages
            assert core.store.one("SELECT COUNT(*) n FROM outbox")["n"] == 1

    run(scenario())
