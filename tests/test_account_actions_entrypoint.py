"""Real Core HTTP, independent planning Store and original account delivery lifecycle."""

import json

import httpx
import pytest

from companion import paid_budget
from companion.core import Core
from companion.model import ModelClient
from companion.retrieval import SemanticIndex
from companion.social_autonomy_protocol import FrozenPlan, SocialAutonomyConfig
from companion.social_autonomy_runtime import run_once as social_tick
from companion.store import encode
from tests.conftest import ingest, run
from tests.test_onebot_capture import pinned_plan, real_core_http
from tests.test_private_companion import companion_options
from tests.test_social_autonomy import read_public


@pytest.mark.parametrize("outcome", [
    "SUCCEEDED", "REJECTED", "UNKNOWN", "CANCELLED", "ERASED", "STOPPED",
    "AUTH_REVOKED", "EXPIRED",
])
def test_account_action_http_uses_distinct_scope_native_budget_and_original_outbox(
    core,
    tmp_path,
    monkeypatch,
    outcome,
):
    monkeypatch.setattr(SemanticIndex, "warmup", lambda self: None)
    ref = read_public(core, "bilibili")
    owner = ingest(core, 99, "用于绑定的合成输入", origin="onebot-direct:FriendMessage:10001")
    conversation = core.store.one(
        "SELECT conversation_id FROM events WHERE event_id=?", (owner["event_id"],)
    )["conversation_id"]
    core.store.execute("UPDATE jobs SET state='SUCCEEDED'")
    policy = SocialAutonomyConfig(
        enabled=True, scope="qq_social_v1", authorization_sha256="a" * 64, qzone_enabled=True,
        max_output_tokens=128, plan_ttl_seconds=30,
    )
    calls = []

    def provider(request):
        calls.append(json.loads(request.content))
        assert "用于绑定的合成输入" not in request.content.decode()
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": encode(
                                {
                                    "kind": "qzone_publish",
                                    "text": "从公开阅读中认识恒星。",
                                    "source_refs": [ref],
                                }
                            )
                        }
                    }
                ],
                "usage": {"prompt_tokens": 100, "completion_tokens": 20},
            },
        )

    async def scenario():
        options = companion_options(core)
        options.update(
            account_actions_scope="qq_social_v1",
            account_actions_authorization_sha256=policy.authorization_sha256,
        )
        options["core_options"].update(social_autonomy=policy, content_enabled=False,
                                       observation_adapters={})
        async with real_core_http(core) as port:
            cfg, plan = pinned_plan(
                core,
                tmp_path,
                3001,
                core_port=port,
                live=True,
                approved=True,
                scope_options=options,
            )
            async with (
                httpx.AsyncClient(
                    base_url=cfg.core_url,
                    trust_env=False,
                    headers={"Authorization": "Bearer " + cfg.bridge_token},
                ) as client,
                httpx.AsyncClient(transport=httpx.MockTransport(provider)) as external,
            ):
                opened = await client.post(
                    "/internal/v1/live-text/open", json={"window_sha256": plan.sha256}
                )
                assert opened.status_code == 200, opened.text
                with Core(
                    core.settings, ModelClient(core.settings, external), core.clock
                ) as worker:
                    assert worker.store is not core.store
                    assert await worker.run_once("independent-social-worker")
                    planned = core.store.one("SELECT plan_json FROM social_autonomy_plans")
                    frozen = FrozenPlan.model_validate_json(planned["plan_json"])
                    assert core.store.one("SELECT conversation_id FROM outbox")[
                        "conversation_id"] == conversation
                    assert len(calls) == 1
                    assert core.store.one("SELECT scenario,state FROM model_usage") == {
                        "scenario": "social_autonomy",
                        "state": "SETTLED",
                    }
                    assert len(core.store.rows("SELECT * FROM cloud_cost_snapshots")) == 1
                    private = await client.post(
                        "/internal/v1/deliveries/lease", json={"worker_id": "private-only"}
                    )
                    assert private.json() == {"items": []}
                    request = {
                        "worker_id": "account-bridge",
                        "limit": 1,
                        "delivery_lane": "qq_account",
                        "window_sha256": plan.sha256,
                        "account_actions_authorization_sha256": policy.authorization_sha256,
                    }
                    denied = await client.post(
                        "/internal/v1/deliveries/lease",
                        json={**request, "account_actions_authorization_sha256": "0" * 64},
                    )
                    assert denied.status_code == 403
                    leased = await client.post("/internal/v1/deliveries/lease", json=request)
                    assert leased.status_code == 200, leased.text
                    item = leased.json()["items"][0]
                    assert item["message_id"] == frozen.message_id
                    assert item["content"]["audience"] == {
                        "ugc_right": 16,
                        "target_uins": [cfg.owner_qq_id],
                    }
                    path = "/internal/v1/deliveries/" + item["message_id"]
                    begun = await client.post(
                        path + "/begin-send", json={"lease_token": item["lease_token"]}
                    )
                    assert begun.status_code == 200, begun.text
                    check = {
                        "window_sha256": plan.sha256,
                        "message_id": frozen.message_id,
                        "lease_token": item["lease_token"],
                        "content_sha256": item["content_sha256"],
                        "payload_sha256": frozen.payload_sha256,
                    }
                    if outcome in {"CANCELLED", "ERASED", "STOPPED", "AUTH_REVOKED", "EXPIRED"}:
                        if outcome == "ERASED":
                            with worker.store.transaction():
                                worker.ledger.expire_events(worker.store, [ref], worker.clock())
                        elif outcome == "STOPPED":
                            worker.store.execute(
                                "INSERT OR REPLACE INTO runtime_meta VALUES ('life_paused','1')"
                            )
                        elif outcome == "AUTH_REVOKED":
                            core.settings.social_autonomy = policy.model_copy(
                                update={"enabled": False}
                            )
                        elif outcome == "EXPIRED":
                            core.clock.advance(31)
                        if outcome != "CANCELLED":
                            refused = await client.post(
                                "/internal/v1/account-actions/send-check", json=check
                            )
                            expected_error = {
                                "ERASED": (503, "LIVE_TEXT_RECONCILIATION_REQUIRED"),
                                "STOPPED": (409, "SOCIAL_AUTONOMY_PAUSED"),
                                "AUTH_REVOKED": (503, "LIVE_TEXT_RUNTIME_CHANGED"),
                                "EXPIRED": (403, "SOCIAL_PLAN_AUTHORITY_CHANGED"),
                            }[outcome]
                            assert (
                                refused.status_code, refused.json().get("error")
                            ) == expected_error
                            assert core.store.one(
                                "SELECT dispatched_at FROM social_autonomy_reservations"
                            ) == {"dispatched_at": None}
                        cancelled = await client.post(
                            "/internal/v1/account-actions/cancel-before-send",
                            json={**check, "reason": "LOCAL_PREPARATION_FAILED"},
                        )
                        assert cancelled.status_code == 200, cancelled.text
                        assert cancelled.json()["state"] == "NOT_SENT"
                        assert (
                            await client.post(
                                "/internal/v1/account-actions/cancel-before-send",
                                json={**check, "reason": "LOCAL_PREPARATION_FAILED"},
                            )
                        ).json() == cancelled.json()
                        expected = "CANCELLED"
                    else:
                        checked = await client.post(
                            "/internal/v1/account-actions/send-check", json=check
                        )
                        assert checked.status_code == 200, checked.text
                        again = await client.post(
                            "/internal/v1/account-actions/send-check", json=check
                        )
                        assert again.status_code == 409
                        receipt = {
                            "message_id": frozen.message_id,
                            "lease_token": item["lease_token"],
                            "payload_sha256": frozen.payload_sha256,
                            "action_type": "qzone.publish",
                            "result": outcome,
                            "native_retcode": {"SUCCEEDED": 0, "REJECTED": 100}.get(outcome),
                            "native_receipt_sha256": paid_budget.sha({"tid": "test-tid"})
                            if outcome != "UNKNOWN"
                            else None,
                            "resource_id": "test-tid" if outcome == "SUCCEEDED" else None,
                        }
                        expected = {"SUCCEEDED": "SENT", "REJECTED": "FAILED",
                                    "UNKNOWN": "DELIVERY_UNCERTAIN"}[outcome]
                        body = {
                            "lease_token": item["lease_token"],
                            "result": "PERMANENT_FAILURE" if outcome == "REJECTED" else expected,
                            "account_action_receipt": receipt,
                        }
                        sent = await client.post(path + "/result", json=body)
                        assert sent.status_code == 200, sent.text
                        assert sent.json() == {"state": expected}
                        assert (
                            await client.post(path + "/result", json=body)
                        ).json() == sent.json()
                        if outcome == "REJECTED":
                            before = core.store.db.total_changes
                            conflict = await client.post(path + "/result", json={**body,
                                "account_action_receipt": {**receipt,
                                    "native_receipt_sha256": "e" * 64}})
                            assert conflict.status_code == 409
                            assert core.store.db.total_changes == before
                        stored = core.store.one(
                            "SELECT receipt_json FROM social_autonomy_reservations"
                        )
                        assert (
                            json.loads(stored["receipt_json"])["resource_id"]
                            == receipt["resource_id"]
                        )
                    row = core.store.one(
                        "SELECT state,attempt_count,provider_message_id,native_ack_json FROM outbox"
                    )
                    assert row == {
                        "state": expected,
                        "attempt_count": 1,
                        "provider_message_id": None,
                        "native_ack_json": None,
                    }
                    assert len(calls) == 1
                    usage = core.store.rows("SELECT * FROM model_usage")
                # Reopen the independent worker against persisted state. A completed or
                # uncertain send must never become a replacement plan or a fresh lease.
                with Core(
                    core.settings, ModelClient(core.settings, external), core.clock
                ) as restarted:
                    assert await social_tick(
                        restarted.store, restarted.settings, restarted.model, restarted.clock
                    ) is False
                    assert core.store.rows("SELECT * FROM model_usage") == usage
                    assert core.store.one(
                        "SELECT state,attempt_count,provider_message_id,native_ack_json FROM outbox"
                    ) == row
                if outcome in {"SUCCEEDED", "REJECTED", "UNKNOWN", "CANCELLED", "ERASED"}:
                    duplicate = await client.post("/internal/v1/deliveries/lease", json=request)
                    if outcome == "UNKNOWN":
                        assert duplicate.status_code == 503
                        assert duplicate.json() == {"error": "LIVE_TEXT_RECONCILIATION_REQUIRED"}
                    else:
                        assert duplicate.status_code == 200, duplicate.text
                        assert duplicate.json() == {"items": []}
                assert len(calls) == 1

    run(scenario())
