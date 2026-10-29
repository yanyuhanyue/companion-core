"""Billing evidence appends once; execution uncertainty and its original custody survive."""

import copy
import hashlib
import json
import pickle
import subprocess
import sys
from pathlib import Path

import pytest

from companion import budget, model_accounting, owner_authority, paid_budget
from companion.contracts import CoreError
from companion.owner_authority_protocol import AuthorityStop
from companion.store import encode
from tests.model_carry_support import MESSAGES, migrated
from tests.owner_model_fixtures import authorized_job
from tests.test_model_carry_lifecycle import current_unknown
from tests.test_owner_authority import activate


def proposal(tmp_path, request_id, *, operation="estimate-first", basis=None):
    private = tmp_path / (operation + ".evidence")
    private.write_bytes(("private-synthetic-billing-evidence:" + operation).encode())
    return {
        "version": 1, "operation_id": operation, "request_id": request_id,
        "authorization_sha256": "a" * 64,
        "evidence_sha256": hashlib.sha256(private.read_bytes()).hexdigest(),
        "basis": basis or {"kind": "owner_estimate", "reference_provider": "reference",
            "reference_model": "reference-text", "currency": "USD",
            "input_rate": 2_000_000, "output_rate": 4_000_000},
    }, private


def prepared(core, tmp_path, request_id, **options):
    value, private = proposal(tmp_path, request_id, **options)
    return model_accounting.preview(core.store, core.settings, value, private, core.clock)[
        "prepared_request"], private


def journal(core):
    return Path(core.settings.paid_model.journal_path)


def snapshot(core):
    return list(core.store.db.iterdump()), journal(core).read_bytes()


def custody(core):
    with core.store.transaction():
        return paid_budget.journal(core.store, core.settings.paid_model, allow_settlement=True)


def test_estimate_keeps_unknown_counts_and_original_settlement_and_is_idempotent(core, tmp_path):
    case = migrated(core, tmp_path)
    before = snapshot(core)
    request, private = prepared(core, tmp_path, case.request_id)
    assert snapshot(core) == before
    original = core.store.one("SELECT * FROM model_usage WHERE request_id=?", (case.request_id,))
    native = custody(core)["model_latest"][case.request_id]
    first = model_accounting.record(core.store, core.settings, request, private, core.clock)
    assert first["billing_status"] == "ESTIMATED" and first["model_outcome"] == "UNKNOWN"
    assert first["accounted_micro_usd"] > original["billed_micro_usd"]
    after = core.store.one("SELECT * FROM model_usage WHERE request_id=?", (case.request_id,))
    assert after == original | {"billed_micro_usd": first["accounted_micro_usd"]}
    assert custody(core)["model_latest"][case.request_id] == native
    recorded = snapshot(core)
    assert model_accounting.record(core.store, core.settings, request, private, core.clock) == first
    assert snapshot(core) == recorded
    assert private.read_bytes() not in journal(core).read_bytes()
    assert core.store.one("SELECT COUNT(*) n FROM model_usage")["n"] == 1
    cost = core.store.one("SELECT state,accounted_micro_usd FROM cloud_cost_snapshots "
                          "WHERE reference_type='model' AND reference_id=?", (case.request_id,))
    assert cost == {"state": "UNKNOWN", "accounted_micro_usd": first["accounted_micro_usd"]}
    with pytest.raises(CoreError):
        budget.reserve(core.store, core.settings, case.job_id, "chat", MESSAGES, core.clock())


def test_provider_statement_follows_estimate_without_refunding_or_inventing_response(
    core, tmp_path,
):
    case = migrated(core, tmp_path)
    request, private = prepared(core, tmp_path, case.request_id)
    first = model_accounting.record(core.store, core.settings, request, private, core.clock)
    bill = {"kind": "provider_statement", "provider": "offline-fixture", "model": "offline-text",
            "currency": "USD", "actual_micro_usd": 0, "provider_record_sha256": "b" * 64}
    request, private = prepared(core, tmp_path, case.request_id,
                                operation="provider-final", basis=bill)
    confirmed = model_accounting.record(core.store, core.settings, request, private, core.clock)
    assert confirmed["billing_status"] == "PROVIDER_CONFIRMED"
    assert confirmed["basis_micro_usd"] == 0
    assert confirmed["accounted_micro_usd"] == first["accounted_micro_usd"]
    assert core.store.one("SELECT state,is_estimate FROM model_usage")["state"] == "UNKNOWN"
    assert core.store.one("SELECT is_estimate FROM model_usage")["is_estimate"] == 1
    before = snapshot(core)
    with pytest.raises(CoreError):
        prepared(core, tmp_path, case.request_id, operation="estimate-after-final")
    assert snapshot(core) == before


