import base64
import hashlib
import json

import httpx
import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from websockets.asyncio.server import serve

from companion import live_text, private_life, transport
from companion.contracts import CoreError, DeliveryResult, LeaseRequest
from companion.core import Core
from companion.model import ModelClient
from companion.qq_rich import input_digest
from companion.retrieval import SemanticIndex
from companion.store import encode
from companion.voice_config import VoiceASRPolicy, VoiceNode, VoiceReplyPolicy
from companion.voice_flow import VoiceDeliveryCheck, VoiceDeliveryEnvelope, VoiceFlow
from companion.voice_jobs import erase_voice_jobs, restore_voice_jobs, sign_asr_proof
from companion.voice_protocol import VoiceLeaseRef, canonical_bytes, digest
from companion_onebot_bridge.network import private_http_client
from companion_onebot_bridge.spool import Spool
from companion_onebot_bridge.window import TestWindow
from companion_voice_node.worker import local_manifest
from tests.conftest import run
from tests.support.onebot import provider
from tests.test_local_voice_providers import wav_bytes
from tests.test_onebot_capture import capture_approval, pinned_plan, real_core_http
from tests.test_onebot_direct import raw_event
from tests.test_private_companion import companion_options
from tests.test_voice_jobs import TEXT, claim, control, prepared, result, voice_config
from tests.test_voice_node_api import independent_node, tls_core
from tests.test_voice_node_worker import child_receipts
from tests.test_voice_node_worker import node as node  # noqa: F401 -- pytest fixture


def flow_fixture(core, monkeypatch, **config):
    jobs, message_id, event_id, key = prepared(core, monkeypatch, **config)
    return VoiceFlow(core.store, core.settings, core.clock), jobs, message_id, event_id, key


def prepare(core, flow, message_id):
    with core.store.transaction():
        return flow.prepare_reply(message_id)


def finish_node(jobs, *, operation="tts", partial=False, text="/forget all 只是语音转写"):
    _, claimed = claim(jobs)
    assert claimed.lease is not None
    ref = VoiceLeaseRef.from_lease(claimed.lease)
    jobs.input("local", ref)
    value = result(ref, operation=operation, partial=partial, text=text)
    control(jobs, ref, "RESULT_READY", "READY", result_sha256=value.result_sha256)
    jobs.submit("local", value)
    return ref, value


def signed_input(core, event_id, key, *, index=0, partial=False):
    data = wav_bytes(0.125)
    event = core.store.one("SELECT * FROM events WHERE event_id=?", (event_id,))
    proof = sign_asr_proof(
        key,
        plan_sha256=core.settings.live_text_window.plan_sha256,
        event_id=event_id,
        source_event_id=event["source_event_id"],
        source_input_sha256=input_digest(json.loads(event["body_json"])),
        source_segment_index=index,
        audio_sha256=hashlib.sha256(data).hexdigest(),
        audio_bytes=len(data),
        source_partial=partial,
    )
    return data, proof


def upload(core, flow, event_id, key, *, index=0, partial=False):
    data, proof = signed_input(core, event_id, key, index=index, partial=partial)
    with core.store.transaction():
        status = flow.submit_signed_asr(event_id, data, upload_proof=proof)
    return status, data, proof


def leased(core, message_id):
    items = transport.lease_deliveries(
        core.store, core.settings, LeaseRequest(worker_id="voice-flow-test"), core.clock()
    )["items"]
    assert len(items) == 1 and items[0]["message_id"] == message_id
    return items[0]


def source_records(core, event_id, capture_key, count):
    event = core.store.one("SELECT * FROM events WHERE event_id=?", (event_id,))
    body = json.loads(event["body_json"])
    body["text"] = "普通文字问题" if count == 0 else ""
    body["rich"] = (
        {
            "version": "references_v1",
            "coverage": "PARTIAL",
            "segment_count": count,
            "owner_text_positions": [],
            "segments": [
                {"index": index, "type": "record", "actor": "UNVERIFIED", "coverage": "UNSUPPORTED"}
                for index in range(count)
            ],
        }
        if count
        else None
    )
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
    for outbox in core.store.rows("SELECT * FROM outbox WHERE origin_event_id=?", (event_id,)):
        core.store.execute(
            "UPDATE runtime_meta SET value=? WHERE key=?",
            (
                encode(private_life.delivery_material(core.store, core.settings, outbox)),
                private_life.DELIVERY + outbox["message_id"],
            ),
        )


