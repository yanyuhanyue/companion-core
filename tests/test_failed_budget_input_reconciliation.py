"""Retire native pre-dispatch budget rejection without altering its original accounts."""

import asyncio
import json
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
import pytest

from companion import budget, live_text, paid_budget, qq_rich
from companion.contracts import CoreError
from companion.core import Core
from companion.model import ModelClient
from companion.privacy import advance_epoch
from companion.retrieval import SemanticIndex
from companion.store import encode, uid
from companion_onebot_bridge import continuous, network
from tests.conftest import run
from tests.test_onebot_capture import capture_approval, real_core_http, source_window
from tests.test_onebot_direct import raw_event
from tests.test_owner_recall_bridge import recall_frame
from tests.test_private_companion import companion_options
from tests.test_qq_rich_input import rich_event


@pytest.fixture(autouse=True)
def no_embedding_download(monkeypatch):
    monkeypatch.setattr(SemanticIndex, "warmup", lambda self: None)


@asynccontextmanager
async def native_window(core, tmp_path, source):
    options = companion_options(core)
    options["core_options"]["monthly_limit_micro_usd"] = 0
    options["paid_options"].update(unlimited_period_spend=False, daily_micro_usd=200000,
                                   monthly_micro_usd=1000000, window_micro_usd=2000000)
    async with real_core_http(core) as port:
        url = f"http://127.0.0.1:{port}"
        async with source_window(core, tmp_path, source, live=True, approved=True,
                scope_options=options, bridge_options={"core_url": url}) as (
            window, _, protocol, _, sends,
        ), network.private_http_client(url, window.plan.config.bridge_token) as client:
            yield window, client, protocol, sends


def add_attempt(store, settings, job, now, *, admission):
    # The admission-only mutation has a foreign usage carrier to preserve its FK;
    # the failed input still has no usage, but its logical request has an admission.
    request_id = uid("model")
    store.insert("model_usage", usage_id=uid("usage"), request_id=request_id,
                 job_id=None if admission else job["job_id"], scenario="chat", provider="mock",
                 model="synthetic", input_tokens=0, output_tokens=0, billed_micro_usd=0,
                 state="SETTLED", occurred_at=now)
    if admission:
        store.execute("INSERT OR IGNORE INTO model_budget_windows VALUES (?,?)",
                      (settings.paid_model.window_id, "c" * 64))
        store.insert("model_admissions", request_id=request_id, logical_request_id=job["job_id"],
                     attempt_id=uid("attempt"), attempt_number=1,
                     window_id=settings.paid_model.window_id, contract_sha256="c" * 64,
                     payload_sha256="d" * 64, snapshot_json="{}", reserved_micro_usd=1,
                     input_bound=1, output_bound=1, lease_token="synthetic-expired",
                     lease_until=now, journal_path=settings.paid_model.journal_path,
                     journal_id=store.one("SELECT value FROM runtime_meta WHERE "
                                          "key='model_budget_journal_id'")["value"])
    assert not store.rows("PRAGMA foreign_key_check")