def test_provider_record_and_operation_cannot_be_reused_for_different_material(core, tmp_path):
    case = migrated(core, tmp_path)
    bill = {"kind": "provider_statement", "provider": "offline-fixture", "model": "offline-text",
            "currency": "USD", "actual_micro_usd": 5000, "provider_record_sha256": "b" * 64}
    request, private = prepared(core, tmp_path, case.request_id, basis=bill)
    result = model_accounting.record(core.store, core.settings, request, private, core.clock)
    assert result["basis_micro_usd"] == result["accounted_micro_usd"] == 5000
    changed = copy.deepcopy(request)
    changed["basis"]["actual_micro_usd"] += 1
    before = snapshot(core)
    with pytest.raises(CoreError, match="MODEL_ACCOUNTING_OPERATION_CONFLICT"):
        model_accounting.record(core.store, core.settings, changed, private, core.clock)
    assert snapshot(core) == before
    other = current_unknown(core)
    before = snapshot(core)
    with pytest.raises(CoreError):
        prepared(core, tmp_path, other, operation="same-provider-record", basis=bill)
    assert snapshot(core) == before


def test_duplicate_evidence_does_not_create_another_operation(core, tmp_path):
    case = migrated(core, tmp_path)
    value, private = proposal(tmp_path, case.request_id)
    request = model_accounting.preview(core.store, core.settings, value, private, core.clock)[
        "prepared_request"]
    model_accounting.record(core.store, core.settings, request, private, core.clock)
    before = snapshot(core)
    value["operation_id"] = "duplicate-evidence"
    with pytest.raises(CoreError):
        model_accounting.preview(core.store, core.settings, value, private, core.clock)
    assert snapshot(core) == before


@pytest.mark.parametrize("change", ["evidence", "head", "binding", "amount", "provider",
                                    "closure", "version", "extra", "restore", "cumulative",
                                    "currency", "boolean_price"])
def test_invalid_accounting_material_cannot_mutate_state(core, tmp_path, change):
    case = migrated(core, tmp_path)
    request, private = prepared(core, tmp_path, case.request_id)
    if change == "evidence":
        private.write_bytes(b"changed evidence")
    elif change == "head":
        current_unknown(core)
    elif change == "binding":
        request["binding_sha256"] = "0" * 64
    elif change == "amount":
        request["previous_accounted_micro_usd"] = 0
    elif change == "provider":
        request["basis"] = {"kind": "provider_statement", "provider": "wrong-route",
            "model": "offline-text", "currency": "USD", "actual_micro_usd": 1,
            "provider_record_sha256": "b" * 64}
    elif change == "closure":
        request["closure_sha256"] = "0" * 64
    elif change == "version":
        request["version"] = True
    elif change == "extra":
        request["credential"] = "must-not-be-accepted"
    elif change == "restore":
        core.store.execute("INSERT INTO runtime_meta VALUES ('model_budget_restore_required','1')")
    elif change == "cumulative":
        core.store.execute("INSERT INTO runtime_meta VALUES ('model_budget_cumulative','{}')")
    elif change == "currency":
        request["basis"]["currency"] = "CNY"
    else:
        request["basis"]["input_rate"] = True
    before = snapshot(core)
    with pytest.raises(CoreError):
        model_accounting.record(core.store, core.settings, request, private, core.clock)
    assert snapshot(core) == before


def test_no_operator_entry_can_close_an_unfinished_transport(core, tmp_path):
    migrated(core, tmp_path)
    job = authorized_job(core, number=2)
    request = budget.reserve(core.store, core.settings, job, "chat", MESSAGES, core.clock())
    budget.settle(core.store, core.settings, request)
    before = snapshot(core)
    with pytest.raises(CoreError):
        prepared(core, tmp_path, request)
    assert snapshot(core) == before


def test_only_identical_operation_repairs_fsync_before_projection_crash(
    core, tmp_path, monkeypatch,
):
    case = migrated(core, tmp_path)
    request, private = prepared(core, tmp_path, case.request_id)
    native = core.store.execute

    def crash(sql, params=()):
        if sql.startswith("UPDATE model_usage SET billed_micro_usd=MAX"):
            raise OSError("synthetic DB write failure after journal fsync")
        return native(sql, params)

    monkeypatch.setattr(core.store, "execute", crash)
    with pytest.raises(OSError):
        model_accounting.record(core.store, core.settings, request, private, core.clock)
    monkeypatch.setattr(core.store, "execute", native)
    durable = journal(core).read_bytes()
    with pytest.raises(CoreError, match="MODEL_BUDGET_RECONCILIATION_REQUIRED"):
        custody(core)
    changed = copy.deepcopy(request)
    changed["authorization_sha256"] = "c" * 64
    with pytest.raises(CoreError):
        model_accounting.record(core.store, core.settings, changed, private, core.clock)
    result = model_accounting.record(core.store, core.settings, request, private, core.clock)
    assert result["state"] == "RECORDED" and journal(core).read_bytes() == durable
    assert len(custody(core)["model_accounting"]) == 1


