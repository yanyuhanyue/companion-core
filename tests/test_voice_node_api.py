"""Node boundary tests and real TLS/WS/Core entrypoint with independent local processes."""

import asyncio
import base64
import hashlib
import ipaddress
import json
import os
import socket
import ssl
import subprocess
import sys
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

import httpx
import pytest
import uvicorn
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.x509.oid import NameOID
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from websockets.asyncio.server import serve

from companion.api import create_app
from companion.contracts import CoreError
from companion.core import Core
from companion.model import ModelClient
from companion.qq_rich import input_digest
from companion.retrieval import SemanticIndex
from companion.store import encode
from companion.voice_config import VoiceASRPolicy, VoiceNode
from companion.voice_jobs import VoiceJobs, sign_asr_proof
from companion.voice_node_api import register_voice_node_routes
from companion.voice_protocol import (
    MAX_RESULT_BODY_BYTES,
    ROUTE_PREFIX,
    VoiceClaimResponse,
    VoiceLeaseRef,
    canonical_bytes,
    digest,
)
from companion_onebot_bridge.network import private_http_client
from companion_onebot_bridge.spool import Spool
from companion_onebot_bridge.window import TestWindow
from companion_voice_node.worker import VoiceNodeConfig, VoiceNodeWorker, local_manifest
from tests.conftest import run
from tests.support.onebot import provider
from tests.test_local_voice_providers import wav_bytes
from tests.test_onebot_capture import capture_approval, pinned_plan, real_core_http
from tests.test_onebot_direct import raw_event
from tests.test_private_companion import companion_options
from tests.test_voice_jobs import TOKEN, create_tts, prepared, result, voice_config
from tests.test_voice_node_worker import child_receipts, factory
from tests.test_voice_node_worker import node as node  # noqa: F401 -- pytest fixture


def claim_body(core):
    session = VoiceJobs(core.store, core.settings, core.clock).session("local")
    return {
        "node_id": "local",
        "incarnation": session.incarnation,
        "sequence": session.last_sequence + 1,
        "manifest_sha256": "b" * 64,
        "resource_state": "READY",
    }


def app_for(core, *, boundary_only=False):
    if boundary_only:
        app = FastAPI()

        @app.exception_handler(CoreError)
        async def error(request, exception):
            return JSONResponse({"error": exception.code}, status_code=exception.status)

        register_voice_node_routes(app)
    else:
        app = create_app(core.settings)
        assert ROUTE_PREFIX + "/claim" in app.openapi()["paths"]
    app.state.core = core
    return app


def test_api_auth_idempotency_path_and_result_receipt(core, monkeypatch):
    jobs, message_id, _, _ = prepared(core, monkeypatch)
    job = create_tts(core, jobs, message_id)

    async def scenario():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app_for(core)), base_url="http://test"
        ) as client:
            body = claim_body(core)
            headers = {
                "Authorization": "Bearer " + TOKEN,
                "Idempotency-Key": f"voice-claim:{body['incarnation']}:{body['sequence']}",
            }
            for token in (
                "wrong-credential-" + "x" * 32,
                core.settings.bridge_token.get_secret_value(),
            ):
                response = await client.post(
                    ROUTE_PREFIX + "/claim",
                    json=body,
                    headers=headers | {"Authorization": "Bearer " + token},
                )
                assert response.status_code == 401
            assert core.store.rows("SELECT * FROM voice_node_state") == []
            assert (
                await client.post(
                    ROUTE_PREFIX + "/claim", json=body, headers={"Authorization": "Bearer " + TOKEN}
                )
            ).status_code == 400
            claimed = await client.post(ROUTE_PREFIX + "/claim", json=body, headers=headers)
            assert claimed.status_code == 200 and claimed.headers["cache-control"] == "no-store"
            # Normalized DTO hashing makes key replay independent of wire whitespace/key order.
            replayed = await client.post(
                ROUTE_PREFIX + "/claim",
                content=json.dumps(dict(reversed(list(body.items()))), indent=2),
                headers=headers | {"Content-Type": "application/json"},
            )
            assert replayed.json() == claimed.json()
            ref = VoiceLeaseRef.from_lease(VoiceClaimResponse.model_validate(claimed.json()).lease)
            path = ROUTE_PREFIX + "/jobs/" + job["job_id"]
            bad = await client.post(
                ROUTE_PREFIX + "/jobs/voice_" + "f" * 32 + "/input",
                json=ref.model_dump(),
                headers=headers,
            )
            assert bad.status_code == 409
            assert jobs.status(job["job_id"])["state"] == "LEASED"
            source = await client.post(path + "/input", json=ref.model_dump(), headers=headers)
            assert source.status_code == 200 and source.headers["cache-control"] == "no-store"
            value = result(ref)
            ready = await client.post(
                path + "/control",
                json={
                    "ref": ref.model_dump(),
                    "phase": "RESULT_READY",
                    "resource_state": "READY",
                    "result_sha256": value.result_sha256,
                },
                headers=headers,
            )
            assert ready.json()["action"] == "CONTINUE"
            headers["Idempotency-Key"] = f"voice-result:{ref.job_id}:{ref.generation}"
            accepted = await client.put(
                path + "/result",
                content=canonical_bytes(value),
                headers=headers | {"Content-Type": "application/json"},
            )
            assert accepted.status_code == 200 and accepted.json()["status"] == "COMMITTED"
            again = await client.put(
                path + "/result", json=value.model_dump(mode="json"), headers=headers
            )
            assert again.json() == accepted.json()
            assert jobs.result(job["job_id"]) == value.payload

    run(scenario())