@pytest.mark.parametrize("mutation", [
    None, "new_privacy_epoch", "usage", "admission", "outbox", "missing_failure",
    "wrong_failure", "not_stopped", "forged_phase", "forged_attempt", "rich",
])
def test_native_failed_budget_retirement_preserves_original_accounts(core, tmp_path, mutation):
    calls = []

    async def source(ws):
        await ws.send(json.dumps(raw_event(1, text="今天想聊聊茶"), ensure_ascii=False))
        await ws.wait_closed()

    def provider(request):
        calls.append(request)
        raise AssertionError("A rejected admission must never invoke the provider")

    async def scenario():
        async with native_window(core, tmp_path, source) as (window, client, protocol, sends):
            await window.capture_once(client, protocol)
            await window.approve_captured(client, capture_approval(window))
            async with httpx.AsyncClient(transport=httpx.MockTransport(provider)) as transport:
                with Core(core.settings, ModelClient(core.settings, transport),
                          core.clock) as worker:
                    assert worker.store is not core.store and worker.store.db is not core.store.db
                    job = worker.claim_job("budget-rejection-worker")
                    with pytest.raises(CoreError, match="MONTHLY_BUDGET_EXHAUSTED") as rejected:
                        budget.reserve(worker.store, worker.settings, job["job_id"], "chat",
                                       [{"role": "user", "content": "今天想聊聊茶"}],
                                       worker.clock(), clock=worker.clock)
                    worker.fail_job(job, rejected.value.code)
            assert core.store.rows("SELECT * FROM model_usage") == []
            assert core.store.rows("SELECT * FROM model_admissions") == []
            assert core.store.rows("SELECT * FROM outbox") == []
            failure = core.store.one("SELECT * FROM audit_log WHERE action='job.failed' "
                                     "AND entity_id=?", (job["job_id"],))
            assert json.loads(failure["safe_detail_json"]) == {
                "code": "MONTHLY_BUDGET_EXHAUSTED", "state": "FAILED",
            }
            original_job = core.store.one("SELECT * FROM jobs WHERE job_id=?", (job["job_id"],))
            assert original_job["state"] == "FAILED"
            assert original_job["last_error_code"] == "MONTHLY_BUDGET_EXHAUSTED"
            await window.stop_live(client)
            core.clock.advance(core.settings.lease_seconds + 1)

            if mutation == "new_privacy_epoch":
                advance_epoch(core.store)
            elif mutation in {"usage", "admission"}:
                add_attempt(core.store, core.settings, job, core.clock(),
                            admission=mutation == "admission")
            elif mutation == "outbox":
                core.store.insert("outbox", message_id=uid("msg"), origin_event_id=job["event_id"],
                    conversation_id=job["conversation_id"], action_type="qq.private.reply",
                    content_json='{"text":"late"}', content_sha256="a" * 64,
                    delivery_key="late-budget-rejection", state="FAILED", attempt_count=0,
                    created_at=core.clock(), updated_at=core.clock())
            elif mutation == "missing_failure":
                core.store.execute("DELETE FROM audit_log WHERE action='job.failed'")
            elif mutation == "wrong_failure":
                core.store.execute("UPDATE audit_log SET safe_detail_json=? "
                                   "WHERE action='job.failed'",
                                   (encode({"code": "OTHER_FAILURE", "state": "FAILED"}),))
            elif mutation == "not_stopped":
                core.store.execute("DELETE FROM runtime_meta WHERE key IN (?,?)",
                                   (live_text.STOP + window.plan.sha256, "live_text_stopped"))
            elif mutation == "rich":
                reference = {"window_sha256": window.plan.sha256, "event_id": job["event_id"]}
                plain = await client.post("/internal/v1/live-text/inputs/cancelled", json=reference)
                assert plain.status_code == 200, plain.text
                body = json.loads(core.store.one("SELECT body_json FROM events WHERE event_id=?",
                                                (job["event_id"],))["body_json"])
                body["rich"] = rich_event([
                    {"type": "text", "data": {"text": body["text"]}},
                    {"type": "reply", "data": {"id": "-42"}},
                ])["payload"]["rich"]
                qq_rich.validate(body["rich"])
                key = live_text.INPUT + job["event_id"]
                receipt = json.loads(core.store.one("SELECT value FROM runtime_meta WHERE key=?",
                                                   (key,))["value"])
                receipt["input_sha256"] = qq_rich.input_digest(body)
                assert receipt["input_sha256"] != plain.json()["input_sha256"]
                # Keep the Core input-digest guard satisfied to isolate the plain-input
                # restriction. The original Bridge capture and journal remain intact.
                with core.store.transaction():
                    core.store.execute("UPDATE events SET body_json=? WHERE event_id=?",
                                       (encode(body), job["event_id"]))
                    core.store.execute("UPDATE runtime_meta SET value=? WHERE key=?",
                                       (encode(receipt), key))
            elif mutation in {"forged_phase", "forged_attempt"}:
                original = window.core_post

                async def forged(*args):
                    receipt = await original(*args)
                    receipt["failure_phase" if mutation == "forged_phase" else "model_attempts"] = (
                        "memory_recall" if mutation == "forged_phase" else 1)
                    return receipt

                window.core_post = forged

            tables = ("events", "jobs", "audit_log", "model_usage", "model_admissions", "outbox")
            before = {table: core.store.rows(f"SELECT * FROM {table} ORDER BY rowid")
                      for table in tables}
            journal = Path(core.settings.paid_model.journal_path)
            budget_before = await asyncio.to_thread(journal.read_bytes)
            head = window.row()["journal_head"]
            reference = {"window_sha256": window.plan.sha256, "event_id": job["event_id"]}
            if mutation not in {None, "new_privacy_epoch"}:
                if mutation == "rich":
                    blocked = await client.post("/internal/v1/live-text/inputs/cancelled",
                                                json=reference)
                    assert blocked.status_code == 409
                    assert blocked.json() == {"error": "LIVE_TEXT_FAILED_INPUT_UNPROVEN"}
                with pytest.raises((ValueError, httpx.HTTPStatusError)):
                    await window.reconcile_cancelled_inputs(client)
                assert window.row()["journal_head"] == head
                assert window.db.execute("SELECT state FROM window_inputs").fetchone()[0] == (
                    "APPROVED")
            else:
                proof = await client.post("/internal/v1/live-text/inputs/cancelled", json=reference)
                assert proof.status_code == 200, proof.text
                repeated = await client.post("/internal/v1/live-text/inputs/cancelled",
                                             json=reference)
                assert repeated.status_code == 200 and repeated.json() == proof.json()
                receipt = proof.json()
                assert receipt["state"] == "FAILED"
                assert receipt["resolution"] == "REJECTED_BEFORE_DISPATCH"
                assert receipt["failure_code"] == "MONTHLY_BUDGET_EXHAUSTED"
                assert receipt["failure_phase"] == "model_admission"
                assert receipt["failure_evidence_sha256"] == paid_budget.sha(failure)
                assert receipt["model_attempts"] == receipt["send_attempts"] == 0
                assert not {"billed_micro_usd", "settled_model_usage_sha256"} & receipt.keys()
                assert receipt["reply_revision"] == 1
                assert (receipt["input_privacy_epoch"] < receipt["privacy_epoch"]) == (
                    mutation == "new_privacy_epoch")
                result = await window.reconcile_cancelled_inputs(client)
                assert result["cancelled_inputs"] == 1 and result["new_inputs"] == 1
                assert result["state"] == "STOPPED"
                records, _ = window.read_journal()
                retired = [row for row in records if row["kind"] == "INPUT_CANCELLED"]
                assert len(retired) == 1 and retired[0]["data"]["core_receipt"] == receipt
                assert not any(row["kind"] == "ACK" for row in records)
                assert window.db.execute("SELECT state FROM window_inputs").fetchone()[0] == (
                    "CANCELLED")
                assert window.verify(active=False)["state"] == "STOPPED"
                retired_head = window.row()["journal_head"]
                assert (await window.reconcile_cancelled_inputs(client))["cancelled_inputs"] == 0
                assert window.row()["journal_head"] == retired_head
            assert core.store.one("SELECT * FROM jobs WHERE job_id=?",
                                  (job["job_id"],)) == original_job
            assert {table: core.store.rows(f"SELECT * FROM {table} ORDER BY rowid")
                    for table in tables} == before
            assert await asyncio.to_thread(journal.read_bytes) == budget_before
            assert calls == sends == []

    run(scenario())


