import base64
import hashlib
import json
import uuid
from datetime import timedelta, timezone

import pytest
from pydantic import ValidationError

from companion import live_text, private_life, transport
from companion.contracts import CoreError
from companion.qq_rich import input_digest
from companion.store import encode
from companion.voice_config import VoiceConfig, VoiceNode
from companion.voice_jobs import (
    VoiceJobs,
    erase_voice_jobs,
    fence_conversation,
    restore_voice_jobs,
    sign_asr_proof,
)
from companion.voice_protocol import (
    VoiceAudioResult,
    VoiceClaimRequest,
    VoiceControlRequest,
    VoiceLeaseRef,
    VoiceTranscriptResult,
    make_voice_result,
    verify_voice_lease,
)
from companion_voice_node.providers import validate_wav
from tests.test_local_voice_providers import wav_bytes
from tests.test_qq_files import prepared as file_source

TOKEN = "independent-voice-node-credential-" + "x" * 32
TEXT = "这是一条待发送的回复。"


def voice_config(**updates):
    return VoiceConfig(
        **{
            "enabled": True,
            "signing_private_key": "61" * 32,
            "nodes": {"local": VoiceNode(token=TOKEN, manifest_sha256="b" * 64)},
        }
        | updates
    )


def prepared(core, monkeypatch, **updates):
    _, approval, capture_key, event_id, _ = file_source(core, monkeypatch)
    # Unit fixture keeps the actual signed-source, native delivery and revision checks.
    # The real entrypoint test separately runs the real window guard and capture ingest.
    row = core.store.one("SELECT * FROM events WHERE event_id=?", (event_id,))
    body = json.loads(row["body_json"])
    body.pop("shared_content", None)
    body["rich"] = {
        "version": "references_v1",
        "coverage": "PARTIAL",
        "segment_count": 1,
        "owner_text_positions": [],
        "segments": [
            {"index": 0, "type": "record", "actor": "UNVERIFIED", "coverage": "UNSUPPORTED"}
        ],
    }
    core.store.execute("UPDATE events SET body_json=? WHERE event_id=?", (encode(body), event_id))
    receipt = json.loads(
        core.store.one(
            "SELECT value FROM runtime_meta WHERE key=?", ("live_text_input:" + event_id,)
        )["value"]
    )
    receipt["input_sha256"] = receipt["capture"]["input_sha256"] = input_digest(body)
    receipt["capture"].pop("signature")
    receipt["capture"]["signature"] = capture_key.sign(
        live_text.CAPTURE_SIGNATURE_DOMAIN + encode(receipt["capture"]).encode()
    ).hex()
    core.store.execute(
        "UPDATE runtime_meta SET value=? WHERE key=?",
        (encode(receipt), "live_text_input:" + event_id),
    )
    core.settings.voice = voice_config(**updates)
    core.settings.live_text_window = approval.model_copy(
        update={
            "voice_scope": "owner_voice_v1",
            "voice_policy_sha256": core.settings.voice.authority_sha256(),
        }
    )
    message_id = transport.enqueue(core.store, event_id, row["conversation_id"], TEXT, core.clock())
    outbox = core.store.one("SELECT * FROM outbox WHERE message_id=?", (message_id,))
    core.store.insert(
        "runtime_meta",
        key=private_life.DELIVERY + message_id,
        value=encode(private_life.delivery_material(core.store, core.settings, outbox)),
    )
    jobs = VoiceJobs(core.store, core.settings, core.clock)
    return jobs, message_id, event_id, capture_key


def create_tts(core, jobs, message_id):
    with core.store.transaction():
        return jobs.create_tts(message_id, TEXT)


