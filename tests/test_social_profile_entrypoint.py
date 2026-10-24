"""Real profile API and independent native Core process enforce profile-only sources."""

import asyncio
import json
import os
import subprocess
import sys

import httpx
from pydantic import SecretStr

from tests.conftest import run
from tests.social_profile_support import confirmed_profile_material
from tests.test_onebot_capture import pinned_plan, real_core_http
from tests.test_private_companion import companion_options
from tests.test_social_autonomy import background


def test_real_api_independent_profile_worker_excludes_public_and_rechecks_forget(core, tmp_path):
    module, ref, _ = background(core, nickname_enabled=True, qzone_enabled=False,
                                max_output_tokens=128)
    memory_id = confirmed_profile_material(core)

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
                baseline = await api.post("/internal/v1/account-actions/profile-baselines", json={
                    "window_sha256": window.sha256,
                    "authorization_sha256": module.config.authorization_sha256,
                    "profiles": [{"bot_qq_id": cfg.bot_qq_id, "field": "nickname",
                                  "value": "External baseline instruction",
                                  "observed_at": core.clock(),
                                  "receipt_sha256": "b" * 64}],
                })
                assert baseline.status_code == 200, baseline.text
                config, result = tmp_path / "profile-child.json", tmp_path / "profile-result.json"
                config.write_text(json.dumps({
                    "settings": core.settings.model_dump(mode="python"), "source_id": ref,
                    "now": core.clock(), "mode": "core_profile", "result_path": str(result),
                }, default=lambda value: value.get_secret_value()
                   if isinstance(value, SecretStr) else str(value)), encoding="utf-8")
                child = await asyncio.to_thread(
                    subprocess.run,
                    [sys.executable, "-B", "-m", "tests.social_recovery_process", str(config)],
                    capture_output=True, timeout=30,
                )
                assert child.returncode == 0, child.stderr.decode(errors="replace")
                outcome = json.loads(result.read_text(encoding="utf-8"))
                assert outcome["pid"] != os.getpid()
                assert outcome["worked"] and outcome["idle"] and outcome["provider_calls"] == 1
                payload = json.loads(outcome["payloads"][0][-1]["content"])
                assert payload["purpose"] == "owner_confirmed_profile"
                assert "sources" not in payload and "current_profile" not in payload
                assert payload["owner_confirmed_material"] == [{
                    "subject": "user", "kind": "preference", "content": "我偏爱星空与简洁的表达。",
                }]
                assert "External baseline instruction" not in json.dumps(payload)
                assert core.store.one("SELECT state FROM model_usage")["state"] == "SETTLED"
                leased = await api.post("/internal/v1/deliveries/lease", json={
                    "worker_id": "synthetic-profile", "limit": 1, "delivery_lane": "qq_account",
                    "window_sha256": window.sha256,
                    "account_actions_authorization_sha256": module.config.authorization_sha256,
                })
                assert leased.status_code == 200, leased.text
                item = leased.json()["items"][0]
                assert item["action_type"] == "qq.profile.nickname"
                with core.store.transaction():
                    core.memory.decide(memory_id, "forget", core.clock())
                denied = await api.post(
                    "/internal/v1/deliveries/" + item["message_id"] + "/begin-send",
                    json={"lease_token": item["lease_token"]},
                )
                assert denied.status_code >= 400, denied.text
                assert core.store.one("SELECT state,attempt_count FROM outbox") == {
                    "state": "CANCELLED", "attempt_count": 0}
                assert core.store.one("SELECT state FROM model_usage")["state"] == "SETTLED"
    run(scenario())