@pytest.mark.parametrize("terminal", ["SUPERSEDED", "RECALLED"])
def test_reconciliation_skips_inputs_already_retired_by_native_state(core, tmp_path, terminal):
    number = 0

    async def source(ws):
        nonlocal number
        number += 1
        await ws.send(json.dumps(raw_event(number, text="本轮输入")))
        await ws.wait_closed()

    async def scenario():
        async with native_window(core, tmp_path, source) as (window, client, protocol, sends):
            await window.capture_once(client, protocol)
            await window.approve_captured(client, capture_approval(window))
            first = window.db.execute("SELECT * FROM window_inputs").fetchone()
            if terminal == "SUPERSEDED":
                await window.capture_once(client, protocol)
                await window.approve_captured(client, capture_approval(window))
            else:
                receiver = continuous.OwnerSource(window, protocol, asyncio.Event())
                receiver.put_recall(recall_frame(), core.clock.now, ("127.0.0.1", 12345))
                assert await continuous.flush_recalls(window, client)
            proof = await window.input_state(client, first)
            assert proof["state"] == terminal
            window.retire_input(first, proof)
            await window.stop_live(client)
            records, _ = window.read_journal()
            retired = next(row for row in records if row["kind"] == "INPUT_" + terminal)
            result = await window.reconcile_cancelled_inputs(client)
            assert result["cancelled_inputs"] == (1 if terminal == "SUPERSEDED" else 0)
            assert window.db.execute("SELECT state FROM window_inputs WHERE event_id=?",
                                     (first["event_id"],)).fetchone()[0] == terminal
            assert retired in window.read_journal()[0]
            assert window.verify(active=False)["state"] == "STOPPED"
            assert core.store.rows("SELECT * FROM model_usage") == []
            assert core.store.rows("SELECT * FROM outbox") == []
            assert sends == []

    run(scenario())