def create_asr(core, jobs, event_id, key, *, data=None, partial=False, proof_change=None):
    data = data if data is not None else wav_bytes(0.125)
    event = core.store.one("SELECT * FROM events WHERE event_id=?", (event_id,))
    proof = sign_asr_proof(
        key,
        plan_sha256=core.settings.live_text_window.plan_sha256,
        event_id=event_id,
        source_event_id=event["source_event_id"],
        source_input_sha256=input_digest(json.loads(event["body_json"])),
        source_segment_index=0,
        audio_sha256=hashlib.sha256(data).hexdigest(),
        audio_bytes=len(data),
        source_partial=partial,
    )
    if proof_change:
        proof = proof.model_copy(update=proof_change)
    with core.store.transaction():
        return jobs.create_asr(
            event_id, data, source_segment_index=0, upload_proof=proof, source_partial=partial
        )


def claim(jobs, **updates):
    request = VoiceClaimRequest(
        **{
            "node_id": "local",
            "request_id": "claim_" + uuid.uuid4().hex,
            "manifest_sha256": "b" * 64,
            "resource_state": "READY",
        }
        | updates
    )
    return request, jobs.claim("local", request)


def control(jobs, ref, phase="RUNNING", resource="RUNNING", **extra):
    return jobs.control(
        "local", VoiceControlRequest(ref=ref, phase=phase, resource_state=resource, **extra)
    )


def result(ref, *, operation="tts", partial=False, seconds=0.125, text="测试结果"):
    if operation == "tts":
        audio = validate_wav(wav_bytes(seconds))
        payload = VoiceAudioResult(
            audio_base64=base64.b64encode(audio.audio).decode(),
            audio_sha256=audio.sha256,
            sample_rate=audio.sample_rate,
            channels=1,
            frames=audio.frames,
            duration_seconds=audio.duration_seconds,
        )
    else:
        payload = VoiceTranscriptResult(
            text=text,
            segments=({"start_seconds": 0.0, "end_seconds": seconds, "text": text},),
            language="zh",
            language_probability=0.9,
            duration_seconds=seconds,
            partial=partial,
        )
    return make_voice_result(ref, payload)


def start(core, jobs, message_id):
    job = create_tts(core, jobs, message_id)
    request, response = claim(jobs)
    ref = VoiceLeaseRef.from_lease(response.lease)
    assert jobs.input("local", ref) == (TEXT.encode(), "text/plain")
    return job, request, response, ref


def test_config_secret_roundtrip_and_fingerprint():
    config = voice_config()
    restored = VoiceConfig.model_validate(config.model_dump(mode="python"))
    assert restored.nodes["local"].token.get_secret_value() == TOKEN
    assert restored.signing_private_key.get_secret_value() == "61" * 32
    assert TOKEN not in config.model_dump_json() and "61" * 32 not in config.model_dump_json()
    snapshot = encode(config.node_snapshot("local"))
    assert TOKEN not in snapshot and "61" * 32 not in snapshot
    assert config.node_fingerprint("local") == restored.node_fingerprint("local")
    with pytest.raises(ValidationError):
        voice_config(nodes={"a": config.nodes["local"], "b": config.nodes["local"]})
    with pytest.raises(ValidationError):
        VoiceConfig(enabled=True)


def test_tts_real_delivery_guards_idempotent_claim_result(core, monkeypatch):
    jobs, message_id, _, _ = prepared(core, monkeypatch)
    job, request, response, ref = start(core, jobs, message_id)
    assert create_tts(core, jobs, message_id)["job_id"] == job["job_id"]
    verify_voice_lease(
        response.lease,
        core.settings.voice.private_key().public_key().public_bytes_raw(),
        node_id="local",
        manifest_sha256="b" * 64,
    )
    assert jobs.claim("local", request) == response
    with pytest.raises(CoreError, match="VOICE_IDEMPOTENCY_CONFLICT"):
        jobs.claim("local", request.model_copy(update={"resource_state": "QUARANTINED"}))
    assert claim(jobs)[1].lease is None
    value = result(ref)
    assert control(
        jobs, ref, "RESULT_READY", "READY", result_sha256=value.result_sha256
    ).action == ("CONTINUE")
    receipt = jobs.submit("local", value)
    assert jobs.submit("local", value) == receipt
    assert control(jobs, ref, "RESULT_LOST", "READY").action == "COMMITTED"
    assert jobs.result(job["job_id"]) == value.payload
    assert core.store.one("SELECT state FROM outbox")["state"] == "PENDING"
    assert core.store.rows("SELECT * FROM model_usage") == []
    assert core.store.one("SELECT input_blob FROM voice_jobs")["input_blob"] is None
    with pytest.raises(CoreError, match="VOICE_RESULT_CONFLICT"):
        jobs.submit("local", result(ref, seconds=0.25))


