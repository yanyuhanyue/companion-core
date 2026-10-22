"""Production API lifespan owns image-model I/O; only the provider transport is synthetic."""

import asyncio
import hashlib
import json
import sqlite3
import threading
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import httpx
import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from fastapi.testclient import TestClient

from companion import __main__, api, budget, live_text, paid_budget
from companion.contracts import CoreError
from companion.core import Core
from companion.model import ModelClient
from companion.qq_images import UPLOAD_DOMAIN
from companion.store import encode
from companion_onebot_bridge.event_normalizer import normalize
from companion_onebot_bridge.rich_input import input_digest
from tests import test_onebot_window as window_fixtures
from tests.conftest import run
from tests.test_live_image_input import options, png
from tests.test_memory_control import grant
from tests.test_onebot_capture import pinned_plan
from tests.test_onebot_direct import raw_event
from tests.test_onebot_images import image_segment
from tests.test_paid_model_gate import approved_live


def image_settings(core, tmp_path, monkeypatch):
    now = datetime.now(UTC)
    # The actual API and worker use their production clock. Shift only the
    # synthetic plan fixture into this test's current interval.
    monkeypatch.setattr(window_fixtures, "raw_event", lambda: raw_event(time=int(now.timestamp())))
    scope = options()
    scope["paid_options"].update(
        starts_at=(now - timedelta(hours=1)).isoformat(),
        expires_at=(now + timedelta(hours=1)).isoformat(),
        rates_valid_until=(now + timedelta(hours=1)).isoformat(),
    )
    config, plan = pinned_plan(
        core, tmp_path, 3001, live=True, approved=True,
        rich_input_scope="references_v1", scope_options=scope,
    )
    return config, plan


def model_clients(monkeypatch, settings, respond):
    clients = []

    def factory(**kwargs):
        assert kwargs == {"timeout": settings.timeout_seconds, "trust_env": False}
        client = httpx.AsyncClient(**kwargs, transport=httpx.MockTransport(respond))
        clients.append(client)
        return client

    # Patch only the API's outbound client factory. ModelClient, Core, app state,
    # HTTP routes, budget reservations and lifespan remain real.
    monkeypatch.setattr(api, "AsyncClient", factory, raising=False)
    return clients


def approve_image(client, config, plan):
    now = datetime.now(UTC)
    raw = raw_event(time=int(now.timestamp()), message=[
        {"type": "text", "data": {"text": "请解释这张图"}}, image_segment(),
    ])
    event = normalize(json.dumps(raw), config, now=now.timestamp(),
                      rich_input_scope="references_v1", image_scope="owner_image_v1")
    receipt = {
        "kind": "authenticated_onebot_ws", "window_sha256": plan.sha256,
        "event_id": event["event_id"], "source_event_id": event["source_event_id"],
        "input_sha256": input_digest(event["payload"]),
        "frame_sha256": hashlib.sha256(encode(raw).encode()).hexdigest(),
        "evidence_sha256": "e" * 64, "identity_sha256": plan.value["identity_sha256"],
        "config_sha256": plan.value["config_sha256"], "protocol_epoch": config.protocol_epoch,
        "received_at": now.isoformat(), "occurred_at": event["occurred_at"],
    }
    key = Ed25519PrivateKey.from_private_bytes(bytes.fromhex(config.capture_signing_key))
    receipt["signature"] = key.sign(
        live_text.CAPTURE_SIGNATURE_DOMAIN + encode(receipt).encode(),
    ).hex()
    response = client.post("/internal/v1/live-text/open", json={"window_sha256": plan.sha256})
    assert response.status_code == 200, response.text
    response = client.post("/internal/v1/live-text/inputs/approve", json={
        "window_sha256": plan.sha256, "event_id": event["event_id"],
        "input_sha256": receipt["input_sha256"], "evidence_sha256": "f" * 64,
        "capture": receipt,
    })
    assert response.status_code == 200, response.text
    response = client.post("/internal/v1/inbox/events", json=event,
                           headers={"X-Idempotency-Key": event["event_id"]})
    assert response.status_code == 202, response.text
    return event, response.json()["job_id"], key


