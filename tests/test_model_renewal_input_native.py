"""Closed current input uses the original CLI/HTTP/Bridge handoff and Life worker."""

import hashlib
import json
from dataclasses import replace
from datetime import UTC, datetime

import httpx

from companion import owner_authority
from companion.store import encode
from companion_onebot_bridge.capture import prepare_owner_input
from companion_onebot_bridge.config import BridgeConfig
from companion_onebot_bridge.owner_runtime import OwnerRuntime
from companion_onebot_bridge.spool import Spool
from tests.conftest import run
from tests.test_life_worker_integration import process_worker
from tests.test_model_carry_native import child, finished
from tests.test_model_renewal_native import life_case, source_and_renewal
from tests.test_onebot_capture import real_core_http
from tests.test_onebot_direct import raw_event


def test_closed_unknown_renewal_native_input_handoff_and_private_life(core, tmp_path, monkeypatch):
    core.clock.now = datetime.now(UTC).replace(microsecond=0)
    case = life_case(core, tmp_path, monkeypatch)

    async def scenario():
        async with real_core_http(core) as port, httpx.AsyncClient(
            base_url=f"http://127.0.0.1:{port}", trust_env=False,
            headers={"Authorization": "Bearer " + core.settings.bridge_token.get_secret_value()},
        ) as http:
            config = BridgeConfig(
                owner_qq_id=core.settings.owner_qq_id, bot_qq_id=core.settings.bot_qq_id,
                bridge_token=core.settings.bridge_token.get_secret_value(), onebot_token="s" * 32,
                protocol_epoch=case.policy.capture.protocol_epoch,
                capture_signing_key=case.key.private_bytes_raw().hex(),
                owner_authority=case.successor["authority"],
                core_url=str(http.base_url).rstrip("/"),
                data_dir=str(tmp_path / "bridge"),
            )
            with Spool(config) as spool:
                runtime = OwnerRuntime(spool, clock=lambda: core.clock.now)
                frame = encode(raw_event(40, time=int(core.clock.now.timestamp()),
                                         text="synthetic current uncertain input"))
                value = await prepare_owner_input(runtime, http, None, frame, core.clock.now,
                                                   ("127.0.0.1", 3001))
                assert runtime.persist_capture(value["event"], frame, received_at=core.clock.now,
                                               peer=("127.0.0.1", 3001))
                assert await runtime.flush_one(http)
                event_id = value["event"]["event_id"]
            output = tmp_path / "current-provider.json"
            worker = await child(tmp_path, "current-timeout", core, mode="worker-timeout",
                                 output=str(output))
            await finished(worker)
            assert len(json.loads(output.read_text())) == 1
            current = core.store.one(
                "SELECT m.* FROM model_usage m JOIN jobs j USING(job_id) WHERE j.event_id=?",
                (event_id,),
            )
            assert current["state"] == "UNKNOWN"
            with core.store.transaction():
                view = owner_authority.custody(core.store, core.settings, settlement=True)
            assert view["model_controls"][view["model_closures"][current["request_id"]]][
                "kind"] == "model_local_close"
            historical = core.store.rows("SELECT * FROM model_usage ORDER BY request_id")
            assert len(historical) == 2 and all(row["state"] == "UNKNOWN" for row in historical)
            source, request, policy, witness = await source_and_renewal(
                core, tmp_path, case, http,
            )
            with Spool(config) as spool:
                OwnerRuntime(spool, clock=lambda: core.clock.now).stop()
            raw = encode(request).encode()
            path = tmp_path / "renewal.json"
            path.write_bytes(raw)
            controller = await child(
                tmp_path, "renewal-controller", core, mode="controller",
                command="carry-closed-model-attempts", executors=witness.value,
                request=str(path), request_sha256=hashlib.sha256(raw).hexdigest(),
            )
            summary = json.loads(await finished(controller))
            assert summary["kind"] == "owner_model_approval_renewal_v1"
            assert summary["retained_micro_usd"] == sum(r["billed_micro_usd"] for r in historical)
            query = {"authority": config.owner_authority, "carry_sha256": summary["carry_sha256"],
                     "successor_policy_sha256": summary["successor_policy_sha256"]}
            response = await http.post("/internal/v1/owner-authority/model-carry-receipt",
                                       json=query)
            assert response.status_code == 200, response.text
            proof = response.json()
            assert proof["kind"] == "owner_model_unknown_carry_v1" and proof["version"] == 1
            assert proof["request_ids"] == [current["request_id"]]
            assert proof["retained_micro_usd"] == current["billed_micro_usd"]

            async def bridge(name, cfg, arguments):
                process = await child(tmp_path, name, core, mode="bridge", config=cfg,
                                      arguments=arguments, freeze_bridge_clock=True)
                return json.loads(await finished(process))

            prepared = await bridge("carry-prepare", config, [
                "owner-carry-handoff-prepare", "--carry-sha256", summary["carry_sha256"],
                "--successor-policy-sha256", summary["successor_policy_sha256"],
            ])
            assert prepared["state"] == "CARRY_HANDOFF_PREPARED"
            control = {"Authorization": "Bearer " + core.settings.control_token.get_secret_value()}
            body = {"expected_revision": 4, "policy": policy.model_dump(mode="json")}
            preview = await http.post("/internal/v1/owner-authority/preview", headers=control,
                                      json=body)
            assert preview.status_code == 200, preview.text
            activated = await http.post("/internal/v1/owner-authority/activate", headers=control,
                json={**body, "operation_id": "renew-current-input",
                      "preview_sha256": preview.json()["preview_sha256"]})
            assert activated.status_code == 200, activated.text
            newer = replace(config, owner_authority=activated.json()["authority"])
            assert (await bridge("handoff", newer, ["owner-handoff"]))["state"] == "ACTIVE"
            with Spool(newer) as spool:
                runtime = OwnerRuntime(spool, clock=lambda: core.clock.now)
                records = runtime.verify()
                carried = [r["data"] for r in records if r["kind"] == "OWNER_INPUT_CARRIED"]
                assert len(carried) == 1 and carried[0]["event_id"] == event_id
                assert carried[0]["carry_receipt"] == proof
                assert not carried[0]["input_state"]["terminal"]
                assert spool.db.execute("SELECT state,body FROM inbox").fetchone() == (
                    "accepted", None)
                assert not await runtime.flush_one(http)
                old_capture = json.loads(spool.db.execute(
                    "SELECT capture_json FROM window_inputs WHERE event_id=?", (event_id,),
                ).fetchone()[0])
            denied = await http.post("/internal/v1/owner-authority/inputs/approve",
                                     json=old_capture)
            assert denied.status_code >= 400
            assert core.store.rows("SELECT * FROM model_usage ORDER BY request_id") == historical
            assert (await http.post("/internal/v1/owner-authority/model-carry-receipt",
                                    json=query)).json() == proof
            first = await process_worker(core, tmp_path, "bug", source)
            assert first["provider_calls"] == 1 and first["results"][0]["state"] == "SHADOW", first
            again = await process_worker(core, tmp_path, "bug", source, restart=True)
            assert again["provider_calls"] == 0
            assert len(core.store.rows(
                "SELECT * FROM outbox WHERE action_type='qq.private.proactive'")) == 1
            assert all(event_id not in encode(item) for item in first["materials"])
            for row in historical:
                assert core.store.one("SELECT * FROM model_usage WHERE request_id=?",
                                       (row["request_id"],)) == row

    run(scenario())
