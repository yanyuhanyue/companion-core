"""Authenticated native delivery results inform only bounded public-source decisions."""

import json

import httpx
import pytest

from companion.contracts import CoreError
from companion.social_autonomy import SocialAutonomy
from companion.store import encode
from tests.conftest import run
from tests.test_life_expression_consumption import prepared, request
from tests.test_onebot_capture import real_core_http
from tests.test_social_owner_authority import close_provider_clients as close_provider_clients


@pytest.mark.parametrize("outcome", ["SUCCEEDED", "REJECTED", "UNKNOWN"])
def test_http_receipt_informs_next_decision_or_blocks_uncertainty(core, tmp_path, outcome):
    job, _, authority, source = prepared(core, tmp_path)
    core.life_topics.consume(job, request(core, job, authority))
    row = core.store.one("SELECT * FROM outbox")
    content = json.loads(row["content_json"])
    module = SocialAutonomy(core.store, core.settings, core.model, core.clock)

    async def deliver():
        async with real_core_http(core) as port, httpx.AsyncClient(
            base_url=f"http://127.0.0.1:{port}", trust_env=False,
            headers={"Authorization": "Bearer " + core.settings.bridge_token.get_secret_value()},
        ) as client:
            leased = await client.post("/internal/v1/deliveries/lease", json={
                "worker_id": "public-outcome-test", "authority": authority,
                "delivery_lane": "qq_account",
                "account_actions_authorization_sha256": content["authorization_sha256"],
            })
            assert leased.status_code == 200, leased.text
            item = leased.json()["items"][0]
            path = "/internal/v1/deliveries/" + item["message_id"]
            begun = await client.post(path + "/begin-send",
                                      json={"lease_token": item["lease_token"]})
            assert begun.status_code == 200, begun.text
            checked = await client.post("/internal/v1/account-actions/send-check", json={
                "authority": authority, "message_id": item["message_id"],
                "lease_token": item["lease_token"], "content_sha256": item["content_sha256"],
                "payload_sha256": content["payload_sha256"],
            })
            assert checked.status_code == 200, checked.text
            native = {"SUCCEEDED": "SENT", "REJECTED": "PERMANENT_FAILURE",
                      "UNKNOWN": "DELIVERY_UNCERTAIN"}
            result = await client.post(path + "/result", json={
                "lease_token": item["lease_token"], "result": native[outcome],
                "account_action_receipt": {
                    "message_id": item["message_id"], "lease_token": item["lease_token"],
                    "payload_sha256": content["payload_sha256"], "action_type": "qzone.publish",
                    "result": outcome, "native_retcode": {
                        "SUCCEEDED": 0, "REJECTED": 1400, "UNKNOWN": None,
                    }[outcome],
                    "native_receipt_sha256": "e" * 64 if outcome != "UNKNOWN" else None,
                    "resource_id": "synthetic-public-tid" if outcome == "SUCCEEDED" else None,
                },
            })
            assert result.status_code == 200, result.text

    run(deliver())
    context = module.context([source])
    receipts = context["execution_outcomes"]
    assert len(receipts) == 1 and receipts[0]["result"] == outcome
    before = core.store.rows("SELECT * FROM model_usage")
    if outcome == "UNKNOWN":
        with pytest.raises(CoreError, match="SOCIAL_DELIVERY_RECONCILIATION_REQUIRED"):
            run(module.plan("after-public-unknown", source_ids=[source]))
        assert core.store.rows("SELECT * FROM model_usage") == before
    else:
        seen = []

        def provider():
            seen.append(json.loads(core.model.calls[-1][0][-1]["content"]))
            core.model.activity = encode({"kind": "skip", "reason_code": "NO_ACTION"})

        core.model.callback = provider
        result = run(module.plan("after-public-result", source_ids=[source]))
        assert result["state"] == "SKIPPED"
        assert seen[0]["execution_outcomes"][0]["result"] == outcome
        assert "evt_test_1" not in encode(seen)
        assert len(core.store.rows("SELECT * FROM model_usage")) == len(before) + 1
    assert len(core.store.rows("SELECT * FROM outbox")) == 1