def test_tts_single_outbox_receipt_binding_and_native_ack(core, monkeypatch):
    flow, jobs, message_id, _, _ = flow_fixture(core, monkeypatch)
    original = core.store.one("SELECT content_json,content_sha256,delivery_key FROM outbox")
    assert prepare(core, flow, message_id).state == "WAITING"
    _, value = finish_node(jobs)
    assert prepare(core, flow, message_id).state == "AUDIO"
    item = leased(core, message_id)
    with core.store.transaction():
        envelope = flow.delivery_envelope(message_id, item["lease_token"])
        flow.validate_send(
            message_id, item["lease_token"], VoiceDeliveryCheck.from_envelope(envelope)
        )
    assert envelope.audio == value.payload
    assert jobs.result(envelope.binding.job_id) is None  # generic reads cannot fetch after handoff
    assert jobs.status(envelope.binding.job_id)["state"] == "READY"
    transport.begin_send(
        core.store, message_id, item["lease_token"], core.clock(), core.settings, clock=core.clock
    )
    with core.store.transaction(), pytest.raises(CoreError, match="VOICE_DELIVERY_BINDING_CHANGED"):
        flow.validate_send(message_id, item["lease_token"], None)
    with core.store.transaction():
        flow.validate_send(
            message_id, item["lease_token"], VoiceDeliveryCheck.from_envelope(envelope)
        )
    transport.delivery_result(
        core.store,
        core.settings,
        message_id,
        DeliveryResult(
            lease_token=item["lease_token"], result="SENT", provider_message_id="synthetic-voice-1"
        ),
        core.clock(),
    )
    assert core.store.one("SELECT content_json,content_sha256,delivery_key FROM outbox") == original
    assert core.store.one("SELECT COUNT(*) AS count FROM outbox")["count"] == 1
    assert core.store.one("SELECT state FROM outbox")["state"] == "SENT"
    assert core.store.rows("SELECT * FROM model_usage") == []


@pytest.mark.parametrize(
    "change", ["audio_sha256", "binding_sha256", "result_sha256", "audio_bytes"]
)
def test_audio_send_check_rejects_tampering(core, monkeypatch, change):
    flow, jobs, message_id, _, _ = flow_fixture(core, monkeypatch)
    prepare(core, flow, message_id)
    finish_node(jobs)
    prepare(core, flow, message_id)
    item = leased(core, message_id)
    with core.store.transaction():
        envelope = flow.delivery_envelope(message_id, item["lease_token"])
    check = VoiceDeliveryCheck.from_envelope(envelope)
    wrong = check.model_copy(update={change: 44 if change == "audio_bytes" else "0" * 64})
    with core.store.transaction(), pytest.raises(CoreError, match="VOICE_DELIVERY_BINDING_CHANGED"):
        flow.validate_send(message_id, item["lease_token"], wrong)


@pytest.mark.parametrize("mode", ["offline", "running", "failed", "late_result"])
def test_local_voice_failure_falls_back_once_without_new_message(core, monkeypatch, mode):
    flow, jobs, message_id, _, _ = flow_fixture(
        core, monkeypatch, reply=VoiceReplyPolicy(wait_seconds=2)
    )
    original = core.store.one("SELECT content_json,content_sha256,delivery_key FROM outbox")
    first = prepare(core, flow, message_id)
    if mode != "offline":
        _, response = claim(jobs)
        ref = VoiceLeaseRef.from_lease(response.lease)
        jobs.input("local", ref)
        if mode == "failed":
            control(jobs, ref, "UNKNOWN", "QUARANTINED")
    core.clock.advance(3)
    if mode == "late_result":
        jobs.submit("local", result(ref))
    selected = prepare(core, flow, message_id)
    assert selected.state == "TEXT" and selected.job_id == first.job_id
    assert prepare(core, VoiceFlow(core.store, core.settings, core.clock), message_id) == selected
    item = leased(core, message_id)
    assert item["content"] == {"text": TEXT} and not item.get("voice")
    assert core.store.one("SELECT COUNT(*) AS count FROM voice_jobs")["count"] == 1
    assert core.store.one("SELECT content_json,content_sha256,delivery_key FROM outbox") == original
    assert core.store.one("SELECT input_blob,result_blob FROM voice_jobs") == {
        "input_blob": None,
        "result_blob": None,
    }


