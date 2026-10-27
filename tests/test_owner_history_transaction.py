"""Native history validation retains its fences while batching one write transaction."""

import json
from pathlib import Path

import httpx
import pytest

from companion import owner_authority, owner_input, paid_budget
from companion.contracts import CoreError
from companion.store import after, encode
from tests.conftest import run
from tests.sdk_reader_fixture import answer, worker_for
from tests.support.native_consumer_entry import environment


async def native_history(core, tmp_path, check):
    async with environment(core, tmp_path, purposes=("chat", "history"), expires_in=30) as (
        entry, sends,
    ):
        def provider(request):
            return httpx.Response(200, json=answer("合成答复。"))

        async with worker_for(core, provider) as worker:
            for number in range(1, 4):
                source = await entry.admit(number, "合成历史输入。")
                job_id = entry.accepted[source["event_id"]]["job_id"]
                assert await worker.run_once("history-transaction", job_id=job_id)
                assert (await entry.send(entry.client, entry.protocol))["worked"]
                core.clock.advance(1)
            assert len(sends) == 3
            source = await entry.admit(4, "合成当前输入。")
            job = core.store.one("SELECT j.*,e.conversation_id FROM jobs j JOIN events e "
                                 "USING(event_id) WHERE event_id=?", (source["event_id"],))
            expected = owner_input.history_sources(core.store, core.settings, core.clock, job)
            assert len(expected) == 3
            check(job, expected)


@pytest.mark.parametrize("transactional", [True, False, "read_transaction"])
def test_history_reuses_custody_only_inside_one_write_transaction(
    core, tmp_path, monkeypatch, transactional,
):
    def check(job, expected):
        calls, recalls = [], []
        native = paid_budget.journal
        native_recall = owner_input.input_recall.require_not_recalled

        def journal(*args, **kwargs):
            calls.append(1)
            return native(*args, **kwargs)

        def recall(*args, **kwargs):
            recalls.append(1)
            return native_recall(*args, **kwargs)

        monkeypatch.setattr(paid_budget, "journal", journal)
        monkeypatch.setattr(owner_input.input_recall, "require_not_recalled", recall)
        if transactional == "read_transaction":
            core.store.db.execute("BEGIN")
            try:
                actual = owner_input.history_sources(core.store, core.settings, core.clock, job)
            finally:
                core.store.db.rollback()
            assert len(calls) == 10
        elif transactional:
            with core.store.transaction():
                actual = owner_input.history_sources(core.store, core.settings, core.clock, job)
            assert len(calls) == 2
        else:
            actual = owner_input.history_sources(core.store, core.settings, core.clock, job)
            assert len(calls) == 10
        assert actual == expected and len(recalls) == 3

    run(native_history(core, tmp_path, check))


@pytest.mark.parametrize("boundary", ["stop", "marker", "journal", "erasure", "expiry"])
def test_transaction_batch_rejects_live_boundary_changes_before_returning(
    core, tmp_path, monkeypatch, boundary,
):
    def check(job, expected):
        original = owner_input.input_recall.require_not_recalled
        changed, saved = [], {}

        def recall(*args, **kwargs):
            original(*args, **kwargs)
            if changed:
                return
            changed.append(1)
            if boundary == "stop":
                core.settings.owner_runtime.mode = "OFF"
            elif boundary == "marker":
                core.store.execute("UPDATE runtime_meta SET value=? WHERE key=?",
                                   (encode("OFF"), owner_authority.MODE))
            elif boundary == "expiry":
                core.clock.advance(31)
            else:
                path = Path(core.settings.paid_model.journal_path if boundary == "journal"
                            else core.settings.erasure_ledger)
                saved[path] = path.read_bytes()
                path.write_bytes(saved[path] + b"\n")

        monkeypatch.setattr(owner_input.input_recall, "require_not_recalled", recall)
        try:
            with pytest.raises(CoreError):
                with core.store.transaction():
                    owner_input.history_sources(core.store, core.settings, core.clock, job)
            assert changed
        finally:
            for path, content in saved.items():
                path.write_bytes(content)

    run(native_history(core, tmp_path, check))


@pytest.mark.parametrize("mutation", ["signature", "redaction", "redaction_first", "future_send"])
def test_transaction_history_still_checks_every_selected_source(
    core, tmp_path, monkeypatch, mutation,
):
    def check(job, expected):
        original = owner_input.input_recall.require_not_recalled
        target = expected[0 if mutation == "redaction_first" else 1]["event_id"]
        changed = []
        proof = owner_input.history_proof(expected)

        def recall(*args, **kwargs):
            original(*args, **kwargs)
            if changed:
                return
            changed.append(1)
            if mutation in {"redaction", "redaction_first"}:
                core.store.execute("UPDATE events SET redacted=1 WHERE event_id=?", (target,))
            elif mutation == "future_send":
                core.store.execute("UPDATE outbox SET sent_at=? WHERE origin_event_id=?",
                                   (after(core.clock(), 100), target))
            else:
                key = owner_input.INPUT + target
                row = core.store.one("SELECT value FROM runtime_meta WHERE key=?", (key,))
                value = json.loads(row["value"])
                value["capture"]["signature"] = "0" * 128
                core.store.execute("UPDATE runtime_meta SET value=? WHERE key=?",
                                   (encode(value), key))

        monkeypatch.setattr(owner_input.input_recall, "require_not_recalled", recall)
        with pytest.raises(CoreError):
            with core.store.transaction():
                owner_input.check_history(core.store, core.settings, core.clock, job, proof)
        assert changed

    run(native_history(core, tmp_path, check))