def test_api_lifespan_image_upload_calls_model_and_worker_continues_same_budget(
    core, tmp_path, monkeypatch,
):
    config, plan = image_settings(core, tmp_path, monkeypatch)
    calls = []

    def provider(request):
        assert str(request.url) == core.settings.model_url + "/chat/completions"
        calls.append(json.loads(request.content))
        return httpx.Response(200, json={
            "choices": [{"message": {"content": "图中有蓝色区域。"}}],
            "usage": {"prompt_tokens": 100, "completion_tokens": 20},
        })

    clients = model_clients(monkeypatch, core.settings, provider)
    app = api.create_app(core.settings)
    with TestClient(app, headers={"Authorization": "Bearer " + config.bridge_token}) as client:
        assert app.state.core is not core
        event, job_id, key = approve_image(client, config, plan)
        prefix = "/internal/v2/content/" + event["event_id"] + "/image"
        source = event["payload"]["rich"]["segments"][0]["source_sha256"]
        claim = client.post(prefix + "/claim", json={
            "request_key": "image:0", "source_sha256": source,
        })
        assert claim.status_code == 200, claim.text
        token = claim.json()["lease_token"]
        data = png()
        proof = {
            "plan_sha256": plan.sha256, "event_id": event["event_id"],
            "source_sha256": source, "lease_token": token,
            "media_sha256": hashlib.sha256(data).hexdigest(), "mime": "image/png",
        }
        proof["signature"] = key.sign(UPLOAD_DOMAIN + encode(proof).encode()).hex()
        uploaded = client.post(prefix, content=data, headers={
            "Content-Type": "image/png", "X-Content-Key": "image:0",
            "X-Content-Lease": token, "X-Content-Proof": encode(proof),
        })
        assert uploaded.status_code == 200, uploaded.text
        assert uploaded.json()["state"] == "SUCCEEDED" and len(calls) == 1
        assert len(clients) == 1 and not clients[0].is_closed
        assert core.store.one("SELECT scenario,state FROM model_usage") == {
            "scenario": "content_vision", "state": "SETTLED",
        }

        async def worker():
            async with httpx.AsyncClient(transport=httpx.MockTransport(provider)) as model_http:
                with Core(core.settings, ModelClient(core.settings, model_http)) as running:
                    assert await running.run_once()

        run(worker())
        assert len(calls) == 2
        assert calls[-1]["messages"][-1]["content"][1] == calls[0]["messages"][0]["content"][1]
        assert core.store.rows("SELECT scenario,state FROM model_usage ORDER BY rowid") == [
            {"scenario": "content_vision", "state": "SETTLED"},
            {"scenario": "chat", "state": "SETTLED"},
        ]
        assert {row["logical_request_id"] for row in core.store.rows(
            "SELECT logical_request_id FROM model_admissions",
        )} == {job_id}
        assert core.store.one("SELECT state FROM outbox")["state"] == "PENDING"
        assert paid_budget.journal(core.store, core.settings.paid_model)["head_sha256"]
    assert clients[0].is_closed
    with pytest.raises(sqlite3.ProgrammingError, match="closed"):
        app.state.core.store.one("SELECT 1")