def test_qq_uncertainty_never_switches_audio_to_text(core, monkeypatch):
    flow, jobs, message_id, _, _ = flow_fixture(core, monkeypatch)
    prepare(core, flow, message_id)
    finish_node(jobs)
    prepare(core, flow, message_id)
    item = leased(core, message_id)
    with core.store.transaction(), pytest.raises(CoreError, match="VOICE_DELIVERY_CHOICE_FROZEN"):
        flow.prepare_reply(message_id)
    transport.begin_send(
        core.store, message_id, item["lease_token"], core.clock(), core.settings, clock=core.clock
    )
    transport.delivery_result(
        core.store,
        core.settings,
        message_id,
        DeliveryResult(lease_token=item["lease_token"], result="DELIVERY_UNCERTAIN"),
        core.clock(),
    )
    with core.store.transaction(), pytest.raises(CoreError, match="VOICE_DELIVERY_CHOICE_FROZEN"):
        flow.prepare_reply(message_id)
    assert (
        transport.lease_deliveries(
            core.store, core.settings, LeaseRequest(worker_id="no-replay"), core.clock()
        )["items"]
        == []
    )
    assert core.store.one("SELECT state,attempt_count FROM outbox") == {
        "state": "DELIVERY_UNCERTAIN",
        "attempt_count": 1,
    }


def test_audio_expiry_requires_release_of_old_outbox_lease(core, monkeypatch):
    flow, jobs, message_id, _, _ = flow_fixture(core, monkeypatch, result_ttl_seconds=1)
    prepare(core, flow, message_id)
    finish_node(jobs)
    prepare(core, flow, message_id)
    item = leased(core, message_id)
    core.clock.advance(2)
    with core.store.transaction(), pytest.raises(CoreError, match="VOICE_DELIVERY_EXPIRED"):
        flow.delivery_envelope(message_id, item["lease_token"])
    with core.store.transaction(), pytest.raises(CoreError, match="VOICE_DELIVERY_CHOICE_FROZEN"):
        flow.prepare_reply(message_id)
    core.clock.advance(core.settings.lease_seconds)
    with core.store.transaction():
        transport.recover_deliveries(core.store, core.clock())
        assert flow.prepare_reply(message_id).state == "TEXT"
    assert core.store.one("SELECT attempt_count FROM outbox")["attempt_count"] == 0


@pytest.mark.parametrize("policy", [VoiceReplyPolicy(enabled=False), VoiceReplyPolicy(max_chars=1)])
def test_reply_policy_does_not_speak_every_reply(core, monkeypatch, policy):
    flow, _, message_id, _, _ = flow_fixture(core, monkeypatch, reply=policy)
    assert prepare(core, flow, message_id).state == "TEXT"
    assert core.store.rows("SELECT * FROM voice_jobs") == []


def test_voice_choice_preserves_existing_picture_expression(core, monkeypatch):
    flow, _, message_id, _, _ = flow_fixture(core, monkeypatch,
                                            reply=VoiceReplyPolicy(mode="auto"))
    original = core.store.one("SELECT * FROM outbox WHERE message_id=?", (message_id,))
    content = {**json.loads(original["content_json"]), "image": {"asset_id": "existing-meme"}}
    core.store.execute("UPDATE outbox SET content_json=?,content_sha256=? WHERE message_id=?",
                       (encode(content), transport.digest(content), message_id))
    updated = core.store.one("SELECT * FROM outbox WHERE message_id=?", (message_id,))
    core.store.execute("UPDATE runtime_meta SET value=? WHERE key=?",
                       (encode(private_life.delivery_material(core.store, core.settings, updated)),
                        private_life.DELIVERY + message_id))
    assert prepare(core, flow, message_id).state == "TEXT"
    assert not core.store.rows("SELECT * FROM voice_jobs")
    current = core.store.one("SELECT * FROM outbox WHERE message_id=?", (message_id,))
    assert current["delivery_key"] == original["delivery_key"]
    assert json.loads(current["content_json"]) == content