def test_tts_requires_transaction_and_exact_pending_outbox(core, monkeypatch):
    jobs, message_id, _, _ = prepared(core, monkeypatch)
    with pytest.raises(RuntimeError, match="VOICE_TRANSACTION_REQUIRED"):
        jobs.create_tts(message_id, TEXT)
    with core.store.transaction(), pytest.raises(CoreError, match="VOICE_OUTBOX_SOURCE_REQUIRED"):
        jobs.create_tts(message_id, "unapproved text")
    core.store.execute("UPDATE outbox SET attempt_count=1")
    with pytest.raises(CoreError, match="VOICE_OUTBOX_SOURCE_REQUIRED"):
        create_tts(core, jobs, message_id)
    core.store.execute("UPDATE outbox SET attempt_count=0")
    core.store.execute(
        "DELETE FROM runtime_meta WHERE key=?", (private_life.DELIVERY + message_id,)
    )
    with pytest.raises(CoreError, match="COMPANION_DELIVERY_SOURCE_CHANGED"):
        create_tts(core, jobs, message_id)


@pytest.mark.parametrize("change", ["epoch", "revision", "source", "receipt", "config", "outbox"])
def test_running_fences_wipe_and_commit_before_rejecting_input(core, monkeypatch, change):
    jobs, message_id, event_id, _ = prepared(core, monkeypatch)
    job, _, _, ref = start(core, jobs, message_id)
    if change == "epoch":
        core.store.execute("INSERT OR REPLACE INTO runtime_meta VALUES ('privacy_epoch','1')")
    elif change == "revision":
        core.store.execute("UPDATE conversations SET reply_revision=reply_revision+1")
    elif change == "source":
        core.store.execute("UPDATE events SET redacted=1 WHERE event_id=?", (event_id,))
    elif change == "receipt":
        core.store.execute(
            "DELETE FROM runtime_meta WHERE key=?", (private_life.DELIVERY + message_id,)
        )
    elif change == "config":
        core.settings.voice = voice_config(job_ttl_seconds=91)
    else:
        core.store.execute("UPDATE outbox SET state='DELIVERY_UNCERTAIN',attempt_count=1")
    with pytest.raises(CoreError, match="VOICE_INPUT_FENCED"):
        jobs.input("local", ref)
    row = core.store.one("SELECT * FROM voice_jobs")
    assert row["state"] == "UNKNOWN" and row["input_blob"] is row["result_blob"] is None
    assert jobs.result(job["job_id"]) is None
    assert create_tts(core, jobs, message_id)["job_id"] == job["job_id"]
    with pytest.raises(CoreError, match="VOICE_RESULT_FENCED"):
        jobs.submit("local", result(ref))


def test_lease_expiry_responsibility_and_node_quarantine(core, monkeypatch):
    jobs, message_id, _, _ = prepared(core, monkeypatch)
    job, request, response, ref = start(core, jobs, message_id)
    core.clock.advance(31)
    assert jobs.status(job["job_id"])["state"] == "UNKNOWN"
    assert jobs.claim("local", request) == response
    assert (
        core.store.one("SELECT active_job_id FROM voice_node_state")["active_job_id"]
        == (job["job_id"])
    )
    assert control(jobs, ref, "UNKNOWN", "QUARANTINED").action == "EXPIRED"
    assert core.store.one("SELECT resource_state FROM voice_node_state")["resource_state"] == (
        "QUARANTINED"
    )
    assert claim(jobs)[1].lease is None
    assert control(jobs, ref, "UNKNOWN", "READY").action == "EXPIRED"
    assert core.store.one("SELECT active_job_id FROM voice_node_state")["active_job_id"] is None
    assert create_tts(core, jobs, message_id)["state"] == "UNKNOWN"


