"""Native authority uses the original budget journal and owner decision flow."""

from copy import deepcopy

import pytest

import companion_delivery_protocol as wire
from companion import delivery_reconciliation as reconciliation
from companion import owner_authority, owner_delivery, owner_input, paid_budget
from companion.contracts import CoreError, DeliveryResult, LeaseRequest
from companion.owner_authority_protocol import AuthorityStop, OwnerSendCheck
from companion.transport import begin_send, delivery_result, lease_deliveries
from tests.test_evaluation import work
from tests.test_owner_input import captured, inbound


def pending(core, tmp_path):
    capture, key = captured(core, tmp_path)
    owner_input.approve(core.store, core.settings, core.clock, capture)
    core.ingest(inbound(core, capture), "native-unknown-source")
    assert work(core)
    item = lease_deliveries(core.store, core.settings, LeaseRequest(
        worker_id="bridge", authority=capture.authority), core.clock())["items"][0]
    check = OwnerSendCheck(authority=item["authority"], message_id=item["message_id"],
        event_id=item["origin_event_id"], content_sha256=item["content_sha256"],
        lease_token=item["lease_token"], reply_revision=item["reply_revision"])
    begin_send(core.store, item["message_id"], item["lease_token"], core.clock(),
               core.settings, clock=core.clock)
    owner_delivery.send_check(core.store, core.settings, core.clock, check)
    result = DeliveryResult(lease_token=item["lease_token"], result="DELIVERY_UNCERTAIN")
    delivery_result(core.store, core.settings, item["message_id"], result, core.clock())
    stopped = owner_authority.stop(core.store, core.settings, core.clock,
        AuthorityStop(operation_id="stop-unknown-native", authority=capture.authority))
    return delivery_proof(core, capture, key, item, check, result, stopped), key


def delivery_proof(core, capture, key, item, check, result, stopped):
    proof = {"kind": "owner_delivery_quiescence_v2", "version": 2,
        "authority": capture.authority,
        "budget_journal_id": core.store.one(
            "SELECT journal_id FROM owner_authority_revisions WHERE revision=1")["journal_id"],
        "message_id": item["message_id"], "event_id": capture.event_id,
        "action_type": item["action_type"], "fence": 1, "item_sha256": wire.digest(item),
        "content_sha256": item["content_sha256"],
        "payload_sha256": wire.digest(item["content"]["text"]),
        "payload_kind": "reply_sha256", "lease_sha256": wire.digest(item["lease_token"]),
        "send_check_sha256": wire.digest(check.model_dump(mode="json")),
        "reply_revision": item["reply_revision"], "attempt_state": "UNKNOWN",
        "attempt_record_sha256": "1" * 64, "reserved_record_sha256": "2" * 64,
        "stop_record_sha256": "3" * 64, "core_stop_record_sha256": stopped["record_sha256"],
        "bridge_journal_head_sha256": "4" * 64,
        "original_result_sha256": wire.digest(result.model_dump(mode="json")),
        "stopped_at": core.clock(), "quiesced_at": core.clock(),
        **({"voice_check": check.voice} if check.voice is not None else {})}
    proof["signature"] = key.sign(wire.signing_payload(proof)).hex()
    return proof


@pytest.mark.parametrize("outcome", ["SENT", "CANCELLED"])
@pytest.mark.parametrize("quota", [False, True])
def test_native_resolution_retains_original_evidence_and_replays(
    core, tmp_path, monkeypatch, outcome, quota,
):
    if quota:
        from tests import test_owner_input
        from tests.test_cumulative_budget import policy

        original_prepare = test_owner_input.prepare

        def configure(core, directory):
            grant = original_prepare(core, directory)
            policy(core)
            return grant.model_copy(update={
                "paid_contract_sha256": paid_budget.sha(paid_budget.snapshot(core.settings))})

        monkeypatch.setattr(test_owner_input, "prepare", configure)
    proof, _ = pending(core, tmp_path)
    usages = deepcopy(core.store.rows("SELECT * FROM model_usage"))
    before = paid_budget.journal(core.store, core.settings.paid_model,
                                 allow_settlement=True)["cumulative"]
    prepared = reconciliation.prepare(core.store, core.settings, proof, core.clock())
    assert prepared["state"] == "READY"
    result = reconciliation.resolve(core.store, core.settings, proof["message_id"], outcome,
        core.clock(), operation_id="owner-native-resolution", operation_sha256="e" * 64)
    assert wire.validate_receipt(result["receipt"])["quiescence"] == proof
    assert result["receipt"]["input_closure"]["terminal"]
    assert core.store.rows("SELECT * FROM model_usage") == usages
    receipt = reconciliation.lookup(core.store, core.settings, proof["message_id"])
    assert receipt == result["receipt"]
    after = paid_budget.journal(core.store, core.settings.paid_model,
                                allow_settlement=True)["cumulative"]
    assert before["policy_sha256"] == after["policy_sha256"]
    if quota:
        key = "send:" + wire.digest(proof["message_id"])
        assert after["operations"][key]["state"] == (
            "OWNER_SENT" if outcome == "SENT" else "OWNER_CLOSED")
        for operation, prior in before["operations"].items():
            assert {k: v for k, v in after["operations"][operation].items() if k != "state"} == {
                k: v for k, v in prior.items() if k != "state"}
        assert before["models"] == after["models"]
        assert before["policies"] == after["policies"]


@pytest.mark.parametrize("mutation", ["authority", "core_stop_record_sha256", "restored"])
def test_native_resolution_rejects_changed_binding(core, tmp_path, mutation):
    proof, key = pending(core, tmp_path)
    if mutation == "authority":
        proof["authority"] = {**proof["authority"], "revision": 2}
    elif mutation == "core_stop_record_sha256":
        proof[mutation] = "f" * 64
    else:
        with core.store.transaction():
            owner_authority.restore(core.store)
    proof["signature"] = key.sign(wire.signing_payload(proof)).hex()
    with pytest.raises(CoreError):
        reconciliation.prepare(core.store, core.settings, proof, core.clock())
    assert core.store.one("SELECT state FROM outbox WHERE message_id=?",
                           (proof["message_id"],))["state"] == "DELIVERY_UNCERTAIN"
