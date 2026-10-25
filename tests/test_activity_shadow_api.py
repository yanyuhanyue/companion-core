"""Identical signed input in independent workers, with original accounting/delivery."""

import asyncio
import json
import os
import sqlite3
import sys
from pathlib import Path

import httpx
import pytest

from companion import owner_authority
from companion.config import ActivityContextConfig
from companion.contracts import DeliveryResult, LeaseRequest
from companion.core import Core
from companion.owner_authority_protocol import OwnerSendCheck
from companion.store import encode
from companion.transport import begin_send, delivery_result, lease_deliveries
from tests.support.native_consumer_entry import environment
from tests.test_activity_shadow import diagnostic, goal
from tests.test_onebot_capture import real_core_http


async def worker(settings, now, job_id, boundary, path):
    value = settings.model_dump(mode="json")
    for field in ("model_key", "bridge_token", "control_token"):
        value[field] = getattr(settings, field).get_secret_value()
    path.write_text(encode({"settings": value, "now": now, "job_id": job_id,
                            "boundary": boundary}), encoding="utf-8")
    child = await asyncio.create_subprocess_exec(sys.executable, "-B", "-m",
        "tests.activity_shadow_worker", str(path),
        env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    try:
        stdout, stderr = await asyncio.wait_for(child.communicate(), 35)
    except BaseException:
        if child.returncode is None:
            child.kill()
        await child.wait()
        raise
    assert child.returncode == 0, stderr.decode(errors="replace")
    return json.loads(stdout)


async def acknowledge(core):
    authority = owner_authority.current(core.store, core.settings, core.clock,
                                         purpose="chat").authority.as_dict()
    item = lease_deliveries(core.store, core.settings, LeaseRequest(
        worker_id="shadow-synthetic-bridge", authority=authority), core.clock())["items"][0]
    begin_send(core.store, item["message_id"], item["lease_token"], core.clock(),
                core.settings, clock=core.clock)
    check = OwnerSendCheck(authority=authority, message_id=item["message_id"],
        event_id=item["origin_event_id"], content_sha256=item["content_sha256"],
        lease_token=item["lease_token"], reply_revision=item["reply_revision"])
    async with real_core_http(core) as port, httpx.AsyncClient(
        base_url=f"http://127.0.0.1:{port}", trust_env=False,
        headers={"Authorization": "Bearer " + core.settings.bridge_token.get_secret_value()},
    ) as client:
        approved = await client.post("/internal/v1/owner-authority/send-check",
                                     json=check.model_dump(mode="json"))
        assert approved.status_code == 200, approved.text
    delivery_result(core.store, core.settings, item["message_id"], DeliveryResult(
        lease_token=item["lease_token"], result="SENT", provider_message_id="synthetic-ack"),
        core.clock())


@pytest.mark.parametrize("boundary", ["settled", "cancel", "unknown"])
def test_signed_entry_shadow_preserves_actual_request_sources_fees_and_ack(
    core, tmp_path, boundary,
):
    async def exercise():
        settings_by_mode = {}
        async with environment(core, tmp_path, purposes=("chat", "goals"),
            core_options={"goals_enabled": True}) as (entry, _):
            source = await entry.admit(1, "SYNTHETIC please read this bounded content")
            job_id = entry.accepted[source["event_id"]]["job_id"]
            goal(core, source["event_id"], commitment=True)
            # Freeze one actual admitted database, not a fabricated context fixture.
            for mode in ("OFF", "SHADOW"):
                directory = tmp_path / mode
                directory.mkdir()
                path = directory / "runtime.db"
                with sqlite3.connect(path) as copy:
                    core.store.db.backup(copy)
                settings = core.settings.model_copy(deep=True)
                settings.database = str(path)
                settings.activity_context = ActivityContextConfig(mode=mode)
                settings_by_mode[mode] = settings
        # Native authority/cumulative projections bind the journal's actual path.
        # Replay the same synthetic starting pair serially, preserving that binding.
        journal_path = Path(core.settings.paid_model.journal_path)
        journal_prefix = await asyncio.to_thread(journal_path.read_bytes)
        reports, receipts = {}, {}
        for mode, settings in settings_by_mode.items():
            await asyncio.to_thread(journal_path.write_bytes, journal_prefix)
            reports[mode] = await worker(settings, core.clock(), job_id, boundary,
                                        tmp_path / mode / "worker.json")
            assert reports[mode]["worked"]
            assert reports[mode]["job"]["last_error_code"] not in {
                "MODEL_INPUT_LIMIT", "MODEL_CONTEXT_LIMIT", "PAID_JOURNAL_INVALID",
                "MODEL_BUDGET_RECONCILIATION_REQUIRED",
            }, reports[mode]["job"]
            with Core(settings, clock=core.clock) as observed:
                row = observed.store.one("SELECT * FROM agent_conversations WHERE job_id=?",
                                          (job_id,))
                snapshots = json.loads(row["snapshots_json"]) if row else []
                if mode == "SHADOW":
                    assert diagnostic(snapshots)["work"]
                    assert not diagnostic(snapshots)["used_in_request"]
                else:
                    assert all(item["kind"] != "activity_shadow" for item in snapshots)
                if boundary == "settled":
                    assert reports[mode]["job"]["state"] == "SUCCEEDED", reports[mode]["job"]
                    # Configuration changes do not refresh or invalidate saved diagnostics.
                    observed.settings.activity_context = ActivityContextConfig(mode="OFF")
                    await acknowledge(observed)
                usage = observed.store.rows("SELECT * FROM model_usage ORDER BY rowid")
                receipts[mode] = {
                    "job": observed.store.one(
                        "SELECT state,last_error_code FROM jobs WHERE job_id=?", (job_id,)),
                    "sources": observed.store.rows("SELECT event_id FROM job_sources "
                                                    "WHERE job_id=? ORDER BY event_id", (job_id,)),
                    "usage": [{k: v for k, v in item.items()
                               if k not in {"request_id", "usage_id"}}
                              for item in usage],
                    "outbox": observed.store.rows("SELECT state,action_type,content_json,"
                        "content_sha256,origin_event_id FROM outbox ORDER BY rowid"),
                    "plans": [{k: v for k, v in request.items() if k != "request_id"}
                        for item in snapshots if item["kind"] == "context_plan"
                        for request in item["metadata"]["core_context"]["requests"]],
                }
        assert reports["OFF"]["requests"] == reports["SHADOW"]["requests"]
        assert len(reports["SHADOW"]["requests"]) == int(boundary != "cancel")
        assert receipts["OFF"] == receipts["SHADOW"]
        wire = encode(reports["SHADOW"]["requests"])
        assert "activity_shadow" not in wire and "used_in_request" not in wire
        assert receipts["SHADOW"]["usage"][0]["state"] == (
            "UNKNOWN" if boundary == "unknown" else "SETTLED")
        assert bool(receipts["SHADOW"]["outbox"]) == (boundary == "settled")

    asyncio.run(exercise())
