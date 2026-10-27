"""Signed native requests preserve retained UNKNOWN, cache observations and accounting."""

import asyncio
import hashlib
import json
import sqlite3
from pathlib import Path

import httpx
import pytest

from companion import cognition_read, model_carry, owner_authority, owner_input_state
from companion.config import ActivityContextConfig
from companion.core import Core
from companion.owner_authority_protocol import OwnerCapture
from companion.qq_rich import input_digest
from companion.store import encode
from companion_delivery_protocol.authority import capture_signing_payload
from tests.conftest import event
from tests.model_carry_support import migrated
from tests.test_activity_shadow import diagnostic
from tests.test_activity_shadow_api import acknowledge, worker
from tests.test_onebot_capture import real_core_http


async def admit_current(core, case):
    text = "SYNTHETIC current carried-window input"
    source = case.capture.source_event_id.rsplit(":", 1)[0] + ":2"
    value = case.capture.model_dump(mode="json", exclude={"signature"})
    value.update(authority=case.successor["authority"], source_event_id=source,
        event_id="evt_" + hashlib.sha256(source.encode()).hexdigest(),
        input_sha256=input_digest({"text": text}), occurred_at=core.clock(),
        received_at=core.clock())
    value["signature"] = case.key.sign(capture_signing_payload(value)).hex()
    capture = OwnerCapture.model_validate(value)
    incoming = event(core, 2, text, origin="onebot-direct:FriendMessage:10001").model_copy(
        update={"event_id": capture.event_id, "source_event_id": source})
    async with real_core_http(core) as port, httpx.AsyncClient(
        base_url=f"http://127.0.0.1:{port}", trust_env=False,
        headers={"Authorization": "Bearer " + core.settings.bridge_token.get_secret_value()},
    ) as client:
        approved = await client.post("/internal/v1/owner-authority/inputs/approve",
                                     json=capture.model_dump(mode="json"))
        assert approved.status_code == 200, approved.text
        accepted = await client.post("/internal/v1/inbox/events",
            json=incoming.model_dump(mode="json"), headers={"X-Idempotency-Key": "carry-shadow"})
        assert accepted.status_code == 202, accepted.text
    return capture, accepted.json()["job_id"]


