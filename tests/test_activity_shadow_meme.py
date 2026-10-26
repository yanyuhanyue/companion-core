"""B2b source custody and delivery stay authoritative beside unused SHADOW data."""

import asyncio
import json
import sqlite3
from pathlib import Path

import httpx
import pytest

from companion import context_planner
from companion.config import ActivityContextConfig
from companion.core import Core
from companion.paid_budget import sha
from companion.store import encode
from companion.token_measurement import measure_payload
from tests.meme_authority_fixture import admit
from tests.test_activity_shadow import diagnostic
from tests.test_activity_shadow_api import acknowledge, worker
from tests.test_meme_core_runtime import runtime_case as runtime_case
from tests.test_meme_native_process import (
    test_empty_turn_native_acquisition_caption_and_next_turn_exact_delivery as acquisition,
)
from tests.test_onebot_capture import real_core_http


def checked_plan(core, job):
    saved = core.store.one(
        "SELECT * FROM agent_conversations WHERE job_id=?", (job["job_id"],))
    assert saved["phase"] == "COMPLETE"
    snapshots = json.loads(saved["snapshots_json"])
    plan = context_planner.check(core, job, snapshots)
    assert plan is not None and plan["pending"] is None
    proofs = [item for item in snapshots
              if item["kind"] not in {"context_plan", "activity_shadow"}]
    assert plan["initial_source_proof_count"] == len(proofs)
    assert plan["initial_source_proofs_sha256"] == sha(proofs)
    for record in plan["requests"]:
        assert record["source_proof_count"] == len(proofs)
        assert record["source_proofs_sha256"] == sha(proofs)
    return snapshots, plan


@pytest.mark.parametrize("runtime_case", ["no_current"], indirect=True)
def test_b2b_offer_none_off_shadow_preserve_actual_wire_sources_paid_basis_and_ack(
    runtime_case, tmp_path,
):
    core = runtime_case.core
    original_usage = core.store.rows("SELECT * FROM model_usage ORDER BY request_id")

    async def exercise():
        async with real_core_http(core) as port, httpx.AsyncClient(
            base_url=f"http://127.0.0.1:{port}", trust_env=False,
            headers={"Authorization": "Bearer " + core.settings.bridge_token.get_secret_value()},
        ) as client:
            current, accepted = await admit(client, runtime_case.config, runtime_case.grant,
                core.clock.now, 2, text="想要温暖的抱抱")
            assert accepted.status_code == 202, accepted.text
            job_id = accepted.json()["job_id"]
        journal = Path(core.settings.paid_model.journal_path)
        prefix = await asyncio.to_thread(journal.read_bytes)
        results = {}
        for mode in ("NONE", "OFF", "SHADOW"):
            directory = tmp_path / mode
            directory.mkdir()
            database = directory / "runtime.db"
            with sqlite3.connect(database) as copy:
                core.store.db.backup(copy)
            settings = core.settings.model_copy(deep=True)
            settings.database = str(database)
            settings.activity_context = None if mode == "NONE" else ActivityContextConfig(mode=mode)
            await asyncio.to_thread(journal.write_bytes, prefix)
            report = await worker(settings, core.clock(), job_id, "settled",
                                  directory / "worker.json")
            assert report["worked"] and report["job"]["state"] == "SUCCEEDED", report
            with Core(settings, clock=core.clock) as observed:
                job = observed.store.one("SELECT * FROM jobs WHERE job_id=?", (job_id,))
                snapshots, plan = checked_plan(observed, job)
                if mode == "SHADOW":
                    sample = diagnostic(snapshots)
                    assert sample["used_in_request"] is False
                    assert sample["omitted"]["unavailable"] == 0
                    assert sample["work"] == sample["attention"] == []
                else:
                    assert all(item["kind"] != "activity_shadow" for item in snapshots)
                offer = json.loads(observed.store.one(
                    "SELECT value FROM runtime_meta WHERE key=?",
                    ("meme_offer:" + current["event_id"],))["value"])
                candidate = runtime_case.item["asset_sha256"]
                binding = offer["catalog_bindings"][candidate]
                assert binding["caption_job_id"] == runtime_case.caption_id
                assert binding["source_sha256"] == runtime_case.item["source_sha256"]
                request, = report["requests"]
                assert candidate in request["wire_utf8"]
                assert "activity_shadow" not in request["wire_utf8"]
                record, = plan["requests"]
                measured = measure_payload(settings, request["payload"], "chat",
                                           tools=request["payload"].get("tools", ()))
                assert record["payload_sha256"] == measured.payload_sha256
                assert record["measurement_sha256"] == sha(measured.receipt())
                admission = observed.store.one(
                    "SELECT * FROM model_admissions WHERE request_id=?", (record["request_id"],))
                usage = observed.store.one(
                    "SELECT * FROM model_usage WHERE request_id=?", (record["request_id"],))
                assert usage["state"] == "SETTLED"
                assert json.loads(admission["measurement_json"]) == measured.receipt()
                sources = observed.store.rows(
                    "SELECT event_id FROM job_sources WHERE job_id=? ORDER BY event_id", (job_id,))
                assert sources == [{"event_id": item} for item in sorted(
                    [runtime_case.source["event_id"], current["event_id"]])]
                before_ack = observed.store.rows("SELECT * FROM model_usage ORDER BY request_id")
                await acknowledge(observed)
                assert observed.store.rows(
                    "SELECT * FROM model_usage ORDER BY request_id") == before_ack
                assert observed.store.rows("SELECT * FROM model_usage WHERE job_id!=? "
                    "ORDER BY request_id", (job_id,)) == original_usage
                outbox, = observed.store.rows("SELECT state,action_type,content_json,"
                    "content_sha256 FROM outbox WHERE origin_event_id=?", (current["event_id"],))
                assert outbox["state"] == "SENT"
                results[mode] = {"requests": report["requests"], "sources": sources,
                    "offer": offer, "outbox": outbox,
                    "usage": {k: v for k, v in usage.items()
                              if k not in {"request_id", "usage_id"}},
                    "paid_basis": {k: admission[k] for k in (
                        "logical_request_id", "payload_sha256", "contract_sha256", "window_id",
                        "input_bound", "output_bound", "reserved_micro_usd", "measurement_json")}}
        assert results["NONE"] == results["OFF"] == results["SHADOW"]

    asyncio.run(exercise())


def test_shadow_keeps_b2b_native_acquisition_caption_and_exact_image_ack(
    core, tmp_path, monkeypatch,
):
    core.settings.activity_context = ActivityContextConfig(mode="SHADOW")
    # Existing real signed WS/HTTP, independent Core workers and Bridge process,
    # including the held ACK, exact downloaded bytes and one settled caption.
    acquisition(core, tmp_path, monkeypatch)
    jobs = core.store.rows("SELECT * FROM jobs WHERE kind='chat' ORDER BY rowid")
    assert len(jobs) == 2
    for job in jobs:
        snapshots, plan = checked_plan(core, job)
        sample = diagnostic(snapshots)
        assert not sample["used_in_request"] and sample["omitted"]["unavailable"] == 0
        assert sample["work"] == sample["attention"] == []
        assert len(plan["requests"]) == 1
        assert "activity_shadow" not in encode(json.loads(core.store.one(
            "SELECT conversation_json FROM agent_conversations WHERE job_id=?",
            (job["job_id"],))["conversation_json"]))
    assert not core.store.rows("SELECT 1 FROM agent_conversations c JOIN jobs j USING(job_id) "
                               "WHERE j.kind IN ('meme_discovery','meme_caption')")
