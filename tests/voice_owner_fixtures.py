"""Signed OwnerAuthority voice input; no window or replacement permission guards."""

import hashlib

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from companion import owner_input, paid_budget
from companion.contracts import InboundEvent
from companion.owner_authority_protocol import OwnerCapture, OwnerGrantPolicy, OwnerRuntimeConfig
from companion.qq_rich import input_digest
from companion.store import after
from companion_delivery_protocol.authority import capture_signing_payload
from tests.conftest import event
from tests.test_owner_authority import activate
from tests.test_paid_model_gate import approved_live
from tests.test_voice_jobs import voice_config


def native_voice(core, tmp_path, *, record=True, expires_seconds=None, voice=True):
    approved_live(core, tmp_path)
    core.settings.rich_input_scope = "references_v1"
    core.settings.voice = voice_config(reply={"mode": "auto"})
    core.settings.owner_runtime = OwnerRuntimeConfig(mode="ENABLED")
    key = Ed25519PrivateKey.generate()
    identity = paid_budget.sha([core.settings.owner_qq_id, core.settings.bot_qq_id])
    policy = OwnerGrantPolicy(
        owner_qq_id=core.settings.owner_qq_id, purposes=["chat", "voice"] if voice else ["chat"],
        authorization_sha256="d" * 64,
        paid_contract_sha256=paid_budget.sha(paid_budget.snapshot(core.settings)),
        capability_policies={"voice": core.settings.voice.authority_sha256()} if voice else {},
        capture={"public_key": key.public_key().public_bytes_raw().hex(),
                 "identity_sha256": identity, "protocol_epoch": "voice-owner-native"},
        expires_at=after(core.clock(), expires_seconds) if expires_seconds is not None else None,
    )
    result, _ = activate(core, policy)
    source = (f"{core.settings.bot_qq_id}:onebot-direct:FriendMessage:"
              f"{core.settings.owner_qq_id}:epoch:voice-owner-native:123")
    message = event(core, text="" if record else "synthetic voice reply request",
                    origin="onebot-direct:FriendMessage:" + core.settings.owner_qq_id).model_dump(
                        mode="json")
    message.update(event_id="evt_" + hashlib.sha256(source.encode()).hexdigest(),
                   source_event_id=source)
    if record:
        message["payload"]["rich"] = {
            "version": "references_v1", "coverage": "PARTIAL", "segment_count": 1,
            "owner_text_positions": [], "segments": [{"index": 0, "type": "record",
                                                       "actor": "UNVERIFIED",
                                                       "coverage": "UNSUPPORTED"}],
        }
    incoming = InboundEvent.model_validate(message)
    value = dict(authority=result["authority"], kind="authenticated_onebot_ws",
                 source_event_id=source, event_id=message["event_id"],
                 input_sha256=input_digest(message["payload"]), frame_sha256="b" * 64,
                 evidence_sha256="c" * 64, identity_sha256=identity,
                 protocol_epoch="voice-owner-native", occurred_at=core.clock(),
                 received_at=core.clock())
    value["signature"] = key.sign(capture_signing_payload(value)).hex()
    capture = OwnerCapture.model_validate(value)
    owner_input.approve(core.store, core.settings, core.clock, capture)
    accepted = core.ingest(incoming, "native-voice-source")
    return capture, key, accepted["job_id"]