@pytest.mark.parametrize("reserved", ["bridge_token", "control_token", "diary_admin_token"])
def test_voice_credential_cannot_alias_owner_bridge_or_admin(core, monkeypatch, reserved):
    prepared(core, monkeypatch)
    from pydantic import SecretStr

    setattr(core.settings, reserved, SecretStr(TOKEN))

    async def scenario():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app_for(core)), base_url="http://test"
        ) as client:
            body = claim_body(core)
            response = await client.post(
                ROUTE_PREFIX + "/claim",
                json=body,
                headers={
                    "Authorization": "Bearer " + TOKEN,
                    "Idempotency-Key": f"voice-claim:{body['incarnation']}:{body['sequence']}",
                },
            )
            assert response.status_code == 401
            assert core.store.rows("SELECT * FROM voice_node_state") == []

    run(scenario())


def test_boundary_auth_precedes_body_read_and_stream_limit_is_enforced(core, monkeypatch):
    prepared(core, monkeypatch)

    async def scenario():
        reads = []

        async def oversized():
            reads.append(True)
            yield b"x" * (MAX_RESULT_BODY_BYTES + 1)

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app_for(core, boundary_only=True)),
            base_url="http://test",
        ) as client:
            path = ROUTE_PREFIX + "/jobs/voice_" + "a" * 32 + "/result"
            denied = await client.put(
                path, content=oversized(), headers={"Content-Type": "application/json"}
            )
            assert denied.status_code == 401 and not reads
            response = await client.put(
                path,
                content=oversized(),
                headers={"Content-Type": "application/json", "Authorization": "Bearer " + TOKEN},
            )
            assert response.status_code == 413 and len(reads) == 1
            duplicate = await client.post(
                ROUTE_PREFIX + "/claim",
                content=b'{"node_id":1,"node_id":2}',
                headers={"Content-Type": "application/json", "Authorization": "Bearer " + TOKEN},
            )
            assert duplicate.status_code == 400 and "node_id" not in duplicate.text

    run(scenario())


@pytest.mark.parametrize("change", ["cancelled", "revision", "source", "scope", "fenced_epoch"])
def test_disabled_asr_input_state_preserves_source_authority(core, monkeypatch, change):
    _, _, event_id, _ = prepared(core, monkeypatch, asr=VoiceASRPolicy(enabled=False))

    async def scenario():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app_for(core)), base_url="http://test",
            headers={"Authorization": "Bearer " + core.settings.bridge_token.get_secret_value()},
        ) as client:
            path = f"/internal/v1/voice-inputs/{event_id}/state"
            initial = await client.get(path)
            assert initial.status_code == 200, initial.text
            assert initial.json()["state"] == "FALLBACK"
            assert not initial.json()["records"][0]["upload_allowed"]
            if change == "cancelled":
                core.store.execute("UPDATE jobs SET state='CANCELLED' WHERE event_id=?",
                                   (event_id,))
            elif change == "revision":
                core.store.execute("UPDATE conversations SET reply_revision=reply_revision+1")
            elif change == "source":
                event = core.store.one("SELECT body_json FROM events WHERE event_id=?", (event_id,))
                body = json.loads(event["body_json"])
                body["text"] = "source changed after capture"
                core.store.execute("UPDATE events SET body_json=? WHERE event_id=?",
                                   (encode(body), event_id))
            elif change == "scope":
                core.settings.live_text_window = core.settings.live_text_window.model_copy(
                    update={"voice_scope": "none"})
            else:
                event = core.store.one("SELECT * FROM events WHERE event_id=?", (event_id,))
                value = core.voice._input_authority(event) | {"state": "FENCED"}
                core.store.execute("INSERT OR REPLACE INTO runtime_meta VALUES (?,?)",
                                   ("voice_input:" + event_id, encode(value)))
                core.store.execute(
                    "INSERT OR REPLACE INTO runtime_meta VALUES ('privacy_epoch','1')")
            before = core.store.db.total_changes
            denied = await client.get(path)
            assert denied.status_code in {403, 409}, denied.text
            assert core.store.db.total_changes == before
            assert not core.store.rows("SELECT * FROM voice_jobs")
            assert not core.store.rows("SELECT * FROM model_usage")

    run(scenario())