@pytest.mark.parametrize("started", [False, True])
@pytest.mark.parametrize("hook", ["erase", "restore", "revision"])
def test_erasure_restore_and_revision_hooks_never_requeue(core, monkeypatch, started, hook):
    jobs, message_id, event_id, _ = prepared(core, monkeypatch)
    job = create_tts(core, jobs, message_id)
    _, response = claim(jobs)
    ref = VoiceLeaseRef.from_lease(response.lease)
    if started:
        jobs.input("local", ref)
    with core.store.transaction():
        for _ in range(2):
            if hook == "erase":
                erase_voice_jobs(core.store, [event_id])
            elif hook == "restore":
                restore_voice_jobs(core.store)
            else:
                row = core.store.one(
                    "SELECT conversation_id FROM events WHERE event_id=?", (event_id,)
                )
                fence_conversation(core.store, row["conversation_id"])
    assert jobs.status(job["job_id"])["state"] == ("UNKNOWN" if started else "CANCELLED")
    assert jobs.result(job["job_id"]) is None
    assert create_tts(core, jobs, message_id)["job_id"] == job["job_id"]
    with pytest.raises(CoreError, match="VOICE_RESULT_FENCED"):
        jobs.submit("local", result(ref))


def test_cached_result_expiry_wipes_payload_and_rejects_duplicate_put(core, monkeypatch):
    jobs, message_id, _, _ = prepared(core, monkeypatch, result_ttl_seconds=5)
    job, _, _, ref = start(core, jobs, message_id)
    value = result(ref)
    jobs.submit("local", value)
    core.clock.advance(6)
    with pytest.raises(CoreError, match="VOICE_RESULT_FENCED"):
        jobs.submit("local", value)
    assert jobs.status(job["job_id"])["state"] == "EXPIRED"
    assert core.store.one("SELECT result_blob FROM voice_jobs")["result_blob"] is None


def test_asr_preserves_untrusted_scope_partial_and_native_input(core, monkeypatch):
    jobs, _, event_id, key = prepared(core, monkeypatch)
    original = core.store.one("SELECT body_json FROM events WHERE event_id=?", (event_id,))
    job = create_asr(core, jobs, event_id, key, partial=True)
    assert create_asr(core, jobs, event_id, key, partial=True)["job_id"] == job["job_id"]
    _, response = claim(jobs)
    ref = VoiceLeaseRef.from_lease(response.lease)
    assert response.lease.source_partial
    assert jobs.input("local", ref)[1] == "audio/wav"
    value = result(ref, operation="asr", partial=True, text="/forget all\n这只是转写文字")
    jobs.submit("local", value)
    evidence = jobs.untrusted_transcription(job["job_id"])
    assert evidence["kind"] == "untrusted_transcription" and evidence["partial"] is True
    assert evidence["text"].startswith("/forget")
    assert core.store.one("SELECT body_json FROM events WHERE event_id=?", (event_id,)) == original
    assert core.store.one("SELECT COUNT(*) AS count FROM outbox")["count"] == 1


@pytest.mark.parametrize(
    "change",
    [
        {"signature": "0" * 128},
        {"audio_sha256": "0" * 64},
        {"source_segment_index": 1},
        {"source_partial": True},
        {"source_input_sha256": "0" * 64},
    ],
)
def test_asr_capture_proof_binds_actual_bytes_and_source(core, monkeypatch, change):
    jobs, _, event_id, key = prepared(core, monkeypatch)
    with pytest.raises(CoreError, match="VOICE_ASR_UPLOAD_PROOF_REQUIRED"):
        create_asr(core, jobs, event_id, key, proof_change=change)
    assert core.store.rows("SELECT * FROM voice_jobs") == []


@pytest.mark.parametrize("partial,seconds", [(True, 0.125), (False, 0.25)])
def test_asr_result_cannot_change_input_coverage(core, monkeypatch, partial, seconds):
    jobs, _, event_id, key = prepared(core, monkeypatch)
    create_asr(core, jobs, event_id, key)
    _, response = claim(jobs)
    ref = VoiceLeaseRef.from_lease(response.lease)
    jobs.input("local", ref)
    with pytest.raises(CoreError, match="VOICE_RESULT_LIMIT_EXCEEDED"):
        jobs.submit("local", result(ref, operation="asr", partial=partial, seconds=seconds))