@pytest.mark.parametrize("boundary,cache_usage", [
    pytest.param("settled", None, id="cache-absent"),
    pytest.param("settled", 0, id="cache-zero"),
    pytest.param("settled", 40, id="cache-known"),
    pytest.param("cancel", None, id="cancel"),
    pytest.param("unknown", None, id="unknown"),
])
def test_carried_owner_none_off_shadow_keep_wire_paid_basis_and_liability(
    core, tmp_path, boundary, cache_usage,
):
    case = migrated(core, tmp_path)
    assert model_carry.active(core.settings)
    original = core.store.one("SELECT * FROM model_usage WHERE request_id=?", (case.request_id,))
    original_admission = core.store.one("SELECT * FROM model_admissions WHERE request_id=?",
                                        (case.request_id,))
    assert original["state"] == "UNKNOWN" and original["billed_micro_usd"] > 0

    async def exercise():
        capture, job_id = await admit_current(core, case)
        journal = Path(core.settings.paid_model.journal_path)
        prefix = await asyncio.to_thread(journal.read_bytes)
        starting, reports, receipts = {}, {}, {}
        for mode in ("NONE", "OFF", "SHADOW"):
            directory = tmp_path / mode
            directory.mkdir()
            database = directory / "runtime.db"
            with sqlite3.connect(database) as copy:
                core.store.db.backup(copy)
            settings = core.settings.model_copy(deep=True)
            settings.database = str(database)
            settings.activity_context = None if mode == "NONE" else ActivityContextConfig(mode=mode)
            starting[mode] = settings
        # Reuse the same synthetic journal path and prefix bound by the native authority.
        for mode, settings in starting.items():
            await asyncio.to_thread(journal.write_bytes, prefix)
            reports[mode] = await worker(settings, core.clock(), job_id, boundary,
                tmp_path / mode / "worker.json", cache_usage=cache_usage)
            assert reports[mode]["worked"]
            with Core(settings, clock=core.clock) as observed:
                assert observed.store.one("SELECT * FROM model_usage WHERE request_id=?",
                                           (case.request_id,)) == original
                assert observed.store.one("SELECT * FROM model_admissions WHERE request_id=?",
                                           (case.request_id,)) == original_admission
                old_input = owner_input_state.read(observed.store, settings, case.capture)
                assert old_input["unsettled_model"] and not old_input["terminal"]
                conversation = observed.store.one(
                    "SELECT snapshots_json FROM agent_conversations WHERE job_id=?", (job_id,))
                snapshots = json.loads(conversation["snapshots_json"])
                if mode == "SHADOW":
                    sample = diagnostic(snapshots)
                    assert not sample["used_in_request"] and sample["omitted"]["unavailable"] == 0
                else:
                    assert all(item["kind"] != "activity_shadow" for item in snapshots)
                row, = observed.store.rows("SELECT * FROM model_usage WHERE job_id=?", (job_id,))
                admission = observed.store.one("SELECT * FROM model_admissions WHERE request_id=?",
                                               (row["request_id"],))
                audit = observed.store.one("SELECT safe_detail_json FROM audit_log "
                    "WHERE action='model.usage_diagnostic' AND entity_id=?", (row["request_id"],))
                if boundary == "settled":
                    assert reports[mode]["job"]["state"] == "SUCCEEDED", reports[mode]
                    assert row["state"] == "SETTLED"
                    assert row["billed_micro_usd"] == (72 if cache_usage == 40 else 108)
                    assert row["cached_input_tokens"] == (cache_usage or 0)
                    assert json.loads(audit["safe_detail_json"])["cache_usage_present"] is (
                        cache_usage is not None)
                    projected = cognition_read.CognitionRead(observed).page(
                        "model", record_id=row["request_id"])["items"][0]["usage"]["cache_usage"]
                    assert projected == {"state": "NOT_RECORDED" if cache_usage is None
                                         else "RECORDED", "cached_input_tokens": cache_usage}
                    before_ack = observed.store.rows(
                        "SELECT * FROM model_usage ORDER BY request_id")
                    await acknowledge(observed)
                    assert observed.store.rows(
                        "SELECT * FROM model_usage ORDER BY request_id") == before_ack
                elif boundary == "unknown":
                    assert row["state"] == "UNKNOWN"
                    assert row["billed_micro_usd"] == admission["reserved_micro_usd"] > 0
                else:
                    assert row["state"] == "SETTLED" and row["billed_micro_usd"] == 0
                with observed.store.transaction():
                    custody = owner_authority.custody(observed.store, settings, settlement=True)
                assert case.receipt["carry_sha256"] in custody["model_carries"]
                assert case.request_id in custody["model_closures"]
                assert (row["request_id"] in custody["model_closures"]) == (boundary == "unknown")
                assert model_carry.scope_windows(custody, settings.paid_model.window_id) == [
                    "offline-window-1", "successor-window-1"]
                sources = observed.store.rows("SELECT event_id FROM job_sources WHERE job_id=? "
                                               "ORDER BY event_id", (job_id,))
                assert sources == [{"event_id": capture.event_id}]
                receipts[mode] = {
                    "job": observed.store.one(
                        "SELECT state,last_error_code FROM jobs WHERE job_id=?", (job_id,)),
                    "sources": sources,
                    "usage": {key: value for key, value in row.items()
                              if key not in {"request_id", "usage_id"}},
                    "paid_basis": {key: admission[key] for key in (
                        "logical_request_id", "payload_sha256", "contract_sha256", "window_id",
                        "input_bound", "output_bound", "reserved_micro_usd", "measurement_json")},
                    "outbox": observed.store.rows("SELECT state,action_type,content_json,"
                        "content_sha256,origin_event_id FROM outbox WHERE origin_event_id=?",
                        (capture.event_id,)),
                }
            assert (await asyncio.to_thread(journal.read_bytes)).startswith(prefix)
        assert reports["NONE"]["requests"] == reports["OFF"]["requests"] == (
            reports["SHADOW"]["requests"])
        assert len(reports["SHADOW"]["requests"]) == int(boundary != "cancel")
        assert receipts["NONE"] == receipts["OFF"] == receipts["SHADOW"]
        assert bool(receipts["SHADOW"]["outbox"]) == (boundary == "settled")
        if boundary == "settled":
            assert receipts["SHADOW"]["outbox"][0]["state"] == "SENT"
        wire = encode(reports["SHADOW"]["requests"])
        assert "activity_shadow" not in wire and "used_in_request" not in wire
        assert "synthetic model boundary source" not in wire

    asyncio.run(exercise())
