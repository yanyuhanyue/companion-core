"""Real HTTP window and delivery with two independent Core worker subprocesses."""

import asyncio
import json
import os
import subprocess
import sys

import httpx
from pydantic import SecretStr

from tests.conftest import run
from tests.test_onebot_capture import pinned_plan, real_core_http
from tests.test_private_companion import companion_options
from tests.test_social_autonomy import background


def test_http_window_independent_worker_resumes_same_unadmitted_social_job(core, tmp_path):
    module, ref, _ = background(core, max_output_tokens=128)

    async def child(mode):
        config, result = tmp_path / (mode + ".json"), tmp_path / (mode + "-result.json")
        config.write_text(json.dumps({
            "settings": core.settings.model_dump(mode="python"), "source_id": ref,
            "now": core.clock(), "mode": mode, "result_path": str(result),
        }, default=lambda value: value.get_secret_value()
           if isinstance(value, SecretStr) else str(value)), encoding="utf-8")
        process = await asyncio.to_thread(
            subprocess.run,
            [sys.executable, "-B", "-m", "tests.social_recovery_process", str(config)],
            capture_output=True, timeout=30,
        )
        assert process.returncode == 0, process.stderr.decode(errors="replace")
        outcome = json.loads(result.read_text(encoding="utf-8"))
        assert outcome["pid"] != os.getpid()
        assert outcome["worked"] and outcome["idle"]
        return outcome

    async def scenario():
        options = companion_options(core)
        options.update(account_actions_scope="qq_social_v1",
                       account_actions_authorization_sha256=module.config.authorization_sha256)
        options["core_options"].update(social_autonomy=module.config, content_enabled=False,
                                       observation_adapters={})
        async with real_core_http(core) as port:
            cfg, window = pinned_plan(core, tmp_path, 3001, core_port=port, live=True,
                                      approved=True, scope_options=options)
            async with httpx.AsyncClient(
                base_url=cfg.core_url, trust_env=False,
                headers={"Authorization": "Bearer " + cfg.bridge_token},
            ) as api:
                opened = await api.post("/internal/v1/live-text/open",
                                        json={"window_sha256": window.sha256})
                assert opened.status_code == 200, opened.text
                first = await child("core_defer")
                before = core.store.one("SELECT * FROM social_autonomy_plans")
                job = core.store.one("SELECT * FROM jobs WHERE kind='social_plan'")
                assert before["state"] == "REJECTED" and job["state"] == "QUEUED"
                assert first["provider_calls"] == 0
                assert not core.store.rows("SELECT * FROM model_usage")
                core.clock.advance(60)
                second = await child("core_resume")
                assert second["pid"] != first["pid"]
                after = core.store.one("SELECT * FROM social_autonomy_plans")
                for key in ("plan_id", "model_request_id", "expires_at", "candidate_sha256"):
                    assert after[key] == before[key]
                assert after["state"] == "READY"
                assert core.store.one("SELECT state FROM jobs WHERE job_id=?",
                                      (job["job_id"],))["state"] == "SUCCEEDED"
                assert core.store.one("SELECT job_id,state FROM model_usage") == {
                    "job_id": job["job_id"], "state": "SETTLED"}
                assert second["provider_calls"] == 1
                assert len(core.store.rows("SELECT * FROM cloud_cost_snapshots")) == 1
                leased = await api.post("/internal/v1/deliveries/lease", json={
                    "worker_id": "synthetic-bridge", "limit": 1, "delivery_lane": "qq_account",
                    "window_sha256": window.sha256,
                    "account_actions_authorization_sha256": module.config.authorization_sha256,
                })
                assert leased.status_code == 200, leased.text
                assert len(leased.json()["items"]) == 1
                assert leased.json()["items"][0]["message_id"] == core.store.one(
                    "SELECT message_id FROM outbox")["message_id"]
    run(scenario())