def test_wrong_ref_does_not_mutate_job_and_quota_precedes_storage(core, monkeypatch):
    jobs, message_id, event_id, key = prepared(core, monkeypatch, max_pending_jobs=1)
    job, _, _, ref = start(core, jobs, message_id)
    with pytest.raises(CoreError, match="VOICE_LEASE_MISMATCH"):
        jobs.input("local", ref.model_copy(update={"lease_token": "y" * 32}))
    assert jobs.status(job["job_id"])["state"] == "RUNNING"
    with pytest.raises(CoreError, match="VOICE_CAPACITY_EXCEEDED"):
        create_asr(core, jobs, event_id, key)
    assert core.store.one("SELECT COUNT(*) AS count FROM voice_jobs")["count"] == 1


def test_tts_requires_intact_native_capture_and_voice_scope(core, monkeypatch):
    jobs, message_id, event_id, _ = prepared(core, monkeypatch)
    core.store.execute("DELETE FROM runtime_meta WHERE key=?", ("live_text_input:" + event_id,))
    with pytest.raises(CoreError, match="LIVE_SOURCE_CHANGED"):
        create_tts(core, jobs, message_id)
    core.settings.live_text_window = core.settings.live_text_window.model_copy(
        update={"voice_scope": "none", "voice_policy_sha256": None}
    )
    with pytest.raises(CoreError, match="VOICE_SCOPE_REQUIRED"):
        create_tts(core, jobs, message_id)


def test_rotated_policy_claim_persists_revocation(core, monkeypatch):
    jobs, message_id, _, _ = prepared(core, monkeypatch)
    job, _, _, _ = start(core, jobs, message_id)
    core.settings.voice = voice_config(lease_ttl_seconds=31)
    with pytest.raises(CoreError, match="VOICE_SCOPE_REQUIRED"):
        claim(jobs)
    row = core.store.one(
        "SELECT state,input_blob,result_blob FROM voice_jobs WHERE job_id=?", (job["job_id"],)
    )
    assert row == {"state": "UNKNOWN", "input_blob": None, "result_blob": None}


def test_erasure_withdraws_completed_audio_and_keeps_only_metadata(core, monkeypatch):
    jobs, message_id, event_id, _ = prepared(core, monkeypatch)
    job, _, _, ref = start(core, jobs, message_id)
    value = result(ref)
    jobs.submit("local", value)
    with core.store.transaction():
        erase_voice_jobs(core.store, [event_id])
    assert jobs.status(job["job_id"])["state"] == "CANCELLED"
    assert jobs.result(job["job_id"]) is None
    row = core.store.one("SELECT * FROM voice_jobs")
    assert row["result_sha256"] == value.result_sha256
    assert row["input_blob"] is row["result_blob"] is None
    audit = encode(
        core.store.rows("SELECT safe_detail_json FROM audit_log WHERE action LIKE 'voice.%'")
    )
    assert TOKEN not in audit and TEXT not in audit and value.payload.audio_base64 not in audit
    with pytest.raises(CoreError, match="VOICE_RESULT_FENCED"):
        jobs.submit("local", value)


def test_lease_deadline_uses_actual_window_end_across_timezones(core, monkeypatch):
    jobs, message_id, _, _ = prepared(core, monkeypatch)
    end = core.clock.now + timedelta(seconds=7)
    core.settings.live_text_window = core.settings.live_text_window.model_copy(
        update={"ends_at": end.astimezone(timezone(timedelta(hours=8)))}
    )
    job = create_tts(core, jobs, message_id)
    _, claimed = claim(jobs)
    assert claimed.lease.job_deadline_at == end
    assert claimed.lease.lease_expires_at == end
    core.clock.advance(7)
    assert jobs.status(job["job_id"])["state"] == "EXPIRED"
