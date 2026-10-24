"""Validate retained voice custody without current authorization or clock access.

The caller authenticates the original scope/policy, attempt, signed proof and
quiescence. Success here validates voice evidence only; it never permits sending.
"""

import hashlib
import hmac
import json

import companion_delivery_protocol as wire
from companion_delivery_protocol.authority import validate_authority_ref

from .voice_protocol import (
    LegacyVoiceDeliveryBinding,
    VoiceDeliveryBinding,
    VoiceRetiredBinding,
)


def validate_original_voice_binding(
    *, receipt, job, proof, outbox, content_sha256, original_scope, voice_policy_sha256
):
    """Read only supplied original records; expired/STOP custody remains provable.

    original_scope is exactly {authority: ref} or {window_sha256: digest}.
    voice_policy_sha256 must come from that authenticated historical policy.
    The caller loads receipt by outbox message ID and job by proof voice_check ID.
    """
    given = proof.get("voice_check")
    if given is None:
        if receipt and (receipt.get("binding") or receipt.get("retired")):
            raise ValueError("original voice send check missing")
        return
    if set(original_scope) == {"authority"}:
        validate_authority_ref(original_scope["authority"])
    elif set(original_scope) != {"window_sha256"}:
        raise ValueError("original voice scope invalid")
    retired = receipt.get("retired") if receipt else None
    given = wire.validate_voice_check(
        given, allow_legacy=bool(retired and retired.get("kind") == "legacy_signature")
    )
    if not receipt or receipt.get("state") not in {"AUDIO", "FENCED"}:
        raise ValueError("original voice binding missing")
    expected = {
        "message_id": outbox["message_id"], "delivery_key": outbox["delivery_key"],
        "event_id": proof["event_id"], "reply_revision": proof["reply_revision"],
        "voice_policy_sha256": voice_policy_sha256, **original_scope,
    }
    if retired:
        value = VoiceRetiredBinding.model_validate(retired).model_dump(mode="json")
        if (any(value.get(k) != v for k, v in expected.items())
            or receipt.get("job_id") != value["job_id"]
            or any(value[k] != given[k] for k in ("job_id", "generation"))):
            raise ValueError("retired voice identity changed")
        if (not job or job["operation"] != "tts" or any(
            job[k] != value[k] for k in ("job_id", "generation", "message_id", "event_id")
        )):
            raise ValueError("retired voice job changed")
        if value["kind"] == "capability" and "retirement_token" in given:
            material = given["retirement_token"].encode()
        elif value["kind"] == "legacy_signature" and "retirement_token" not in given:
            material = bytes.fromhex(proof["signature"])
        else:
            raise ValueError("retired voice proof kind changed")
        if not hmac.compare_digest(value["verifier"], hashlib.sha256(material).hexdigest()):
            raise ValueError("retired voice proof changed")
        return
    codec = VoiceDeliveryBinding if "authority" in original_scope else LegacyVoiceDeliveryBinding
    binding = codec.model_validate(receipt["binding"]).model_dump(mode="json")
    # A full binding requires retained original content. Erasure uses retired custody.
    content = json.loads(outbox["content_json"])
    if wire.digest(content) != content_sha256 or not isinstance(content.get("text"), str):
        raise ValueError("original voice content changed")
    reply_hash = wire.digest(content["text"])
    if proof.get("payload_kind") == "reply_sha256" and proof["payload_sha256"] != reply_hash:
        raise ValueError("original voice reply changed")
    expected.update(content_sha256=content_sha256, reply_sha256=reply_hash)
    job_expected = {
        "job_id": binding["job_id"], "generation": binding["generation"],
        "message_id": outbox["message_id"], "event_id": proof["event_id"],
        "result_sha256": binding["result_sha256"],
        "outbox_content_sha256": content_sha256,
        "outbox_delivery_key": outbox["delivery_key"],
        "source_sha256": binding["source_sha256"],
        "identity_sha256": binding["identity_sha256"],
        "captured_privacy_epoch": binding["privacy_epoch"],
        "captured_reply_revision": binding["reply_revision"],
        "node_id": binding["node_id"], "manifest_sha256": binding["manifest_sha256"],
        "config_sha256": binding["node_config_sha256"],
    }
    if "window_sha256" in original_scope:
        job_expected.update(original_scope)
    if (not job or job["operation"] != "tts"
        or job["state"] not in {"READY", "CANCELLED", "EXPIRED"}
        or any(job[k] != v for k, v in job_expected.items())
        or "authority" in original_scope
        and json.loads(job["authority_json"]) != original_scope["authority"]
        or receipt.get("job_id") != job["job_id"]
        or receipt.get("binding_sha256") != wire.digest(binding)
        or given["binding_sha256"] != wire.digest(binding)
        or any(given[k] != binding[k] for k in wire.VOICE_CHECK_FIELDS - {"binding_sha256"})
        or any(binding[k] != v for k, v in expected.items())
        or wire.timestamp(job["result_expires_at"]) != wire.timestamp(binding["expires_at"])):
        raise ValueError("original voice delivery changed")
