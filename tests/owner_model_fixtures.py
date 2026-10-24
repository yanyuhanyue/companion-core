"""Signed native chat custody for isolated model/admission boundary checks."""

import hashlib

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from companion import owner_input, paid_budget
from companion.owner_authority_protocol import OwnerCapture, OwnerGrantPolicy, OwnerRuntimeConfig
from companion.qq_rich import input_digest
from companion_delivery_protocol.authority import capture_signing_payload
from tests.conftest import event
from tests.test_owner_authority import activate


def authorized_job(core, *, lease_seconds=None):
    """Keep the test's paid policy and return one genuinely admitted, leased job."""
    if core.settings.mode != "live":
        return None
    if hasattr(core, "authorized_model_test_job"):
        return core.authorized_model_test_job
    core.settings.owner_runtime = OwnerRuntimeConfig(mode="ENABLED")
    key = Ed25519PrivateKey.generate()
    identity = paid_budget.sha([core.settings.owner_qq_id, core.settings.bot_qq_id])
    policy = OwnerGrantPolicy(
        owner_qq_id=core.settings.owner_qq_id, purposes=["chat"], authorization_sha256="d" * 64,
        paid_contract_sha256=paid_budget.sha(paid_budget.snapshot(core.settings)),
        capture={"public_key": key.public_key().public_bytes_raw().hex(),
                 "identity_sha256": identity, "protocol_epoch": "model-boundary-test"},
    )
    active, _ = activate(core, policy)
    source = (f"{core.settings.bot_qq_id}:onebot-direct:FriendMessage:"
              f"{core.settings.owner_qq_id}:epoch:model-boundary-test:1")
    event_id = "evt_" + hashlib.sha256(source.encode()).hexdigest()
    text = "synthetic model boundary source"
    value = dict(authority=active["authority"], kind="authenticated_onebot_ws",
                 source_event_id=source, event_id=event_id,
                 input_sha256=input_digest({"text": text}),
                 frame_sha256="b" * 64, evidence_sha256="c" * 64, identity_sha256=identity,
                 protocol_epoch="model-boundary-test", occurred_at=core.clock(),
                 received_at=core.clock())
    value["signature"] = key.sign(capture_signing_payload(value)).hex()
    capture = OwnerCapture.model_validate(value)
    owner_input.approve(core.store, core.settings, core.clock, capture)
    incoming = event(core, text=text, origin="onebot-direct:FriendMessage:10001").model_copy(
        update={"event_id": event_id, "source_event_id": source})
    admitted = core.ingest(incoming, "model-boundary-source")
    original_lease = core.settings.lease_seconds
    try:
        if lease_seconds is not None:
            core.settings.lease_seconds = lease_seconds
        job = core.claim_job("model-boundary-test", job_id=admitted["job_id"])
    finally:
        core.settings.lease_seconds = original_lease
    assert job is not None
    core.authorized_model_test_job = job["job_id"]
    return job["job_id"]
