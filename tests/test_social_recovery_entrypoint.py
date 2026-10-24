"""Native social deferral through real HTTP and a separate Core worker."""

import httpx

from companion import cloud_budget
from companion.contracts import CoreError
from companion.core import Core
from companion.model import ModelClient
from companion.retrieval import SemanticIndex
from companion.store import encode
from tests.conftest import run
from tests.test_onebot_capture import pinned_plan, real_core_http
from tests.test_private_companion import companion_options
from tests.test_social_autonomy import background


def test_http_window_independent_worker_resumes_same_unadmitted_social_job(core, tmp_path,
                                                                         monkeypatch):
    monkeypatch.setattr(SemanticIndex, "warmup", lambda self: None)
    module, ref, _ = background(core, max_output_tokens=128)
    calls = []
    original_reserve = cloud_budget.reserve
    denied = [False]

    def reserve(*args, **kwargs):
        if not denied[0]:
            denied[0] = True
            raise CoreError("CLOUD_DAILY_BUDGET_EXHAUSTED", 429)
        return original_reserve(*args, **kwargs)

    monkeypatch.setattr(cloud_budget, "reserve", reserve)

    def provider(request):
        calls.append(request.content)
        return httpx.Response(200, json={
            "choices": [{"message": {"content": encode({
                "kind": "qzone_publish", "text": "A synthetic public observation.",
                "source_refs": [ref],
            })}}], "usage": {"prompt_tokens": 100, "completion_tokens": 20},
        })

    async def scenario():
        options = companion_options(core)
        options.update(account_actions_scope="qq_social_v1",
                       account_actions_authorization_sha256=module.config.authorization_sha256)
        options["core_options"].update(social_autonomy=module.config, content_enabled=False,
                                       observation_adapters={})
        async with real_core_http(core) as port:
            cfg, window = pinned_plan(core, tmp_path, 3001, core_port=port, live=True,
                                      approved=True, scope_options=options)
            async with (
                httpx.AsyncClient(base_url=cfg.core_url, trust_env=False,
                                  headers={"Authorization": "Bearer " + cfg.bridge_token}) as api,
                httpx.AsyncClient(transport=httpx.MockTransport(provider)) as external,
            ):
                opened = await api.post("/internal/v1/live-text/open",
                                        json={"window_sha256": window.sha256})
                assert opened.status_code == 200, opened.text
                with Core(
                    core.settings, ModelClient(core.settings, external), core.clock
                ) as worker:
                    assert worker.store is not core.store
                    assert await worker.run_once("social-defer")
                    before = core.store.one("SELECT * FROM social_autonomy_plans")
                    job = core.store.one("SELECT * FROM jobs WHERE kind='social_plan'")
                    assert before["state"] == "REJECTED" and job["state"] == "QUEUED"
                    assert not calls and not core.store.rows("SELECT * FROM model_usage")
                    assert not await worker.run_once("social-too-early")
                    core.clock.advance(60)
                    assert await worker.run_once("social-resume")
                    after = core.store.one("SELECT * FROM social_autonomy_plans")
                    for key in ("plan_id", "model_request_id", "expires_at", "candidate_sha256"):
                        assert after[key] == before[key]
                    assert after["state"] == "READY"
                    assert core.store.one("SELECT state FROM jobs WHERE job_id=?",
                                          (job["job_id"],))["state"] == "SUCCEEDED"
                    assert core.store.one("SELECT job_id,state FROM model_usage") == {
                        "job_id": job["job_id"], "state": "SETTLED"}
                    assert len(calls) == 1
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
