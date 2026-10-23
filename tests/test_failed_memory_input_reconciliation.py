"""Retire a historical settled recall failure without replay, refund or invented ACK."""
import asyncio
import json
from pathlib import Path

import httpx
import pytest

from companion import budget, live_text
from companion.core import Core
from companion.model import RECALL_RULES, ModelClient
from companion.privacy import advance_epoch, epoch
from companion.runtime import Runtime
from companion.store import after, uid
from tests.conftest import ingest, run
from tests.test_onebot_capture import capture_approval, source_window
from tests.test_onebot_direct import raw_event
from tests.test_private_companion import companion_options


@pytest.mark.parametrize("mutation", [
    None, "new_privacy_epoch", "contract_replay", "unknown", "reserved", "missing_admission",
    "outbox",
    "active_job", "lease", "foreign_event", "superseded", "missing_failure", "wrong_failure",
    "wrong_scenario", "erased_source", "not_stopped", "forged_revision", "forged_resolution",
])
def test_native_failed_recall_retirement_preserves_original_accounting(
    core, tmp_path, mutation,
):
    calls = []
    if mutation == "contract_replay":
        seed = ingest(core, 900, "expired unrelated source",
                      origin=f"seed:FriendMessage:{core.settings.owner_qq_id}")
        with core.store.transaction():
            core.store.execute("UPDATE jobs SET state='SUCCEEDED' WHERE job_id=?",
                               (seed["job_id"],))
            core.ledger.expire_events(core.store, [seed["event_id"]], core.clock())
        assert core.store.one("SELECT state FROM privacy_erasure_ledger")["state"] == "COMPLETE"

    async def source(ws):
        await ws.send(json.dumps(raw_event(1, text="🤔"), ensure_ascii=False))
        await ws.wait_closed()

    def provider(request):
        calls.append(json.loads(request.content))
        return httpx.Response(200, json={
            "choices": [{"message": {"content": "synthetic invalid recall selection"}}],
            "usage": {"prompt_tokens": 100, "completion_tokens": 20},
        })

    async def scenario():
        async with source_window(core, tmp_path, source, live=True, approved=True,
                scope_options=companion_options(core)) as (window, client, protocol, _, sends):
            await window.capture_once(client, protocol)
            await window.approve_captured(client, capture_approval(window))
            async with httpx.AsyncClient(transport=httpx.MockTransport(provider)) as transport:
                with Core(core.settings, ModelClient(core.settings, transport),
                          core.clock) as worker:
                    assert worker.store is not core.store
                    job = worker.claim_job("historical-recall-worker")
                    messages = [{"role": "system", "content": RECALL_RULES},
                                {"role": "user", "content": '{"query":"🤔","memories":[]}'}]
                    # Reproduce the pre-upgrade outcome with native admission,
                    # settlement, audit and fail_job. Future safe-recall handling
                    # need not keep producing this already-persisted old failure.
                    request_id = budget.reserve(worker.store, worker.settings, job["job_id"],
                        "memory_review", messages, worker.clock(), clock=worker.clock)
                    await budget.complete(worker.model, worker.store, worker.settings,
                        request_id, messages, "memory_review", worker.clock)
                    worker.store.audit("memory.recall_invalid", job["job_id"], job["trace_id"],
                                       worker.clock())
                    worker.fail_job(job, "MEMORY_REVIEW_INVALID")
            await window.stop_live(client)
            core.clock.advance(core.settings.lease_seconds + 1)
            original_job = core.store.one("SELECT * FROM jobs WHERE job_id=?", (job["job_id"],))
            assert original_job["state"] == "FAILED"
            assert original_job["last_error_code"] == "MEMORY_REVIEW_INVALID"
            usage = core.store.one("SELECT * FROM model_usage")
            assert usage["state"] == "SETTLED" and usage["billed_micro_usd"] > 0
            if mutation == "new_privacy_epoch":
                advance_epoch(core.store)
            elif mutation == "contract_replay":
                from companion.erasure import CONTRACT_KEY, contract_signature

                old_epoch = epoch(core.store)
                ledger = Path(core.settings.erasure_ledger)
                ledger_before = await asyncio.to_thread(ledger.read_bytes)
                core.store.execute("UPDATE runtime_meta SET value='1,legacy' WHERE key=?",
                                   (CONTRACT_KEY,))
                with Runtime(core.settings, clock=core.clock) as upgraded:
                    assert upgraded.store is not core.store
                    assert epoch(upgraded.store) == old_epoch + 1
                    assert upgraded.store.one("SELECT value FROM runtime_meta WHERE key=?",
                                              (CONTRACT_KEY,))["value"] == contract_signature()
                assert await asyncio.to_thread(ledger.read_bytes) == ledger_before
            elif mutation in {"unknown", "reserved"}:
                core.store.execute("UPDATE model_usage SET state=?", (mutation.upper(),))
            elif mutation == "missing_admission":
                core.store.execute("DELETE FROM model_admissions")
            elif mutation == "outbox":
                core.store.insert("outbox", message_id=uid("msg"), origin_event_id=job["event_id"],
                    conversation_id=job["conversation_id"], action_type="qq.private.reply",
                    content_json='{"text":"late"}', content_sha256="a"*64,
                    delivery_key="late-failed-recall", state="FAILED", attempt_count=0,
                    created_at=core.clock(), updated_at=core.clock())
            elif mutation == "active_job":
                core.store.execute("UPDATE jobs SET state='RUNNING' WHERE job_id=?",
                                   (job["job_id"],))
            elif mutation == "lease":
                core.store.execute("UPDATE jobs SET lease_until=? WHERE job_id=?",
                                   (after(core.clock(), 10), job["job_id"]))
            elif mutation == "foreign_event":
                core.store.execute("UPDATE events SET actor_id='companion' WHERE event_id=?",
                                   (job["event_id"],))
            elif mutation == "superseded":
                core.store.execute("UPDATE conversations SET reply_revision=reply_revision+1")
            elif mutation == "missing_failure":
                core.store.execute("DELETE FROM audit_log WHERE action='memory.recall_invalid'")
            elif mutation == "wrong_failure":
                core.store.execute("UPDATE jobs SET last_error_code='OTHER_FAILURE'")
            elif mutation == "wrong_scenario":
                core.store.execute("UPDATE model_usage SET scenario='chat'")
            elif mutation == "erased_source":
                with core.store.transaction():
                    core.ledger.expire_events(core.store, [job["event_id"]], core.clock())
            elif mutation == "not_stopped":
                core.store.execute("DELETE FROM runtime_meta WHERE key=?",
                                   (live_text.STOP + window.plan.sha256,))
                core.store.execute("DELETE FROM runtime_meta WHERE key='live_text_stopped'")
            elif mutation in {"forged_revision", "forged_resolution"}:
                original = window.core_post

                async def forged(*args):
                    result = await original(*args)
                    result["reply_revision" if mutation == "forged_revision" else "resolution"] = (
                        True if mutation == "forged_revision" else "ACK")
                    return result

                window.core_post = forged
            models = core.store.rows("SELECT * FROM model_usage")
            admissions = core.store.rows("SELECT * FROM model_admissions")
            jobs = core.store.rows("SELECT * FROM jobs")
            journal = Path(core.settings.paid_model.journal_path)
            budget_before = await asyncio.to_thread(journal.read_bytes)
            head = window.row()["journal_head"]
            if mutation not in {None, "new_privacy_epoch", "contract_replay"}:
                with pytest.raises((ValueError, httpx.HTTPStatusError)):
                    await window.reconcile_cancelled_inputs(client)
                assert window.row()["journal_head"] == head
                assert window.db.execute("SELECT state FROM window_inputs").fetchone()[0] == (
                    "APPROVED")
            else:
                result = await window.reconcile_cancelled_inputs(client)
                assert result["cancelled_inputs"] == 1 and result["new_inputs"] == 1
                assert result["state"] == "STOPPED"
                records, _ = window.read_journal()
                receipt = next(row["data"]["core_receipt"] for row in records
                               if row["kind"] == "INPUT_CANCELLED")
                assert receipt["state"] == "FAILED"
                assert receipt["resolution"] == "SETTLED_NO_DELIVERY"
                assert receipt["failure_code"] == "MEMORY_REVIEW_INVALID"
                assert receipt["failure_phase"] == "memory_recall"
                assert receipt["model_attempts"] == 1 and receipt["send_attempts"] == 0
                assert receipt["billed_micro_usd"] == usage["billed_micro_usd"]
                assert receipt["reply_revision"] == 1
                assert (receipt["input_privacy_epoch"] < receipt["privacy_epoch"]) == (
                    mutation in {"new_privacy_epoch", "contract_replay"})
                assert not any(row["kind"] == "ACK" for row in records)
                assert window.verify(active=False)["state"] == "STOPPED"
                assert (await window.reconcile_cancelled_inputs(client))["cancelled_inputs"] == 0
            assert core.store.rows("SELECT * FROM model_usage") == models
            assert core.store.rows("SELECT * FROM model_admissions") == admissions
            assert core.store.rows("SELECT * FROM jobs") == jobs
            assert await asyncio.to_thread(journal.read_bytes) == budget_before
            assert len(calls) == 1 and sends == []

    run(scenario())