@pytest.mark.parametrize("outcome", [
    "settled", "unknown", "stopped", "admission_expired", "image_lease_expired",
])
def test_worker_loop_waits_for_api_vision_and_preserves_unknown_block(
    core, tmp_path, monkeypatch, outcome,
):
    config, plan = image_settings(core, tmp_path, monkeypatch)
    entered, release = threading.Event(), threading.Event()
    calls = []

    async def provider(request):
        payload = json.loads(request.content)
        calls.append(payload)
        if payload["messages"][0]["role"] == "user":
            entered.set()
            assert await asyncio.to_thread(release.wait, 5)
            if outcome == "unknown":
                raise httpx.ReadTimeout("synthetic uncertain image response", request=request)
        return httpx.Response(200, json={
            "choices": [{"message": {"content": "图中有蓝色区域。"}}],
            "usage": {"prompt_tokens": 100, "completion_tokens": 20},
        })

    clients = model_clients(monkeypatch, core.settings, provider)
    monkeypatch.setattr(__main__, "httpx", SimpleNamespace(AsyncClient=lambda **kwargs:
        httpx.AsyncClient(**kwargs, transport=httpx.MockTransport(provider))))
    app = api.create_app(core.settings)
    with TestClient(app, headers={"Authorization": "Bearer " + config.bridge_token}) as client:
        event, job_id, key = approve_image(client, config, plan)
        prefix = "/internal/v2/content/" + event["event_id"] + "/image"
        source = event["payload"]["rich"]["segments"][0]["source_sha256"]
        claim = client.post(prefix + "/claim", json={
            "request_key": "image:0", "source_sha256": source,
        })
        assert claim.status_code == 200, claim.text
        token, data = claim.json()["lease_token"], png()
        proof = {
            "plan_sha256": plan.sha256, "event_id": event["event_id"],
            "source_sha256": source, "lease_token": token,
            "media_sha256": hashlib.sha256(data).hexdigest(), "mime": "image/png",
        }
        proof["signature"] = key.sign(UPLOAD_DOMAIN + encode(proof).encode()).hex()

        async def scenario():
            polled, replied = asyncio.Event(), asyncio.Event()
            original_completed = __main__.Recorder.completed

            def completed(recorder, **kwargs):
                original_completed(recorder, **kwargs)
                polled.set()
                if recorder.store.one("SELECT 1 FROM outbox WHERE state='PENDING'"):
                    replied.set()

            monkeypatch.setattr(__main__.Recorder, "completed", completed)
            upload = asyncio.create_task(asyncio.to_thread(client.post, prefix, content=data,
                headers={"Content-Type": "image/png", "X-Content-Key": "image:0",
                         "X-Content-Lease": token, "X-Content-Proof": encode(proof)}))
            worker = None
            try:
                assert await asyncio.to_thread(entered.wait, 3)
                assert core.store.one("SELECT state FROM model_usage")["state"] == "RESERVED"
                worker = asyncio.create_task(__main__.worker(core.settings))
                poll = asyncio.create_task(polled.wait())
                done, _ = await asyncio.wait({worker, poll}, timeout=3,
                                             return_when=asyncio.FIRST_COMPLETED)
                poll.cancel()
                await asyncio.gather(poll, return_exceptions=True)
                assert worker not in done, worker.exception()
                assert polled.is_set()
                assert core.store.one("SELECT state,attempts FROM jobs WHERE job_id=?",
                                      (job_id,)) == {"state": "QUEUED", "attempts": 0}
                assert len(calls) == 1
                assert not core.store.one("SELECT 1 FROM outbox")
                with pytest.raises(CoreError, match="LIVE_TEXT_RECONCILIATION_REQUIRED"):
                    budget.reserve(core.store, core.settings, job_id, "chat",
                                   [{"role": "user", "content": "concurrent request"}],
                                   core.clock(), clock=core.clock)
                assert core.store.one("SELECT COUNT(*) n FROM model_admissions")["n"] == 1
                if outcome in {"stopped", "admission_expired", "image_lease_expired"}:
                    expired = (datetime.now(UTC) - timedelta(seconds=1)).isoformat()
                    if outcome == "stopped":
                        live_text.stop_window(core.store, live_text.WindowReference(
                            window_sha256=plan.sha256,
                        ))
                    elif outcome == "admission_expired":
                        core.store.execute("UPDATE model_admissions SET lease_until=?", (expired,))
                    else:
                        core.store.execute("UPDATE content_items SET lease_until=?", (expired,))
                    expected = {
                        "stopped": "MODEL_BUDGET_RECONCILIATION_REQUIRED",
                        "admission_expired": "LIVE_TEXT_RECONCILIATION_REQUIRED",
                        "image_lease_expired": "LIVE_IMAGE_LEASE_FENCED",
                    }[outcome]
                    with pytest.raises(CoreError, match=expected):
                        await asyncio.wait_for(worker, 3)
                    assert core.store.one("SELECT state,attempts FROM jobs WHERE job_id=?",
                                          (job_id,)) == {"state": "QUEUED", "attempts": 0}
                    assert len(calls) == 1
                    assert core.store.one("SELECT state FROM model_usage")["state"] == "RESERVED"
                    return
                release.set()
                uploaded = await asyncio.wait_for(upload, 3)
                if outcome == "settled":
                    assert uploaded.status_code == 200, uploaded.text
                    await asyncio.wait_for(replied.wait(), 3)
                    assert not worker.done()
                    assert len(calls) == 2
                    assert core.store.rows(
                        "SELECT scenario,state FROM model_usage ORDER BY rowid",
                    ) == [
                        {"scenario": "content_vision", "state": "SETTLED"},
                        {"scenario": "chat", "state": "SETTLED"},
                    ]
                else:
                    assert uploaded.status_code >= 400
                    with pytest.raises(CoreError, match="LIVE_TEXT_RECONCILIATION_REQUIRED"):
                        await asyncio.wait_for(worker, 3)
                    assert len(calls) == 1
                    assert core.store.one("SELECT state FROM model_usage")["state"] == "UNKNOWN"
                    assert core.store.one("SELECT billed_micro_usd FROM model_usage")[
                        "billed_micro_usd"
                    ] > 0
                    assert not core.store.one("SELECT 1 FROM outbox")
            finally:
                release.set()
                await asyncio.wait_for(upload, 3)
                if worker is not None:
                    worker.cancel()
                    await asyncio.gather(worker, return_exceptions=True)

        run(scenario())
    assert clients[0].is_closed