def test_old_journal_prefix_and_dropped_projection_fail_closed(core, tmp_path):
    case = migrated(core, tmp_path)
    old = journal(core).read_bytes()
    request, private = prepared(core, tmp_path, case.request_id)
    model_accounting.record(core.store, core.settings, request, private, core.clock)
    current = journal(core).read_bytes()
    journal(core).write_bytes(old)
    with pytest.raises(CoreError, match="MODEL_BUDGET_RECONCILIATION_REQUIRED"):
        custody(core)
    journal(core).write_bytes(current)
    core.store.execute("DELETE FROM runtime_meta WHERE key=?",
                       (model_accounting.PROJECTION + request["operation_id"],))
    with pytest.raises(CoreError, match="MODEL_BUDGET_RECONCILIATION_REQUIRED"):
        custody(core)


def test_rehashed_receipt_cannot_change_native_amount_formula(core, tmp_path):
    case = migrated(core, tmp_path)
    request, private = prepared(core, tmp_path, case.request_id)
    model_accounting.record(core.store, core.settings, request, private, core.clock)
    rows = [json.loads(line) for line in journal(core).read_text(encoding="utf-8").splitlines()]
    rows[-1]["accounted_micro_usd"] -= 1
    rows[-1]["sha256"] = paid_budget.sha({k: v for k, v in rows[-1].items() if k != "sha256"})
    journal(core).write_text("".join(encode(row) + "\n" for row in rows), encoding="utf-8")
    core.store.execute("UPDATE runtime_meta SET value=? WHERE key=?", (rows[-1]["sha256"],
                       model_accounting.PROJECTION + request["operation_id"]))
    with pytest.raises(CoreError, match="MODEL_BUDGET_RECONCILIATION_REQUIRED"):
        custody(core)
def test_accounting_retains_erasure_and_owner_stop_semantics(core, tmp_path):
    case = migrated(core, tmp_path)
    request, private = prepared(core, tmp_path, case.request_id)
    model_accounting.record(core.store, core.settings, request, private, core.clock)
    original = core.store.one("SELECT event_id FROM jobs WHERE job_id=?", (case.job_id,))
    with core.store.transaction():
        core.ledger.expire_events(core.store, [original["event_id"]], core.clock())
    assert core.store.one("SELECT redacted FROM events WHERE event_id=?",
                          (original["event_id"],))["redacted"] == 1
    assert len(custody(core)["model_accounting"]) == 1
    owner_authority.stop(core.store, core.settings, core.clock, AuthorityStop(
        operation_id="stop-after-accounting", authority=case.successor["authority"]))
    before = snapshot(core)
    assert model_accounting.record(core.store, core.settings, request, private, core.clock)[
        "billing_status"] == "ESTIMATED"
    assert snapshot(core) == before
    assert owner_authority._latest(core.store)["state"] == "STOPPED"


def test_same_policy_activation_keeps_repriced_original_liability(core, tmp_path):
    case = migrated(core, tmp_path)
    request, private = prepared(core, tmp_path, case.request_id)
    result = model_accounting.record(core.store, core.settings, request, private, core.clock)
    owner_authority.stop(core.store, core.settings, core.clock, AuthorityStop(
        operation_id="stop-repriced-carry", authority=case.successor["authority"]))
    activate(core, case.policy, revision=4, operation="resume-repriced-carry")
    assert core.store.one("SELECT billed_micro_usd,state FROM model_usage") == {
        "billed_micro_usd": result["accounted_micro_usd"], "state": "UNKNOWN"}


def test_real_cli_in_independent_process_previews_and_records_once(core, tmp_path):
    case = migrated(core, tmp_path)
    value, private = proposal(tmp_path, case.request_id)
    settings = tmp_path / "settings.pickle"
    settings.write_bytes(pickle.dumps(core.settings))
    request = tmp_path / "request.json"
    script = """import pickle,sys
from companion.config import Settings
from companion.__main__ import main
with open(sys.argv.pop(1),'rb') as stream:cfg=pickle.load(stream)
Settings.load=classmethod(lambda cls,**kwargs:cfg)
main()
"""

    def execute(command, data):
        request.write_text(encode(data), encoding="utf-8")
        result = subprocess.run([sys.executable, "-B", "-c", script, str(settings), command,
            "--request", str(request), "--request-sha256",
            hashlib.sha256(request.read_bytes()).hexdigest(), "--evidence", str(private)],
            capture_output=True, text=True, timeout=30)
        assert result.returncode == 0, result.stderr
        return json.loads(result.stdout)

    before = snapshot(core)
    value = execute("preview-model-accounting", value)["prepared_request"]
    assert snapshot(core) == before
    first = execute("record-model-accounting", value)
    recorded = snapshot(core)
    assert execute("record-model-accounting", value) == first
    assert snapshot(core) == recorded