@pytest.mark.parametrize("mode,expected", [("voice_input", "TEXT"), ("auto", "WAITING")])
def test_default_voice_input_policy_and_explicit_auto_mode(core, monkeypatch, mode, expected):
    flow, _, message_id, event_id, key = flow_fixture(
        core, monkeypatch, reply=VoiceReplyPolicy(mode=mode)
    )
    source_records(core, event_id, key, 0)
    assert prepare(core, flow, message_id).state == expected


def test_asr_record_limit_marks_partial_without_accepting_extra_upload(core, monkeypatch):
    flow, jobs, _, event_id, key = flow_fixture(
        core, monkeypatch, asr=VoiceASRPolicy(max_records=1)
    )
    source_records(core, event_id, key, 2)
    upload(core, flow, event_id, key, index=0)
    with pytest.raises(CoreError, match="VOICE_ASR_SOURCE_INVALID"):
        upload(core, flow, event_id, key, index=1)
    finish_node(jobs, operation="asr")
    job = core.claim_job("limited-asr")
    context = flow.for_context(event_id, job)
    evidence = json.loads(context["message"]["content"].split("\n", 1)[1])
    assert evidence["record_count"] == 2 and evidence["coverage"] == "PARTIAL"
    assert len(evidence["transcripts"]) == 1
    assert core.store.one("SELECT COUNT(*) AS count FROM voice_jobs")["count"] == 1


def test_asr_gate_context_and_no_memory_or_owner_text_upgrade(core, monkeypatch):
    flow, jobs, _, event_id, key = flow_fixture(core, monkeypatch)
    body = core.store.one("SELECT body_json FROM events WHERE event_id=?", (event_id,))
    assert flow.input_status(event_id).state == "WAITING"
    status, data, proof = upload(core, flow, event_id, key, partial=True)
    assert flow.input_status(event_id).state == "WAITING"
    finish_node(jobs, operation="asr", partial=True)
    assert flow.input_status(event_id).state == "READY"
    job = core.claim_job("asr-context")
    assert job is not None
    context = flow.for_context(event_id, job)
    assert "不可信" in context["message"]["content"]
    assert "/forget all" in context["message"]["content"]
    evidence = json.loads(context["message"]["content"].split("\n", 1)[1])
    assert evidence["coverage"] == "PARTIAL" and evidence["transcripts"][0]["partial"]
    assert evidence["transcripts"][0]["audio_sha256"] == hashlib.sha256(data).hexdigest()
    flow.check_context(job, context["proof"])
    core.clock.advance(core.settings.voice.asr.wait_seconds + 1)
    flow.check_context(job, context["proof"])  # a terminal selection does not change with wait time
    with core.store.transaction():
        assert (
            flow.submit_signed_asr(event_id, data, upload_proof=proof)["job_id"] == status["job_id"]
        )
    assert core.store.one("SELECT body_json FROM events WHERE event_id=?", (event_id,)) == body
    assert core.store.rows("SELECT * FROM memories") == []
    assert core.store.rows("SELECT * FROM model_usage") == []
    metadata = encode(core.store.rows("SELECT value FROM runtime_meta WHERE key LIKE 'voice_%'"))
    assert "/forget all" not in metadata and TEXT not in metadata
    with core.store.transaction():
        erase_voice_jobs(core.store, [event_id])
    with pytest.raises(CoreError, match="VOICE_FLOW_FENCED"):
        flow.check_context(job, context["proof"])