@pytest.mark.parametrize("mode", ["demo", "live_text", "ordinary_content"])
def test_api_without_owner_image_scope_opens_no_model_http_client(
    core, tmp_path, monkeypatch, mode,
):
    if mode == "live_text":
        approved_live(core, tmp_path)
    elif mode == "ordinary_content":
        core.settings.content_enabled = True
    clients = model_clients(monkeypatch, core.settings,
                            lambda request: pytest.fail("No model I/O expected"))
    app = api.create_app(core.settings)
    with TestClient(app) as client:
        assert client.get("/internal/v1/health/live").status_code == 200
        assert app.state.core.model is None
    assert clients == []


def test_api_image_client_closes_when_core_startup_fails(core, tmp_path, monkeypatch):
    image_settings(core, tmp_path, monkeypatch)
    clients = model_clients(monkeypatch, core.settings,
                            lambda request: pytest.fail("Startup must not call the model"))

    def fail_core(*args, **kwargs):
        raise CoreError("SYNTHETIC_STARTUP_FAILURE", 503)

    monkeypatch.setattr(api, "Core", fail_core)
    with pytest.raises(CoreError, match="SYNTHETIC_STARTUP_FAILURE"), TestClient(
        api.create_app(core.settings),
    ):
        pytest.fail("Startup failure must stop lifespan")
    assert len(clients) == 1 and clients[0].is_closed


def test_exclusive_memory_control_opens_no_image_client_even_with_prior_image_settings(
    core, tmp_path, monkeypatch,
):
    config, _ = image_settings(core, tmp_path, monkeypatch)
    approval = grant(core, config, "/记住这个：合成项目")
    clients = model_clients(monkeypatch, core.settings,
                            lambda request: pytest.fail("Memory controls have no model I/O"))
    app = api.create_app(core.settings, memory_control_grant=approval)
    with TestClient(app) as client:
        assert client.get("/internal/v1/health/live").status_code == 200
        assert client.get("/internal/v1/health/ready").status_code == 403
        assert app.state.core.model is None
    assert clients == []