def tls_material(tmp_path):
    private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "synthetic voice core")])
    now = datetime.now(UTC)
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(subject)
        .public_key(private.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(days=1))
        .not_valid_after(now + timedelta(days=1))
        .add_extension(
            x509.SubjectAlternativeName([x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]),
            critical=False,
        )
        .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
        .sign(private, hashes.SHA256())
    )
    cert_path, key_path = tmp_path / "core-cert.pem", tmp_path / "core-key.pem"
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(
        private.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    return cert_path, key_path


@asynccontextmanager
async def tls_core(core, tmp_path):
    cert, key = tls_material(tmp_path)
    ready = asyncio.Event()

    class Server(uvicorn.Server):
        async def startup(self, sockets=None):
            await super().startup(sockets=sockets)
            ready.set()

    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    server = Server(
        uvicorn.Config(
            app_for(core),
            lifespan="off",
            log_config=None,
            access_log=False,
            ssl_certfile=str(cert),
            ssl_keyfile=str(key),
        )
    )
    task = asyncio.create_task(server.serve(sockets=[listener]))
    try:
        await asyncio.wait_for(ready.wait(), 5)
        yield f"https://127.0.0.1:{listener.getsockname()[1]}", cert
    finally:
        server.should_exit = True
        await asyncio.wait_for(task, 5)
        listener.close()


async def independent_node(config, certificate, counter, tmp_path, timestamp, scenario="success"):
    config_path = tmp_path / "node-fixture.json"
    config_path.write_text(
        encode(
            config.model_dump(mode="json") | {"credential": config.credential.get_secret_value()}
        ),
        encoding="utf-8",
    )
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-B",
        "-m",
        "tests.test_voice_node_api",
        "--node-fixture",
        str(config_path),
        str(certificate),
        str(counter),
        str(timestamp),
        scenario,
        cwd=str(config.workspace),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
    try:
        stdout, stderr = await asyncio.wait_for(process.communicate(), 45)
        assert process.returncode == 0, stderr.decode("utf-8", errors="replace")
        receipt = json.loads(stdout)
        assert receipt["pid"] != os.getpid() and receipt["outcome"] == "COMPLETED"
    finally:
        if process.returncode is None:
            process.kill()
            await process.wait()
        config_path.unlink(missing_ok=True)


@pytest.mark.parametrize("operation", ["tts", "asr"])
def test_real_core_tls_native_authority_and_independent_node(
    core, tmp_path, monkeypatch, node, operation
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
        asr=VoiceASRPolicy(enabled=operation != "tts"),
        nodes={
            "local": VoiceNode(
                token=config.credential, manifest_sha256=digest(local_manifest(config))
            )
        },
    )
    monkeypatch.setattr(SemanticIndex, "warmup", lambda self: None)
    sends, model_calls = [], []

    async def source(ws):
        await ws.send(
            encode(
                raw_event(
                    1,
                    time=int(core.clock.now.timestamp()),
                    message=[
                        {"type": "text", "data": {"text": "请听这段话"}},
                        {"type": "record", "data": {"file": "native-opaque-record"}},
                    ],
                )
            )
        )
        await ws.wait_closed()

    def model(request):
        model_calls.append(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": "合成语音链路回复。"}}],
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
                        transport=httpx.MockTransport(lambda r: provider(r, sends)),
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
                    jobs = VoiceJobs(core.store, core.settings, core.clock)
                    data = wav_bytes(0.125)
                    proof = sign_asr_proof(
                        Ed25519PrivateKey.from_private_bytes(bytes.fromhex(cfg.capture_signing_key)),
                        plan_sha256=plan.sha256,
                        event_id=event["event_id"],
                        source_event_id=event["source_event_id"],
                        source_input_sha256=input_digest(json.loads(event["body_json"])),
                        source_segment_index=1,
                        audio_sha256=hashlib.sha256(data).hexdigest(),
                        audio_bytes=len(data),
                        source_partial=True,
                    )
                    if operation == "tts":
                        path = f"/internal/v1/voice-inputs/{event['event_id']}"
                        state_before = core.store.db.total_changes
                        receipt = await client.get(path + "/state")
                        assert receipt.status_code == 200, receipt.text
                        assert receipt.json()["state"] == "FALLBACK"
                        assert receipt.json()["records"] == [{
                            "index": 1, "state": "MISSING", "job_id": None,
                            "input_sha256": None, "source_proof_sha256": None,
                            "upload_allowed": False,
                        }]
                        assert core.store.db.total_changes == state_before
                        unauthorized = await client.get(path + "/state", headers={
                            "Authorization": "Bearer wrong-token",
                        })
                        assert unauthorized.status_code == 401
                        proof_header = base64.urlsafe_b64encode(canonical_bytes(proof)).decode()
                        rejected = await client.post(path, content=data, headers={
                            "Content-Type": "audio/wav",
                            "x-voice-proof": proof_header,
                        })
                        assert rejected.status_code == 403, rejected.text
                        assert "VOICE_ASR_DISABLED" in rejected.text
                        assert core.store.db.total_changes == state_before
                        assert not core.store.rows("SELECT * FROM voice_jobs")
                        assert not core.store.rows("SELECT * FROM model_usage")
                        with Core(
                            core.settings, ModelClient(core.settings, model_http), core.clock
                        ) as worker:
                            assert worker.store is not core.store
                            assert await worker.run_once("voice-native-chat")
                        outbox = core.store.one("SELECT * FROM outbox")
                        with core.store.transaction():
                            job = jobs.create_tts(
                                outbox["message_id"], json.loads(outbox["content_json"])["text"]
                            )
                    else:
                        with core.store.transaction():
                            job = jobs.create_asr(
                                event["event_id"],
                                data,
                                source_segment_index=1,
                                upload_proof=proof,
                                source_partial=True,
                            )
                    before = core.store.rows("SELECT request_id,state FROM model_usage")
                    async with tls_core(core, tmp_path) as (origin, certificate):
                        await independent_node(
                            config.model_copy(update={"core_origin": origin}),
                            certificate,
                            counter,
                            tmp_path,
                            core.clock.now.timestamp(),
                            "large-result" if operation == "tts" else "success",
                        )
                    assert jobs.status(job["job_id"])["state"] == (
                        "READY" if operation == "tts" else "TRANSCRIBED"
                    )
                    assert jobs.result(job["job_id"]).kind == operation
                    assert core.store.rows("SELECT request_id,state FROM model_usage") == before
                    if operation == "tts":
                        assert len(jobs.result(job["job_id"]).audio_bytes()) > 131072
                        assert len(before) == 1 and before[0]["state"] == "SETTLED"
                        assert core.store.one("SELECT state FROM outbox")["state"] == "PENDING"
                    else:
                        assert jobs.untrusted_transcription(job["job_id"])["partial"] is True
                        assert before == []
                    assert not sends and len(model_calls) == int(operation == "tts")
                    assert len(child_receipts(counter)) == 1
                    assert child_receipts(counter)[0]["pid"] != os.getpid()
                    assert core.store.one("SELECT body_json FROM events WHERE event_id=?",
                                           (event["event_id"],))["body_json"] == event["body_json"]
                    window.verify()
                    if operation == "tts":
                        core.clock.advance((plan.end - core.clock.now).total_seconds() + 1)
                        # The independent node may outlive the original HTTP keep-alive.
                        async with private_http_client(cfg.core_url, cfg.bridge_token) as resumed:
                            expired = await resumed.get(path + "/state")
                        assert expired.status_code == 503, expired.text
                        assert expired.json() == {"error": "LIVE_TEXT_APPROVAL_EXPIRED"}

    run(scenario())


if __name__ == "__main__" and sys.argv[1:2] == ["--node-fixture"]:
    node_config = VoiceNodeConfig.model_validate_json(Path(sys.argv[2]).read_text(encoding="utf-8"))
    trust = ssl.create_default_context(cafile=sys.argv[3])
    # The existing native window fixture uses a fixed Core clock. The simulated
    # independent node shares only that wall clock; lease verification stays real.
    with patch("companion_voice_node.worker.time.time", return_value=float(sys.argv[5])):
        outcome = asyncio.run(
            VoiceNodeWorker(
                node_config,
                transport=httpx.AsyncHTTPTransport(verify=trust),
                supervisor_factory=factory(Path(sys.argv[4]), scenario=sys.argv[6]),
            ).run(once=True)
        )
    print(json.dumps({"outcome": outcome, "pid": os.getpid()}))
