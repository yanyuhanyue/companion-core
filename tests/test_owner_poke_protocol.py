"""Core and the independent Bridge sign the same finite native poke proof."""

import copy

import pytest
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from pydantic import ValidationError

from companion.poke_protocol import OwnerPokeCapture, PokeCapture, signed_payload
from companion_delivery_protocol.poke import owner_poke_signing_payload


def receipt():
    return {"kind": "authenticated_onebot_poke", "authority": {
        "version": 2, "grant_id": "grant_" + "1" * 32, "revision": 1,
        "policy_sha256": "2" * 64, "restore_generation": "initial"},
        "event_id": "evt_" + "3" * 64, "source_event_id": "native-poke-source",
        "notice": {"time": 1792872000, "self_id": "10002", "user_id": "10001",
            "sender_id": "10001", "target_id": "10002", "action": "拍了拍", "suffix": ""},
        "protocol_epoch": "native-poke-epoch", "identity_sha256": "4" * 64,
        "config_sha256": "5" * 64, "frame_sha256": "6" * 64, "evidence_sha256": "7" * 64,
        "occurred_at": "2026-10-25T04:00:00+08:00", "received_at": "2026-10-25T04:00:01+08:00"}


def test_native_signature_interoperates_without_upgrading_a_legacy_receipt():
    key, value = Ed25519PrivateKey.generate(), receipt()
    signature = key.sign(owner_poke_signing_payload(value))
    capture = OwnerPokeCapture.model_validate({**value, "signature": signature.hex()})
    assert signed_payload(capture) == owner_poke_signing_payload(value)
    key.public_key().verify(signature, signed_payload(capture))
    legacy = {key: val for key, val in capture.model_dump(mode="json").items()
              if key != "authority"} | {"window_sha256": "8" * 64}
    old_capture = PokeCapture.model_validate(legacy)
    with pytest.raises(InvalidSignature):
        key.public_key().verify(signature, signed_payload(old_capture))
    with pytest.raises(ValidationError):
        OwnerPokeCapture.model_validate(legacy)


@pytest.mark.parametrize("change", ["revision", "sender", "epoch", "evidence", "config"])
def test_signed_native_proof_cannot_change_its_original_binding(change):
    key, value = Ed25519PrivateKey.generate(), receipt()
    signature = key.sign(owner_poke_signing_payload(value))
    changed = copy.deepcopy(value)
    if change == "revision":
        changed["authority"]["revision"] += 1
    elif change == "sender":
        changed["notice"]["sender_id"] = "20001"
    elif change == "epoch":
        changed["protocol_epoch"] += "2"
    else:
        changed[change + "_sha256"] = "9" * 64
    with pytest.raises(InvalidSignature):
        key.public_key().verify(signature, owner_poke_signing_payload(changed))


@pytest.mark.parametrize("change", ["legacy", "group", "bool_time", "naive", "control"])
def test_wire_codec_rejects_ambiguous_capture_material(change):
    value = receipt()
    if change == "legacy":
        value["window_sha256"] = "8" * 64
    elif change == "group":
        value["notice"]["group_id"] = "20001"
    elif change == "bool_time":
        value["notice"]["time"] = True
    elif change == "naive":
        value["received_at"] = "2026-10-25T04:00:01"
    else:
        value["notice"]["suffix"] = "\n"
    with pytest.raises(ValueError):
        owner_poke_signing_payload(value)