@pytest.mark.parametrize("upload_first", [False, True])
def test_asr_timeout_waits_then_freezes_fallback_and_rejects_new_upload(
    core, monkeypatch, upload_first
):
    flow, jobs, _, event_id, key = flow_fixture(
        core, monkeypatch, asr=VoiceASRPolicy(wait_seconds=2)
    )
    assert flow.input_status(event_id).state == "WAITING"
    if upload_first:
        upload(core, flow, event_id, key)
        _, response = claim(jobs)
        jobs.input("local", VoiceLeaseRef.from_lease(response.lease))
    core.clock.advance(3)
    assert flow.input_status(event_id).state == "FALLBACK"
    if upload_first:
        assert core.store.one("SELECT state FROM voice_jobs")["state"] == "UNKNOWN"
    else:
        with pytest.raises(CoreError, match="VOICE_INPUT_ALREADY_CONSUMED"):
            upload(core, flow, event_id, key)
    job = core.claim_job("asr-timeout")
    context = flow.for_context(event_id, job)
    evidence = json.loads(context["message"]["content"].split("\n", 1)[1])
    assert evidence["status"] == "UNAVAILABLE" and evidence["transcripts"] == []
    flow.check_context(job, context["proof"])


def test_asr_late_result_does_not_enter_context(core, monkeypatch):
    flow, jobs, _, event_id, key = flow_fixture(
        core, monkeypatch, asr=VoiceASRPolicy(wait_seconds=2)
    )
    upload(core, flow, event_id, key)
    core.clock.advance(3)
    finish_node(jobs, operation="asr")
    assert flow.input_status(event_id).state == "FALLBACK"
    assert core.store.one("SELECT result_blob FROM voice_jobs")["result_blob"] is None


def test_context_bound_and_result_expiry_cannot_change_existing_proof(core, monkeypatch):
    flow, jobs, _, event_id, key = flow_fixture(
        core, monkeypatch, asr=VoiceASRPolicy(max_context_chars=128), result_ttl_seconds=5
    )
    upload(core, flow, event_id, key)
    finish_node(jobs, operation="asr", text="语音" * 200)
    job = core.claim_job("bounded-asr")
    context = flow.for_context(event_id, job)
    evidence = json.loads(context["message"]["content"].split("\n", 1)[1])
    assert len(evidence["transcripts"][0]["text"]) == 128
    assert evidence["transcripts"][0]["excerpt"] and evidence["coverage"] == "PARTIAL"
    core.clock.advance(6)
    with pytest.raises(CoreError, match="VOICE_CONTEXT_RESULT_CHANGED"):
        flow.check_context(job, context["proof"])


def test_restore_invalidates_flow_metadata_even_before_audio_upload(core, monkeypatch):
    flow, _, message_id, event_id, _ = flow_fixture(core, monkeypatch)
    flow.input_status(event_id)
    prepare(core, flow, message_id)
    with core.store.transaction():
        restore_voice_jobs(core.store)
    values = core.store.rows("SELECT value FROM runtime_meta WHERE key LIKE 'voice_%'")
    assert values and all(json.loads(row["value"])["state"] == "FENCED" for row in values)
    with pytest.raises(CoreError, match="VOICE_FLOW_FENCED"):
        flow.input_status(event_id)
    with core.store.transaction(), pytest.raises(CoreError, match="VOICE_FLOW_FENCED"):
        flow.prepare_reply(message_id)


def test_even_fallback_context_is_fenced_by_privacy_epoch(core, monkeypatch):
    flow, _, _, event_id, _ = flow_fixture(core, monkeypatch, asr=VoiceASRPolicy(wait_seconds=1))
    core.clock.advance(2)
    assert flow.input_status(event_id).state == "FALLBACK"
    job = core.claim_job("fallback-proof")
    context = flow.for_context(event_id, job)
    core.store.execute("INSERT OR REPLACE INTO runtime_meta VALUES ('privacy_epoch','1')")
    with pytest.raises(CoreError, match="VOICE_CONTEXT_RESULT_CHANGED"):
        flow.check_context(job, context["proof"])


