"""Real owner HTTP control and independent worker dispatch/reset replay boundaries."""

import asyncio
import json
import sys

import httpx
from cryptography.fernet import Fernet
from pydantic import SecretStr

from companion.affect import NEUTRAL
from companion.backup import backup, restore
from companion.conversation_policy import ConversationPolicyConfig
from tests.conftest import ingest
from tests.test_evaluation_process import process_config
from tests.test_onebot_capture import real_core_http
from tests.test_paid_model_gate import approved_live


async def child(core, tmp_path, mode):
    config = tmp_path / (mode + ".json")
    process_config(core, config)
    return await asyncio.create_subprocess_exec(sys.executable, "-B", "-m", "tests.p1_process",
        "--config", str(config), "--now", core.clock(), "--mode", mode,
        stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE)


def prepare(core, tmp_path):
    approved_live(core, tmp_path)
    core.settings.affect_enabled = True
    core.settings.control_token = SecretStr("synthetic-owner-control-" * 3)
    accepted = ingest(core, 1, "简短一点")
    core.store.execute("UPDATE jobs SET state='SUCCEEDED' WHERE job_id=?", (accepted["job_id"],))
    core.clock.advance(1)


def test_owner_reset_after_context_before_dispatch_across_processes(core, tmp_path):
    prepare(core, tmp_path)
    core.settings.conversation_policy = ConversationPolicyConfig(mode="ENABLED")
    ingest(core, 2, "怎么处理这个问题")

    async def scenario():
        process = await child(core, tmp_path, "fence")
        try:
            async with asyncio.timeout(35), real_core_http(core) as port, httpx.AsyncClient(
                base_url=f"http://127.0.0.1:{port}", trust_env=False,
            ) as client:
                assert (await process.stdout.readline()).strip() == b"CONTEXT_READY"
                denied = await client.post("/internal/v2/affect/reset",
                    json={"request_id": "reset-process"}, headers={"Authorization":
                        "Bearer " + core.settings.bridge_token.get_secret_value()})
                assert denied.status_code == 401
                reset = await client.post("/internal/v2/affect/reset",
                    json={"request_id": "reset-process"}, headers={"Authorization":
                        "Bearer " + core.settings.control_token.get_secret_value()})
                assert reset.status_code == 200, reset.text
                assert reset.headers["Cache-Control"] == "no-store"
                stdout, stderr = await process.communicate(b"continue\n")
                assert process.returncode == 0, stderr.decode()
                value = json.loads(stdout)
                assert value["calls"] == [] and value["outbox"] == []
                assert value["affect"]["state"] == NEUTRAL
        finally:
            if process.returncode is None:
                process.kill()
                await process.wait()
    asyncio.run(scenario())


def test_reset_command_and_old_backup_replay_in_independent_core(core, tmp_path):
    prepare(core, tmp_path)
    key, archive, destination = Fernet.generate_key(), tmp_path / "old.enc", tmp_path / "old.db"
    backup(core.settings.database, archive, key)
    ingest(core, 2, "/重置互动")

    async def run(mode):
        process = await child(core, tmp_path, mode)
        try:
            async with asyncio.timeout(30):
                stdout, stderr = await process.communicate()
                assert process.returncode == 0, stderr.decode()
                return json.loads(stdout)
        finally:
            if process.returncode is None:
                process.kill()
                await process.wait()

    value = asyncio.run(run("command"))
    assert value["calls"] == [] and value["affect"]["state"] == NEUTRAL
    assert "已重置互动反馈" in value["outbox"][0]["content_json"]
    restore(archive, destination, key, core.settings.erasure_ledger)
    core.settings.database = str(destination)
    value = asyncio.run(run("inspect"))
    assert value["calls"] == [] and value["affect"]["state"] == NEUTRAL
