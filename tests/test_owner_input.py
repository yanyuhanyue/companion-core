"""Versioned signatures prove provenance without reviving stopped execution."""

import hashlib
from datetime import datetime

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from companion import owner_authority, owner_input, paid_budget
from companion.contracts import CoreError
from companion.owner_authority_protocol import AuthorityStop, OwnerCapture, OwnerGrantPolicy
from companion.qq_rich import input_digest
from companion.store import encode
from companion_delivery_protocol.authority import CAPTURE_DOMAIN
from tests.test_owner_authority import activate, prepare


def captured(core, tmp_path):
    policy = prepare(core, tmp_path)
    key = Ed25519PrivateKey.generate()
    policy = OwnerGrantPolicy.model_validate({**policy.model_dump(), "purposes": ["chat"],
        "capture": {"public_key": key.public_key().public_bytes_raw().hex(),
                    "identity_sha256": paid_budget.sha(
                        [core.settings.owner_qq_id, core.settings.bot_qq_id]),
                    "protocol_epoch": "synthetic-epoch"}})
    result, _ = activate(core, policy)
    source = (f"{core.settings.bot_qq_id}:onebot-direct:FriendMessage:"
              f"{core.settings.owner_qq_id}:epoch:synthetic-epoch:123")
    value = dict(authority=result["authority"], kind="authenticated_onebot_ws",
                 source_event_id=source,
                 event_id="evt_" + hashlib.sha256(source.encode()).hexdigest(),
                 input_sha256=input_digest({"text": "synthetic owner input"}),
                 frame_sha256="b" * 64, evidence_sha256="c" * 64,
                 identity_sha256=policy.capture.identity_sha256,
                 protocol_epoch=policy.capture.protocol_epoch,
                 occurred_at=datetime.fromisoformat(core.clock()).isoformat(),
                 received_at=datetime.fromisoformat(core.clock()).isoformat())
    value["signature"] = key.sign(CAPTURE_DOMAIN + encode(value).encode()).hex()
    return OwnerCapture.model_validate(value), key


def test_capture_admission_is_idempotent_and_signature_bound(core, tmp_path):
    capture, _ = captured(core, tmp_path)
    assert owner_input.approve(core.store, core.settings, core.clock, capture) == {
        "approved": True, "duplicate": False}
    assert owner_input.approve(core.store, core.settings, core.clock, capture)["duplicate"]
    altered = capture.model_copy(update={"input_sha256": "f" * 64})
    with pytest.raises(CoreError, match="OWNER_CAPTURE_INVALID"):
        owner_input.approve(core.store, core.settings, core.clock, altered)


def test_v1_domain_cannot_sign_v2_capture(core, tmp_path):
    capture, key = captured(core, tmp_path)
    value = capture.model_dump(mode="json", exclude={"signature"})
    signature = key.sign(b"companion-core/onebot-capture/v1\x00" + encode(value).encode()).hex()
    with pytest.raises(CoreError, match="OWNER_CAPTURE_INVALID"):
        owner_input.approve(core.store, core.settings, core.clock,
                            capture.model_copy(update={"signature": signature}))


@pytest.mark.parametrize("boundary", ["stop", "restore"])
def test_history_verification_does_not_reauthorize_capture(core, tmp_path, boundary):
    capture, _ = captured(core, tmp_path)
    owner_input.approve(core.store, core.settings, core.clock, capture)
    if boundary == "stop":
        owner_authority.stop(core.store, core.settings, core.clock, AuthorityStop(
            operation_id="operation-stop-capture", authority=capture.authority))
    else:
        owner_authority.restore(core.store)
    assert owner_input.verify(core.store, core.settings, capture.model_dump(mode="json"))[0] == (
        capture)
    with pytest.raises(CoreError, match="OWNER_AUTHORITY_DISABLED"):
        owner_input.approve(core.store, core.settings, core.clock, capture)


def test_erasure_removes_capture_and_prevents_its_readmission(core, tmp_path):
    capture, _ = captured(core, tmp_path)
    owner_input.approve(core.store, core.settings, core.clock, capture)
    with core.store.transaction():
        core.ledger.expire_events(core.store, [capture.event_id], core.clock())
    assert not core.store.one("SELECT 1 FROM runtime_meta WHERE key=?",
                               (owner_input.INPUT + capture.event_id,))
    with pytest.raises(CoreError, match="EVENT_ERASED"):
        owner_input.approve(core.store, core.settings, core.clock, capture)


def test_explicit_history_scope_can_read_original_signed_source_after_revision(core, tmp_path):
    from tests.conftest import event

    capture, _ = captured(core, tmp_path)
    owner_input.approve(core.store, core.settings, core.clock, capture)
    inbound = event(core, text="synthetic owner input", origin="onebot-direct:FriendMessage:10001")
    inbound = inbound.model_copy(update={"event_id": capture.event_id,
                                          "source_event_id": capture.source_event_id})
    admitted = core.ingest(inbound, "synthetic-owner-source")
    with core.store.transaction():
        owner_authority.bind_job(core.store, core.settings, core.clock,
                                  admitted["job_id"], purpose="chat")
    original = owner_authority.current(core.store, core.settings, core.clock)
    assert owner_input.source(core.store, core.settings, original, capture.event_id,
                              core.clock())[1]["text"] == "synthetic owner input"
    core.store.execute("UPDATE jobs SET state='CANCELLED'")
    policy = OwnerGrantPolicy.model_validate({**original.policy.model_dump(),
        "purposes": ["chat", "history"], "history": {"scope": "owner_private",
            "not_before": core.clock(), "max_turns": 8, "max_utf8_bytes": 8192}})
    activate(core, policy, revision=1, operation="operation-history-revision")
    current = owner_authority.current(core.store, core.settings, core.clock)
    with pytest.raises(CoreError, match="OWNER_SOURCE_CHANGED"):
        owner_input.source(core.store, core.settings, current, capture.event_id, core.clock())
    assert owner_input.source(core.store, core.settings, current, capture.event_id,
                              core.clock(), history=True)[0]["event_id"] == capture.event_id
    with core.store.transaction():
        core.ledger.expire_events(core.store, [capture.event_id], core.clock())
    with pytest.raises(CoreError, match="OWNER_SOURCE_CHANGED"):
        owner_input.source(core.store, core.settings, current, capture.event_id,
                            core.clock(), history=True)