def test_real_native_asr_context_tts_outbox_send_check_and_counter_chain(
    core,
    tmp_path,
    monkeypatch,
    node,
):
    config, signing_key, counter = node
    config = config.model_copy(
        update={
            "node_id": "local",
            "request_timeout_seconds": 10.0,
            "heartbeat_seconds": 0.5,
            "execution_timeout_seconds": 20.0,
        }
    )
    core_voice = voice_config(
        signing_private_key=signing_key.private_bytes_raw().hex(),
        nodes={
            "local": VoiceNode(
                token=config.credential, manifest_sha256=digest(local_manifest(config))
            )
        },
    )
    monkeypatch.setattr(SemanticIndex, "warmup", lambda self: None)
    sends, calls = [], []

    async def source(ws):
        await ws.send(
            encode(
                raw_event(
                    1,
                    time=int(core.clock.now.timestamp()),
                    message=[
                        {"type": "text", "data": {"text": "请听我的语音"}},
                        {"type": "record", "data": {"file": "native-record"}},
                    ],
                )
            )
        )
        await ws.wait_closed()

    def model(request):
        calls.append(json.loads(request.content)["messages"])
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": TEXT}}],
                "usage": {"prompt_tokens": 100, "completion_tokens": 20},
            },
        )

    async def scenario():
        options = companion_options(core)
        options["core_options"]["voice"] = core_voice
        options.update(
            voice_scope="owner_voice_v1", voice_policy_sha256=core_voice.authority_sha256()
        )
        async with real_core_http(core) as port, serve(source, "127.0.0.1", 0) as server:
            cfg, plan = pinned_plan(
                core,
                tmp_path,
                server.sockets[0].getsockname()[1],
                core_port=port,
                live=True,
                approved=True,
                rich_input_scope="references_v1",
                scope_options=options,
            )
            async with (
                private_http_client(cfg.core_url, cfg.bridge_token) as client,
                (
                    private_http_client(
                        cfg.onebot_http_url,
                        cfg.onebot_token,
                        transport=httpx.MockTransport(lambda request: provider(request, sends)),
                    )
                ) as protocol,
                httpx.AsyncClient(transport=httpx.MockTransport(model)) as model_http,
            ):
                with Spool(cfg) as spool:
                    window = TestWindow(spool, plan, clock=lambda: core.clock.now)
                    await window.open(client, protocol)
                    await window.capture_once(client, protocol)
                    await window.approve_captured(client, capture_approval(window))
                    event = core.store.one("SELECT * FROM events WHERE source='qq.onebot'")
                    original_body = event["body_json"]
                    flow = VoiceFlow(core.store, core.settings, core.clock)
                    with Core(
                        core.settings, ModelClient(core.settings, model_http), core.clock
                    ) as worker:
                        assert worker.store is not core.store
                        assert worker.claim_job("wait-for-transcript") is None
                        assert not calls and core.store.rows("SELECT * FROM model_usage") == []
                    capture_key = Ed25519PrivateKey.from_private_bytes(
                        bytes.fromhex(cfg.capture_signing_key)
                    )
                    data, proof = signed_input(
                        core, event["event_id"], capture_key, index=1, partial=True
                    )
                    upload_path = f"/internal/v1/voice-inputs/{event['event_id']}"
                    before_state = core.store.db.total_changes
                    initial_state = await client.get(upload_path + "/state")
                    assert initial_state.status_code == 200, initial_state.text
                    assert initial_state.json()["records"] == [{
                        "index": 1, "state": "MISSING", "job_id": None,
                        "input_sha256": None, "source_proof_sha256": None,
                        "upload_allowed": True,
                    }]
                    assert initial_state.json()["input_sha256"] == proof.source_input_sha256
                    assert core.store.db.total_changes == before_state
                    unauthorized = await client.get(upload_path + "/state", headers={
                        "Authorization": "Bearer wrong-token",
                    })
                    assert unauthorized.status_code == 401
                    broken = proof.model_copy(update={"signature": "0" * 128})
                    rejected = await client.post(
                        upload_path,
                        content=data,
                        headers={
                            "Content-Type": "audio/wav",
                            "x-voice-proof": base64.urlsafe_b64encode(
                                canonical_bytes(broken)
                            ).decode(),
                        },
                    )
                    assert rejected.status_code == 403
                    assert core.store.rows("SELECT * FROM voice_jobs") == []
                    uploaded = await client.post(
                        upload_path,
                        content=data,
                        headers={
                            "Content-Type": "audio/wav",
                            "x-voice-proof": base64.urlsafe_b64encode(
                                canonical_bytes(proof)
                            ).decode(),
                        },
                    )
                    assert uploaded.status_code == 200, uploaded.text
                    before_state = core.store.db.total_changes
                    recovered = await client.get(upload_path + "/state")
                    assert recovered.status_code == 200, recovered.text
                    assert recovered.json()["records"] == [{
                        "index": 1, "state": "QUEUED", "job_id": uploaded.json()["job_id"],
                        "input_sha256": proof.audio_sha256,
                        "source_proof_sha256": digest(proof), "upload_allowed": False,
                    }]
                    assert core.store.db.total_changes == before_state
                    assert "audio_base64" not in recovered.text
                    assert "transcript" not in recovered.text
                    assert flow.input_status(event["event_id"]).state == "WAITING"
                    async with tls_core(core, tmp_path) as (origin, certificate):
                        node_config = config.model_copy(update={"core_origin": origin})
                        await independent_node(
                            node_config, certificate, counter, tmp_path, core.clock.now.timestamp()
                        )
                        with Core(
                            core.settings, ModelClient(core.settings, model_http), core.clock
                        ) as worker:
                            assert await worker.run_once("consume-voice-evidence")
                        assert len(calls) == 1 and "untrusted_transcription" in encode(calls[0])
                        assert "测试结果" in encode(calls[0])
                        original = core.store.one("SELECT * FROM outbox")
                        usage = core.store.rows(
                            "SELECT request_id,state,billed_micro_usd FROM model_usage"
                        )
                        assert len(usage) == 1 and usage[0]["state"] == "SETTLED"
                        waiting = await client.post(
                            "/internal/v1/deliveries/lease", json={"worker_id": "voice-http-bridge"}
                        )
                        assert waiting.status_code == 200 and waiting.json()["items"] == []
                        await independent_node(
                            node_config, certificate, counter, tmp_path, core.clock.now.timestamp()
                        )
                    leased_reply = await client.post(
                        "/internal/v1/deliveries/lease", json={"worker_id": "voice-http-bridge"}
                    )
                    assert leased_reply.status_code == 200
                    item = leased_reply.json()["items"][0]
                    envelope = VoiceDeliveryEnvelope.model_validate(item["voice"])
                    assert item["content"] == {"text": TEXT}
                    assert item["delivery_key"] == original["delivery_key"]
                    begun = await client.post(
                        f"/internal/v1/deliveries/{item['message_id']}/begin-send",
                        json={"lease_token": item["lease_token"]},
                    )
                    assert begun.status_code == 200
                    check = {
                        "window_sha256": plan.sha256,
                        "event_id": event["event_id"],
                        "message_id": item["message_id"],
                        "reply_sha256": transport.digest(TEXT),
                        "reply_revision": event["reply_revision"],
                        "lease_token": item["lease_token"],
                        "voice": VoiceDeliveryCheck.from_envelope(envelope).model_dump(mode="json"),
                    }
                    checked = await client.post("/internal/v1/live-text/send-check", json=check)
                    assert checked.status_code == 200, checked.text
                    wrong = dict(check)
                    wrong.pop("voice")
                    denied = await client.post("/internal/v1/live-text/send-check", json=wrong)
                    assert denied.status_code == 409
                    ack = await client.post(
                        f"/internal/v1/deliveries/{item['message_id']}/result",
                        json={
                            "lease_token": item["lease_token"],
                            "result": "SENT",
                            "provider_message_id": "synthetic-audio-ack-1",
                        },
                    )
                    assert ack.status_code == 200
                    assert core.store.one("SELECT state FROM outbox")["state"] == "SENT"
                    assert core.store.one("SELECT COUNT(*) AS count FROM outbox")["count"] == 1
                    assert (
                        core.store.one("SELECT content_sha256 FROM outbox")["content_sha256"]
                        == original["content_sha256"]
                    )
                    assert (
                        core.store.rows("SELECT request_id,state,billed_micro_usd FROM model_usage")
                        == usage
                    )
                    assert (
                        core.store.one(
                            "SELECT body_json FROM events WHERE event_id=?", (event["event_id"],)
                        )["body_json"]
                        == original_body
                    )
                    assert len(child_receipts(counter)) == 2 and not sends
                    assert core.store.rows("SELECT * FROM memories") == []

    run(scenario())
