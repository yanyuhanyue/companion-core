"""A TTS-only node must not strand an authenticated owner voice input."""

import base64
import json

import httpx
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from websockets.asyncio.server import serve

from companion.core import Core
from companion.model import ModelClient
from companion.retrieval import SemanticIndex
from companion.store import encode
from companion.voice_config import VoiceNode
from companion.voice_jobs import INPUT_RECEIPT
from companion.voice_protocol import canonical_bytes
from companion_onebot_bridge.network import private_http_client
from companion_onebot_bridge.spool import Spool
from companion_onebot_bridge.window import TestWindow
from tests.conftest import run
from tests.support.onebot import provider
from tests.test_onebot_capture import capture_approval, pinned_plan, real_core_http
from tests.test_onebot_direct import raw_event
from tests.test_private_companion import companion_options
from tests.test_voice_flow import signed_input
from tests.test_voice_jobs import TOKEN, voice_config


def test_tts_only_node_projects_unavailable_asr_and_independent_worker_replies(
    core, tmp_path, monkeypatch,
):
    # Keep the actual window, signed capture, provider admission, source proof,
    # and independent worker boundaries. Only embedding warmup and external I/O
    # are substituted; no guard or model-admission function is bypassed.
    monkeypatch.setattr(SemanticIndex, "warmup", lambda self: None)
    voice = voice_config(nodes={
        "tts-only": VoiceNode(token=TOKEN, manifest_sha256="b" * 64, capabilities=("tts",)),
    })
    assert voice.asr.enabled
    calls, sends = [], []

    async def source(ws):
        await ws.send(encode(raw_event(
            1, time=int(core.clock.now.timestamp()),
            message=[{"type": "text", "data": {"text": "请听我的语音"}},
                     {"type": "record", "data": {"file": "native-record"}}],
        )))
        await ws.wait_closed()

    def model(request):
        calls.append(json.loads(request.content)["messages"])
        return httpx.Response(200, json={
            "choices": [{"message": {"content": "这条语音暂时无法转写，请补充文字。"}}],
            "usage": {"prompt_tokens": 100, "completion_tokens": 20},
        })

    async def scenario():
        options = companion_options(core)
        options["core_options"]["voice"] = voice
        options.update(voice_scope="owner_voice_v1", voice_policy_sha256=voice.authority_sha256())
        async with real_core_http(core) as port, serve(source, "127.0.0.1", 0) as server:
            cfg, plan = pinned_plan(
                core, tmp_path, server.sockets[0].getsockname()[1], core_port=port,
                live=True, approved=True, rich_input_scope="references_v1", scope_options=options,
            )
            async with (
                private_http_client(cfg.core_url, cfg.bridge_token) as client,
                private_http_client(cfg.onebot_http_url, cfg.onebot_token,
                    transport=httpx.MockTransport(lambda request: provider(request, sends)))
                    as protocol,
                httpx.AsyncClient(transport=httpx.MockTransport(model)) as model_http,
            ):
                with Spool(cfg) as spool:
                    window = TestWindow(spool, plan, clock=lambda: core.clock.now)
                    await window.open(client, protocol)
                    await window.capture_once(client, protocol)
                    await window.approve_captured(client, capture_approval(window))
                    event = core.store.one("SELECT * FROM events WHERE source='qq.onebot'")
                    original_body = event["body_json"]
                    path = "/internal/v1/voice-inputs/" + event["event_id"]
                    before = core.store.db.total_changes
                    state = await client.get(path + "/state")
                    assert state.status_code == 200, state.text
                    assert state.json()["state"] == "FALLBACK"
                    assert state.json()["records"] == [{
                        "index": 1, "state": "MISSING", "job_id": None,
                        "input_sha256": None, "source_proof_sha256": None,
                        "upload_allowed": False,
                    }]
                    assert core.store.db.total_changes == before
                    assert not core.store.one("SELECT 1 FROM runtime_meta WHERE key=?",
                        (INPUT_RECEIPT + event["event_id"],))
                    assert "audio_base64" not in state.text and "transcript" not in state.text

                    key = Ed25519PrivateKey.from_private_bytes(
                        bytes.fromhex(cfg.capture_signing_key))
                    data, proof = signed_input(core, event["event_id"], key, index=1)
                    rejected = await client.post(path, content=data, headers={
                        "Content-Type": "audio/wav",
                        "x-voice-proof": base64.urlsafe_b64encode(canonical_bytes(proof)).decode(),
                    })
                    assert rejected.status_code in {403, 409}, rejected.text
                    assert not core.store.rows("SELECT * FROM voice_jobs")
                    assert not core.store.rows("SELECT * FROM model_usage")

                    with Core(core.settings, ModelClient(core.settings, model_http),
                              core.clock) as worker:
                        assert worker.store is not core.store
                        assert await worker.run_once("tts-only-asr-fallback")
                    assert len(calls) == 1
                    evidence = [json.loads(message["content"].split("\n", 1)[1])
                                for message in calls[0]
                                if isinstance(message["content"], str)
                                and '"kind":"untrusted_transcription"' in message["content"]]
                    assert len(evidence) == 1
                    assert evidence[0]["status"] == "UNAVAILABLE"
                    assert evidence[0]["transcripts"] == []
                    assert evidence[0]["unavailable"] == [{
                        "segment_index": 1, "reason": "VOICE_ASR_NODE_UNAVAILABLE",
                    }]
                    assert evidence[0]["coverage"] == "PARTIAL"
                    assert core.store.one("SELECT state FROM jobs WHERE event_id=?",
                                           (event["event_id"],))["state"] == "SUCCEEDED"
                    assert core.store.rows("SELECT scenario,state FROM model_usage") == [
                        {"scenario": "chat", "state": "SETTLED"},
                    ]
                    assert len(core.store.rows("SELECT * FROM outbox")) == 1
                    assert not core.store.rows("SELECT * FROM voice_jobs")
                    assert core.store.one("SELECT body_json FROM events WHERE event_id=?",
                                           (event["event_id"],))["body_json"] == original_body
                    assert not sends
                    before = core.store.db.total_changes
                    settled = await client.get(path + "/state")
                    assert settled.status_code == 200, settled.text
                    assert settled.json()["state"] == "FALLBACK"
                    assert all(not item["upload_allowed"] for item in settled.json()["records"])
                    assert core.store.db.total_changes == before
                    with Core(core.settings, ModelClient(core.settings, model_http),
                              core.clock) as restarted:
                        assert not await restarted.run_once("tts-only-restart")
                    assert len(calls) == 1

    run(scenario())
