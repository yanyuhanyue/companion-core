"""Production lifespan and worker share revisions; provider transport alone is synthetic."""

import asyncio
import hashlib
import json
import time
from datetime import UTC, datetime
from types import SimpleNamespace

import httpx
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from companion import __main__, api, live_text, paid_budget
from companion.core import Core
from companion.store import encode
from companion_onebot_bridge.event_normalizer import normalize
from companion_onebot_bridge.rich_input import input_digest
from tests import test_onebot_window as window_fixtures
from tests.conftest import run
from tests.test_onebot_capture import pinned_plan
from tests.test_onebot_direct import raw_event
from tests.test_private_companion import companion_options


async def admit(client, config, plan, number, text):
    now = datetime.now(UTC)
    raw = raw_event(number, time=int(now.timestamp()), text=text)
    event = normalize(json.dumps(raw), config, now=now.timestamp())
    capture = {
        "kind": "authenticated_onebot_ws", "window_sha256": plan.sha256,
        "event_id": event["event_id"], "source_event_id": event["source_event_id"],
        "input_sha256": input_digest(event["payload"]),
        "frame_sha256": hashlib.sha256(encode(raw).encode()).hexdigest(),
        "evidence_sha256": "e" * 64, "identity_sha256": plan.value["identity_sha256"],
        "config_sha256": plan.value["config_sha256"], "protocol_epoch": config.protocol_epoch,
        "received_at": now.isoformat(), "occurred_at": event["occurred_at"],
    }
    key = Ed25519PrivateKey.from_private_bytes(bytes.fromhex(config.capture_signing_key))
    capture["signature"] = key.sign(
        live_text.CAPTURE_SIGNATURE_DOMAIN + encode(capture).encode(),
    ).hex()
    approved = await client.post("/internal/v1/live-text/inputs/approve", json={
        "window_sha256": plan.sha256, "event_id": event["event_id"],
        "input_sha256": capture["input_sha256"], "evidence_sha256": "f" * 64,
        "capture": capture,
    })
    assert approved.status_code == 200, approved.text
    result = await client.post("/internal/v1/inbox/events", json=event,
                               headers={"X-Idempotency-Key": event["event_id"]})
    assert result.status_code == 202, result.text
    return event, result.json()


def test_native_api_accepts_burst_while_independent_worker_drains_old_model(
    core, tmp_path, monkeypatch,
):
    now = datetime.now(UTC)
    core.clock.now = now
    monkeypatch.setattr(window_fixtures, "raw_event", lambda: raw_event(time=int(now.timestamp())))
    config, plan = pinned_plan(core, tmp_path, 3001, live=True, approved=True,
                               scope_options=companion_options(core))
    calls = []

    async def scenario():
        entered, release, replied = asyncio.Event(), asyncio.Event(), asyncio.Event()

        async def provider(request):
            calls.append(json.loads(request.content))
            if len(calls) == 1:
                entered.set()
                await release.wait()
            return httpx.Response(200, json={
                "choices": [{"message": {"content": "最新问题的合成回复"}}],
                "usage": {"prompt_tokens": 100, "completion_tokens": 20},
            })

        monkeypatch.setattr(__main__, "httpx", SimpleNamespace(AsyncClient=lambda **kwargs:
            httpx.AsyncClient(**kwargs, transport=httpx.MockTransport(provider))))
        completed = __main__.Recorder.completed

        def observed(recorder, **kwargs):
            completed(recorder, **kwargs)
            if recorder.store.one("SELECT 1 FROM outbox WHERE state='PENDING'"):
                replied.set()

        monkeypatch.setattr(__main__.Recorder, "completed", observed)
        app = api.create_app(core.settings)
        async with app.router.lifespan_context(app), httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1",
            headers={"Authorization": "Bearer " + config.bridge_token},
        ) as client:
            assert app.state.core is not core
            opened = await client.post("/internal/v1/live-text/open",
                                       json={"window_sha256": plan.sha256})
            assert opened.status_code == 200, opened.text
            first, first_result = await admit(client, config, plan, 1, "先记下青竹项目")
            worker = asyncio.create_task(__main__.worker(core.settings))
            try:
                await asyncio.wait_for(entered.wait(), 5)
                started = time.monotonic()
                second, second_result = await admit(client, config, plan, 2, "补充地点在城西")
                third, third_result = await admit(client, config, plan, 3, "请把前两条一起回答")
                assert not release.is_set() and not replied.is_set()
                assert time.monotonic() - started < 3  # Local acceptance, not QQ latency.
                duplicate = await client.post("/internal/v1/inbox/events", json=third,
                    headers={"X-Idempotency-Key": third["event_id"]})
                assert duplicate.status_code == 202, duplicate.text
                assert duplicate.json()["duplicate"] and duplicate.json()["reply_revision"] == 3
                assert [item["reply_revision"] for item in (
                    first_result, second_result, third_result,
                )] == [1, 2, 3]
                assert [row["state"] for row in core.store.rows(
                    "SELECT state FROM jobs ORDER BY rowid",
                )] == ["CANCELLED", "CANCELLED", "QUEUED"]
                assert core.store.one("SELECT state FROM model_usage") == {"state": "RESERVED"}
                with Core(core.settings) as second_worker:
                    assert second_worker.claim_job("concurrent-worker") is None
                release.set()
                await asyncio.wait_for(replied.wait(), 5)
                assert not worker.done(), worker.exception()
                assert len(calls) == 2
                prompt = encode(calls[-1]["messages"])
                assert all(text in prompt for text in ("青竹项目", "地点在城西", "前两条一起回答"))
                assert core.store.rows("SELECT origin_event_id FROM outbox") == [
                    {"origin_event_id": third["event_id"]},
                ]
                assert core.store.rows("SELECT state FROM model_usage ORDER BY rowid") == [
                    {"state": "SETTLED"}, {"state": "SETTLED"},
                ]
                assert {row["event_id"] for row in core.store.rows(
                    "SELECT event_id FROM job_sources WHERE job_id=?", (third_result["job_id"],),
                )} == {first["event_id"], second["event_id"], third["event_id"]}
                assert not live_text.stopped(core.store, plan.sha256)
                assert paid_budget.journal(core.store, core.settings.paid_model)["head_sha256"]
            finally:
                release.set()
                worker.cancel()
                await asyncio.gather(worker, return_exceptions=True)

    run(scenario())
