"""Real HTTP, separate Core worker and stopped Bridge process; no platform replay."""

import asyncio
import json
from dataclasses import asdict, replace
from pathlib import Path

import httpx
import pytest
from pydantic import SecretStr

from companion import paid_budget
from companion.transport import recover_deliveries
from companion_onebot_bridge import owner_handoff, owner_native_resolution
from companion_onebot_bridge.event_normalizer import normalize
from companion_onebot_bridge.owner_runtime import OwnerRuntime
from companion_onebot_bridge.owner_send import OwnerSendGuard
from companion_onebot_bridge.spool import Spool, encode
from tests.test_bridge_owner_resolution import child
from tests.test_evaluation_process import process_config
from tests.test_onebot_direct import raw_event
from tests.test_reading_followup import window


@pytest.mark.parametrize("lost_ack", [False, True])
def test_reserved_before_begin_send_closes_and_handoffs_without_owner_guess(
    core, tmp_path, monkeypatch, lost_ack,
):
    core.settings.control_token = SecretStr("synthetic-owner-control-token")
    owner = {"Authorization": "Bearer synthetic-owner-control-token"}

    async def scenario():
        async with window(core, tmp_path, monkeypatch) as (client, base, plan):
            config = replace(base, owner_authority=plan.authority,
                capture_signing_key=plan.key.private_bytes_raw().hex(),
                data_dir=str(tmp_path / "native-bridge"), core_url=str(client.base_url).rstrip("/"))
            with Spool(config) as spool:
                runtime = OwnerRuntime(spool, clock=lambda: core.clock.now)
                raw = encode(raw_event(1, time=int(core.clock.now.timestamp()),
                                       text="未开始发送的合成输入"))
                event = normalize(raw, config, now=core.clock.now.timestamp())
                runtime.persist_capture(event, raw, received_at=core.clock.now,
                                        peer=("127.0.0.1", 1))
                assert await runtime.flush_one(client)
                worker_config = tmp_path / "worker.json"
                process_config(core, worker_config)
                exported = json.loads(worker_config.read_text("utf-8"))
                for name, adapter in core.settings.observation_adapters.items():
                    exported["observation_adapters"][name]["token"] = (
                        adapter.token.get_secret_value())
                worker_config.write_text(json.dumps(exported), "utf-8")
                assert await child("tests.owner_runtime_process", "--config", str(worker_config),
                                   "--now", core.clock()) == {"worked": True, "calls": 1}
                leased = await client.post("/internal/v1/deliveries/lease", json={
                    "worker_id": "bridge", "authority": plan.authority, "limit": 1})
                assert leased.status_code == 200, leased.text
                item = leased.json()["items"][0]
                OwnerSendGuard(runtime, client).reserve(item)
                assert not spool.db.execute("SELECT 1 FROM results").fetchone()
            # The stopped process lost its in-memory item. Core's normal lease
            # expiry has already removed the original plaintext token.
            core.clock.advance(core.settings.lease_seconds + 1)
            recover_deliveries(core.store, core.clock())
            assert core.store.one("SELECT state,attempt_count,lease_token FROM outbox "
                "WHERE message_id=?", (item["message_id"],)) == {
                    "state": "PENDING", "attempt_count": 0, "lease_token": None}
            stopped = await client.post("/internal/v1/owner-authority/stop", headers=owner,
                json={"authority": plan.authority, "operation_id": "stop-reserved-process"})
            assert stopped.status_code == 200, stopped.text
            usages = core.store.rows("SELECT * FROM model_usage")
            budget_path = Path(core.settings.paid_model.journal_path)
            budget = await asyncio.to_thread(budget_path.read_bytes)
            if lost_ack:
                original_post = owner_native_resolution.bounded_post

                async def lose_response(client, path, body):
                    result = await original_post(client, path, body)
                    if path == "/internal/v1/deliveries/reconciliation/prepare":
                        raise httpx.ReadTimeout("synthetic Core response lost")
                    return result

                with Spool(config, reconciliation_only=True) as spool:
                    runtime = OwnerRuntime(spool, clock=lambda: core.clock.now)
                    with monkeypatch.context() as patch:
                        patch.setattr(owner_native_resolution, "bounded_post", lose_response)
                        with pytest.raises(httpx.ReadTimeout):
                            await owner_native_resolution.prepare(runtime, client,
                                                                  item["message_id"])
            bridge_config = tmp_path / "bridge.json"
            bridge_config.write_text(json.dumps(asdict(config)), "utf-8")
            core.clock.advance(1)
            args = ("--config", str(bridge_config), "--now", core.clock(),
                    "--message-id", item["message_id"])
            prepared = await child("tests.owner_reconcile_process", *args, "--operation", "prepare")
            assert prepared["state"] == "NOT_SENT" and prepared["paused"]
            assert await child("tests.owner_reconcile_process", *args,
                               "--operation", "prepare") == prepared
            assert await child("tests.owner_reconcile_process", *args,
                               "--operation", "reconcile") == prepared
            assert core.store.rows("SELECT * FROM model_usage") == usages
            assert await asyncio.to_thread(budget_path.read_bytes) == budget
            assert not core.store.one("SELECT 1 FROM console_operations")
            with Spool(config, reconciliation_only=True) as spool:
                runtime = OwnerRuntime(spool, clock=lambda: core.clock.now)
                records = runtime.verify(active=False)
                assert records[-1]["kind"] == "NOT_SENT"
                body, state = spool.db.execute("SELECT body,state FROM results").fetchone()
                assert state == "accepted" and json.loads(body)["result"] == "NOT_SENT"
                assert "lease_token" not in json.loads(body)
                assert not await spool.flush_result(client)
                assert (await owner_handoff.prepare(runtime, client))["state"] == "HANDOFF_PREPARED"
                value = {"expected_revision": 2, "policy": plan.policy.model_dump(mode="json")}
                preview = await client.post("/internal/v1/owner-authority/preview",
                                            json=value, headers=owner)
                assert preview.status_code == 200, preview.text
                activated = await client.post("/internal/v1/owner-authority/activate",
                    headers=owner,
                    json=value | {"operation_id": "reopen-after-not-sent",
                                  "preview_sha256": preview.json()["preview_sha256"]})
                assert activated.status_code == 200, activated.text
                successor = activated.json()["authority"]
                result = await owner_handoff.handoff(runtime, client, successor)
                assert result["state"] == "ACTIVE"
            with Spool(replace(config, owner_authority=successor)) as spool:
                runtime = OwnerRuntime(spool, clock=lambda: core.clock.now)
                runtime.verify()
                assert not await runtime.flush_one(client)
                assert not await spool.flush_result(client)
                assert "未开始发送的合成输入" not in runtime.path.read_text("utf-8")
            assert not core.store.one("SELECT 1 FROM outbox WHERE state='SENT'")
            assert not core.store.one("SELECT 1 FROM audit_log WHERE action='delivery.result'")
            paid_budget.journal(core.store, core.settings.paid_model, allow_settlement=True)
    asyncio.run(scenario())
