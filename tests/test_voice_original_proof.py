import copy
import hashlib
import json

import pytest

import companion_delivery_protocol as wire
from companion.voice_original_proof import validate_original_voice_binding
from companion.voice_protocol import LegacyVoiceDeliveryBinding, VoiceDeliveryBinding


def original(*, legacy=False, retired=False):
    scope = {"window_sha256": "a" * 64} if legacy else {"authority": {
        "version": 2, "grant_id": "grant_" + "a" * 32, "revision": 1,
        "policy_sha256": "b" * 64, "restore_generation": "original",
    }}
    content = {"text": "original reply"}
    binding = dict(
        retirement_token="r" * 32, job_id="voice_" + "a" * 32, generation=1,
        message_id="msg_" + "a" * 32, delivery_key="original-delivery",
        event_id="original-event", source_sha256="a" * 64, identity_sha256="b" * 64,
        privacy_epoch=0, reply_revision=1, node_id="original-node",
        manifest_sha256="c" * 64, node_config_sha256="d" * 64,
        voice_policy_sha256="e" * 64, content_sha256=wire.digest(content),
        reply_sha256=hashlib.sha256(content["text"].encode()).hexdigest(),
        result_sha256="f" * 64, audio_sha256="a" * 64, audio_bytes=44,
        expires_at="2000-01-01T00:00:00Z", **scope,
    )
    codec = LegacyVoiceDeliveryBinding if legacy else VoiceDeliveryBinding
    binding = codec.model_validate(binding).model_dump(mode="json")
    given = {k: binding[k] for k in wire.VOICE_CHECK_FIELDS - {"binding_sha256"}}
    given["binding_sha256"] = wire.digest(binding)
    receipt = dict(state="AUDIO", binding=binding, job_id=binding["job_id"],
                   binding_sha256=wire.digest(binding))
    job = {k: binding[k] for k in (
        "job_id", "generation", "message_id", "event_id", "result_sha256",
        "source_sha256", "identity_sha256", "node_id", "manifest_sha256",
    )}
    job.update(operation="tts", state="EXPIRED", outbox_content_sha256=binding["content_sha256"],
               outbox_delivery_key=binding["delivery_key"], captured_privacy_epoch=0,
               captured_reply_revision=1, config_sha256=binding["node_config_sha256"],
               result_expires_at=binding["expires_at"],
               authority_json=json.dumps(scope.get("authority", {})))
    if legacy:
        job.update(scope)
    if retired:
        receipt = dict(state="FENCED", job_id=binding["job_id"], retired={
            **{k: binding[k] for k in ("job_id", "generation", "message_id", "event_id",
                "delivery_key", "voice_policy_sha256", "reply_revision")}, **scope,
            "kind": "capability", "verifier": hashlib.sha256(b"r" * 32).hexdigest(),
        })
        job["authority_json"] = "{}"  # retirement erases execution authority
    return dict(receipt=receipt, job=job,
                proof=dict(voice_check=given, event_id=binding["event_id"], reply_revision=1),
                outbox=dict(message_id=binding["message_id"], delivery_key=binding["delivery_key"],
                            content_json=json.dumps(content) if not retired else "{}"),
                content_sha256=binding["content_sha256"], original_scope=scope,
                voice_policy_sha256=binding["voice_policy_sha256"])


@pytest.mark.parametrize("legacy", [False, True])
@pytest.mark.parametrize("retired", [False, True])
def test_original_custody_survives_expiry_and_retirement_without_live_authority(legacy, retired):
    records = original(legacy=legacy, retired=retired)
    before = copy.deepcopy(records)
    validate_original_voice_binding(**records)
    assert records == before


@pytest.mark.parametrize("retired", [False, True])
@pytest.mark.parametrize("change", ["authority", "policy", "job", "token", "missing"])
def test_original_custody_rejects_changed_evidence(retired, change):
    records = original(retired=retired)
    if change == "authority":
        records["original_scope"] = copy.deepcopy(records["original_scope"])
        records["original_scope"]["authority"]["revision"] = 2
    elif change == "policy":
        records["voice_policy_sha256"] = "0" * 64
    elif change == "job":
        records["job"]["generation"] = 2
    elif change == "token":
        records["proof"]["voice_check"]["retirement_token"] = "s" * 32
    else:
        records["proof"].pop("voice_check")
    with pytest.raises(ValueError):
        validate_original_voice_binding(**records)


@pytest.mark.parametrize("change", ["content", "bytes", "result", "expiry", "job_authority"])
def test_full_binding_rejects_changed_original_material(change):
    records = original()
    if change == "content":
        records["outbox"]["content_json"] = json.dumps({"text": "changed"})
    elif change == "bytes":
        records["proof"]["voice_check"]["audio_bytes"] = 45
    elif change == "result":
        records["job"]["result_sha256"] = "0" * 64
    elif change == "expiry":
        records["job"]["result_expires_at"] = "2001-01-01T00:00:00Z"
    else:
        records["job"]["authority_json"] = "{}"
    with pytest.raises(ValueError):
        validate_original_voice_binding(**records)


def test_legacy_signature_retirement_is_original_signature_bound():
    records = original(legacy=True, retired=True)
    records["proof"]["voice_check"].pop("retirement_token")
    records["proof"]["signature"] = "ab" * 64
    records["receipt"]["retired"].update(
        kind="legacy_signature", verifier=hashlib.sha256(bytes.fromhex("ab" * 64)).hexdigest())
    validate_original_voice_binding(**records)
    records["proof"]["signature"] = "cd" * 64
    with pytest.raises(ValueError, match="retired voice proof changed"):
        validate_original_voice_binding(**records)
