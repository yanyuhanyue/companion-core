"""STOP, real process exits, original journal carry, Bridge handoff and new-input ACK."""

import asyncio
import hashlib
import json
import pickle
import socket
import sys
from dataclasses import replace

import httpx
from pydantic import SecretStr

from companion import owner_authority, owner_input_state, paid_budget
from companion.owner_authority_protocol import OwnerCapture
from companion.store import encode
from companion_delivery_protocol import digest
from companion_onebot_bridge.capture import prepare_owner_input
from companion_onebot_bridge.network import private_http_client
from companion_onebot_bridge.outbound import deliver_tick
from companion_onebot_bridge.owner_runtime import OwnerRuntime
from companion_onebot_bridge.owner_send import OwnerOneBot, OwnerSendGuard
from companion_onebot_bridge.spool import Spool
from tests.conftest import run
from tests.model_carry_support import executors
from tests.support.onebot import provider
from tests.test_native_video import animation
from tests.test_onebot_direct import raw_event
from tests.test_prepare_owner_input import window
from tests.test_video_understanding import video_policy


async def child(tmp_path, name, core, **options):
    path = tmp_path / (name + ".pickle")
    bundle = {"settings": core.settings, "now": core.clock.now, **options}
    path.write_bytes(pickle.dumps(bundle))
    return await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        "tests.model_carry_native_host",
        str(path),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )


async def marker(process, expected):
    lines = []
    while True:
        line = await asyncio.wait_for(process.stdout.readline(), 30)
        assert line, b"".join(lines).decode(errors="replace")
        lines.append(line)
        if expected.encode() in line:
            return


async def finished(process):
    output, _ = await asyncio.wait_for(process.communicate(), 40)
    assert process.returncode == 0, output.decode(errors="replace")
    return output.decode().strip()


async def stopped(process):
    if process.returncode is None:
        process.terminate()
    await asyncio.wait_for(process.wait(), 10)
    assert process.returncode is not None


def test_native_video_unknown_carry_recovers_only_new_input_and_real_ack(
    core,
    tmp_path,
    monkeypatch,
):
    import tests.test_prepare_owner_input as preparation

    original_prepare = preparation.prepare

    def one_slot(*args, **kwargs):
        policy = original_prepare(*args, **kwargs)
        args[0].settings.paid_model = args[0].settings.paid_model.model_copy(
            update={"concurrency": 1}
        )
        return policy

    monkeypatch.setattr(preparation, "prepare", one_slot)
    core.settings.control_token = SecretStr("synthetic-native-carry-control")

    async def scenario():
        # Initial capture/upload is real authenticated HTTP over original synthetic pixels.
        # Honest frozen rates yield an 84 microUSD video reservation, matching
        # the recovery case without editing any usage or reservation projection.
        video = video_policy(input_rate=1, cached_input_rate=1, output_rate=650000)
        async with window(
            core, tmp_path, media=True, content=True, video=True, video_config=video
        ) as (
            client,
            config,
            plan,
        ):
            event = await animation(core, client, config, plan, tmp_path, "image/gif")
        config = replace(
            config,
            owner_authority=plan.authority,
            capture_signing_key=plan.key.private_bytes_raw().hex(),
            rich_input_scope="references_v1",
            data_dir=str(tmp_path / "native-bridge"),
        )
        old_paid = core.settings.paid_model
        old_policy = owner_authority.evidence(core.store, core.settings, plan.authority).policy
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            port = listener.getsockname()[1]
        config = replace(config, core_url=f"http://127.0.0.1:{port}")
        api = await child(tmp_path, "old-api", core, mode="api", port=port)
        workers = []
        try:
            await marker(api, "API_READY")
            worker = await child(
                tmp_path,
                "old-worker",
                core,
                mode="worker-video",
                output=str(tmp_path / "old-provider.json"),
            )
            workers.append(worker)
            await marker(worker, "WORK_COMPLETED")
            old_wire = json.loads((tmp_path / "old-provider.json").read_text("utf-8"))
            assert len(old_wire) == 2
            assert len(core.store.rows("SELECT * FROM model_usage WHERE state='UNKNOWN'")) == 1
            original_tool = core.store.one("SELECT * FROM agent_tool_calls")
            assert original_tool["state"] == "STARTED"
            async with private_http_client(config.core_url, config.bridge_token) as client:
                response = await client.post(
                    "/internal/v1/owner-authority/stop",
                    json={"authority": plan.authority, "operation_id": "native-carry-stop"},
                    headers={
                        "Authorization": "Bearer " + core.settings.control_token.get_secret_value()
                    },
                )
                assert response.status_code == 200, response.text
            with Spool(config) as spool:
                OwnerRuntime(spool, clock=lambda: core.clock.now).stop()
            # Genuine old API and native worker exits precede the offline controller.
            await stopped(worker)
            await stopped(api)
            assert worker.returncode is not None and api.returncode is not None
            original_usage = core.store.rows("SELECT * FROM model_usage ORDER BY rowid")
            capture = OwnerCapture.model_validate(
                json.loads(
                    core.store.one(
                        "SELECT value FROM runtime_meta WHERE key=?",
                        ("owner_input:" + event["event_id"],),
                    )["value"]
                )["capture"]
            )
            original_state = owner_input_state.read(core.store, core.settings, capture)
            assert original_state["terminal"] is False
            assert original_state["unsettled_model"] is True
            assert original_state["unsettled_work"] is False
            core.settings.paid_model = old_paid.model_copy(
                update={
                    "window_id": "native-recovered-window",
                    "approval_sha256": "e" * 64,
                    "concurrency_basis": "local_active_attempts_v2",
                }
            )
            items = executors(core.clock())
            request = {
                "version": 1,
                "operation_id": "native-carry-offline",
                "authorization_sha256": "f" * 64,
                "expected_head_sha256": owner_authority.custody(
                    core.store, core.settings, settlement=True
                )["head_sha256"],
                "authority": plan.authority,
                "successor_window_id": "native-recovered-window",
                "manifest_sha256": "0" * 64,
                "successor_policy": {},
                "executors": items,
            }
            path = tmp_path / "carry-request.json"

            async def controller(command):
                raw = encode(request).encode()
                path.write_bytes(raw)
                process = await child(
                    tmp_path,
                    command,
                    core,
                    mode="controller",
                    command=command,
                    executors=items,
                    request=str(path),
                    request_sha256=hashlib.sha256(raw).hexdigest(),
                )
                return json.loads(await finished(process))

            preview = await controller("preview-model-carry")
            request["manifest_sha256"] = preview["manifest_sha256"]
            policy = old_policy.model_copy(
                update={
                    "authorization_sha256": "f" * 64,
                    "unknown_carry_sha256": request["manifest_sha256"],
                    "paid_contract_sha256": paid_budget.sha(paid_budget.snapshot(core.settings)),
                }
            )
            request["successor_policy"] = policy.model_dump(mode="json")
            receipt = await controller("carry-closed-model-attempts")
            assert receipt["request_ids"] == [
                item["request_id"] for item in original_usage if item["state"] == "UNKNOWN"
            ]
            assert receipt["retained_micro_usd"] == sum(
                item["billed_micro_usd"] for item in original_usage if item["state"] == "UNKNOWN"
            )
            assert receipt["retained_micro_usd"] == 84
            api = await child(tmp_path, "new-api", core, mode="api", port=port)
            await marker(api, "API_READY")

            async def bridge(arguments, bridge_config):
                process = await child(
                    tmp_path,
                    "bridge-" + arguments[0],
                    core,
                    mode="bridge",
                    config=bridge_config,
                    arguments=arguments,
                )
                return json.loads(await finished(process))

            prepared = await bridge(
                [
                    "owner-carry-handoff-prepare",
                    "--carry-sha256",
                    receipt["carry_sha256"],
                    "--successor-policy-sha256",
                    digest(request["successor_policy"]),
                ],
                config,
            )
            assert prepared["state"] == "CARRY_HANDOFF_PREPARED"
            async with private_http_client(config.core_url, config.bridge_token) as client:
                response = await client.post(
                    "/internal/v1/owner-authority/inputs/state",
                    json=capture.model_dump(mode="json"),
                )
                assert response.json() == original_state
                control_headers = {
                    "Authorization": "Bearer " + core.settings.control_token.get_secret_value()
                }
                query = {"expected_revision": 2, "policy": request["successor_policy"]}
                preview_response = await client.post(
                    "/internal/v1/owner-authority/preview", json=query, headers=control_headers
                )
                assert preview_response.status_code == 200, preview_response.text
                activated = await client.post(
                    "/internal/v1/owner-authority/activate",
                    json={
                        **query,
                        "operation_id": "native-carry-activate",
                        "preview_sha256": preview_response.json()["preview_sha256"],
                    },
                    headers=control_headers,
                )
                assert activated.status_code == 200, activated.text
                successor = activated.json()["authority"]
                newer = replace(config, owner_authority=successor)
                assert (await bridge(["owner-handoff"], newer))["state"] == "ACTIVE"
                # Old source/reference cannot become an independent new input.
                assert (
                    await client.post(
                        "/internal/v1/owner-authority/inputs/approve",
                        json=capture.model_dump(mode="json"),
                    )
                ).status_code >= 400
                with Spool(newer) as spool:
                    runtime = OwnerRuntime(spool, clock=lambda: core.clock.now)
                    frame = encode(
                        raw_event(
                            number=43,
                            time=int(core.clock.now.timestamp()),
                            message=[{"type": "text", "data": {"text": "现在普通聊天"}}],
                        )
                    )
                    value = await prepare_owner_input(
                        runtime, client, None, frame, core.clock.now, ("127.0.0.1", 3001)
                    )
                    assert runtime.persist_capture(
                        value["event"], frame, received_at=core.clock.now, peer=("127.0.0.1", 3001)
                    )
                    assert await runtime.flush_one(client)
                text_worker = await child(
                    tmp_path,
                    "new-worker",
                    core,
                    mode="worker-text",
                    output=str(tmp_path / "new-provider.json"),
                )
                workers.append(text_worker)
                await finished(text_worker)
                wire = json.loads((tmp_path / "new-provider.json").read_text("utf-8"))
                assert len(wire) == 1 and "video_url" not in encode(wire)
                sends = []
                with Spool(newer) as spool:
                    runtime = OwnerRuntime(spool, clock=lambda: core.clock.now)
                    async with private_http_client(
                        newer.onebot_http_url,
                        newer.onebot_token,
                        transport=httpx.MockTransport(lambda req: provider(req, sends)),
                    ) as protocol:
                        guard = OwnerSendGuard(runtime, client)
                        assert await deliver_tick(
                            client, OwnerOneBot(guard, protocol), spool, guard=guard
                        )
                assert len(sends) == 1
            assert core.store.one("SELECT state FROM outbox")["state"] == "SENT"
            assert core.store.one("SELECT * FROM agent_tool_calls") == original_tool
            assert (
                core.store.rows(
                    "SELECT * FROM model_usage WHERE request_id IN (?,?) ORDER BY rowid",
                    tuple(item["request_id"] for item in original_usage),
                )
                == original_usage
            )
        finally:
            for process in workers:
                await stopped(process)
            await stopped(api)

    run(scenario())
