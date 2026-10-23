"""Authenticated lifecycle controls use the real API and independently opened state."""

import hashlib
import json
from contextlib import asynccontextmanager
from dataclasses import replace
from datetime import UTC, datetime

import httpx
import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from companion import api, input_protocol, input_recall, live_text
from companion.config import LiveTextHandoff
from companion.store import encode, uid
from companion.transport import enqueue
from companion_onebot_bridge.event_normalizer import normalize, normalize_recall
from companion_onebot_bridge.rich_input import input_digest
from tests import test_onebot_window as window_fixtures
from tests.conftest import run
from tests.test_onebot_capture import pinned_plan
from tests.test_onebot_direct import raw_event
from tests.test_private_companion import companion_options


@asynccontextmanager
async def environment(core, tmp_path, monkeypatch):
    now = datetime.now(UTC)
    core.clock.now = now
    monkeypatch.setattr(window_fixtures, "raw_event", lambda: raw_event(time=int(now.timestamp())))
    config, plan = pinned_plan(core, tmp_path, 3001, live=True, approved=True,
                               scope_options=companion_options(core))
    app = api.create_app(core.settings)
    async with app.router.lifespan_context(app), httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1",
        headers={"Authorization": "Bearer " + config.bridge_token},
    ) as client:
        opened = await client.post("/internal/v1/live-text/open",
                                   json={"window_sha256": plan.sha256})
        assert opened.status_code == 200, opened.text
        assert app.state.core is not core
        yield client, config, plan, now


def sign(config, body, domain):
    key = Ed25519PrivateKey.from_private_bytes(bytes.fromhex(config.capture_signing_key))
    return body | {"signature": key.sign(domain + encode(body).encode()).hex()}


async def admit(client, config, plan, now, number=1):
    raw = raw_event(number, time=int(now.timestamp()), text="合成主人输入" + str(number))
    event = normalize(json.dumps(raw), config, now=now.timestamp())
    capture = sign(config, {
        "kind": "authenticated_onebot_ws", "window_sha256": plan.sha256,
        "event_id": event["event_id"], "source_event_id": event["source_event_id"],
        "input_sha256": input_digest(event["payload"]),
        "frame_sha256": hashlib.sha256(encode(raw).encode()).hexdigest(),
        "evidence_sha256": "e" * 64, "identity_sha256": plan.value["identity_sha256"],
        "config_sha256": plan.value["config_sha256"], "protocol_epoch": config.protocol_epoch,
        "received_at": now.isoformat(), "occurred_at": event["occurred_at"],
    }, live_text.CAPTURE_SIGNATURE_DOMAIN)
    result = await client.post("/internal/v1/live-text/inputs/approve", json={
        "window_sha256": plan.sha256, "event_id": event["event_id"],
        "input_sha256": capture["input_sha256"], "evidence_sha256": "f" * 64,
        "capture": capture,
    })
    assert result.status_code == 200, result.text
    result = await client.post("/internal/v1/inbox/events", json=event,
                               headers={"X-Idempotency-Key": event["event_id"]})
    return event, result


def notice(config, plan, now, number=1):
    frame = encode({"time": int(now.timestamp()), "self_id": int(config.bot_qq_id),
                    "post_type": "notice", "notice_type": "friend_recall",
                    "user_id": int(config.owner_qq_id), "message_id": number})
    projection = normalize_recall(frame, config, now=now.timestamp())
    return sign(config, {
        "kind": "authenticated_onebot_friend_recall", "window_sha256": plan.sha256,
        **projection, "protocol_epoch": config.protocol_epoch,
        "identity_sha256": plan.value["identity_sha256"],
        "config_sha256": plan.value["config_sha256"], "received_at": now.isoformat(),
        "frame_sha256": hashlib.sha256(frame.encode()).hexdigest(),
        "evidence_sha256": "e" * 64,
    }, input_protocol.RECALL_DOMAIN)


def test_recall_erases_queued_input_once_and_next_input_still_admits(core, tmp_path, monkeypatch):
    async def scenario():
        async with environment(core, tmp_path, monkeypatch) as (client, config, plan, now):
            event, accepted = await admit(client, config, plan, now)
            assert accepted.status_code == 202, accepted.text
            body = notice(config, plan, now)
            first = await client.post("/internal/v1/live-text/inputs/recall", json=body)
            assert first.status_code == 200 and first.json()["state"] == "RECALLED", first.text
            assert first.json()["duplicate"] is False
            before = core.ledger.path.read_bytes()
            repeat = await client.post("/internal/v1/live-text/inputs/recall", json=body)
            assert repeat.status_code == 200 and repeat.json()["duplicate"], repeat.text
            assert core.ledger.path.read_bytes() == before
            state = await client.get(f"/internal/v1/live-text/inputs/{event['event_id']}/state")
            assert state.json()["state"] == "RECALLED"
            assert not state.json()["unsettled_model"] and not state.json()["unsettled_delivery"]
            assert core.store.one("SELECT body_json,redacted FROM events") == {
                "body_json": "{}", "redacted": 1,
            }
            _, accepted = await admit(client, config, plan, now, 2)
            assert accepted.status_code == 202, accepted.text
            assert not live_text.stopped(core.store, plan.sha256)
    run(scenario())


@pytest.mark.parametrize("mutation", ["signature", "epoch", "source"])
def test_recall_rejects_invalid_authentication_or_source(core, tmp_path, monkeypatch, mutation):
    async def scenario():
        async with environment(core, tmp_path, monkeypatch) as (client, config, plan, now):
            _, accepted = await admit(client, config, plan, now)
            assert accepted.status_code == 202, accepted.text
            body = notice(config, plan, now)
            if mutation == "signature":
                body["signature"] = "0" * 128
            elif mutation == "epoch":
                body = notice(replace(config, protocol_epoch="previous-epoch"), plan, now)
            else:
                unsigned = {key: value for key, value in body.items() if key != "signature"}
                unsigned["target_source_event_id"] = "99999:" + body[
                    "target_source_event_id"].split(":", 1)[1]
                body = sign(config, unsigned, input_protocol.RECALL_DOMAIN)
            result = await client.post("/internal/v1/live-text/inputs/recall", json=body)
            assert result.status_code == 403, result.text
            assert core.store.one("SELECT redacted FROM events") == {"redacted": 0}
    run(scenario())


def test_notice_before_input_commits_tombstone_and_returns_authenticated_410(
    core, tmp_path, monkeypatch,
):
    async def scenario():
        async with environment(core, tmp_path, monkeypatch) as (client, config, plan, now):
            result = await client.post("/internal/v1/live-text/inputs/recall",
                                       json=notice(config, plan, now))
            assert result.status_code == 200 and result.json()["state"] == "PENDING_TARGET"
            event, result = await admit(client, config, plan, now)
            assert result.status_code == 410 and result.json() == {"error": "EVENT_ERASED"}
            assert core.store.rows("SELECT * FROM events") == []
            assert core.store.rows("SELECT * FROM jobs") == []
            state = await client.get(f"/internal/v1/live-text/inputs/{event['event_id']}/state")
            assert state.json()["state"] == "RECALLED"
            _, accepted = await admit(client, config, plan, now, 2)
            assert accepted.status_code == 202, accepted.text
            assert not live_text.stopped(core.store, plan.sha256)
    run(scenario())


def test_prebegin_cancellation_requires_exact_lease_and_retains_attempt(
    core, tmp_path, monkeypatch,
):
    async def scenario():
        async with environment(core, tmp_path, monkeypatch) as (client, config, plan, now):
            event, accepted = await admit(client, config, plan, now)
            assert accepted.status_code == 202, accepted.text
            stored = core.store.one("SELECT * FROM events")
            message_id = enqueue(core.store, event["event_id"], stored["conversation_id"],
                                 "旧回复", core.clock())
            token = uid("lease")
            core.store.execute("UPDATE outbox SET state='LEASED',lease_token=? WHERE message_id=?",
                               (token, message_id))
            _, accepted = await admit(client, config, plan, now, 2)
            assert accepted.status_code == 202, accepted.text
            body = {"window_sha256": plan.sha256, "message_id": message_id,
                    "lease_token": uid("lease"), "reply_revision": 1}
            rejected = await client.post("/internal/v1/live-text/cancel-before-send", json=body)
            assert rejected.status_code == 409, rejected.text
            body["lease_token"] = token
            cancelled = await client.post("/internal/v1/live-text/cancel-before-send", json=body)
            assert cancelled.status_code == 200, cancelled.text
            assert cancelled.json()["send_attempt_retained"] is True
            repeated = await client.post("/internal/v1/live-text/cancel-before-send", json=body)
            assert repeated.json() == cancelled.json()
            assert core.store.one("SELECT state,attempt_count FROM outbox") == {
                "state": "CANCELLED", "attempt_count": 1,
            }
    run(scenario())


def test_old_capture_uses_verified_same_epoch_handoff_chain_and_original_signing_key(
    core, tmp_path, monkeypatch,
):
    async def scenario():
        async with environment(core, tmp_path, monkeypatch) as (client, config, plan, now):
            event, accepted = await admit(client, config, plan, now)
            assert accepted.status_code == 202, accepted.text
            prior = core.settings.live_text_window
            successor_sha = "2" * 64
            original = live_text._meta(core.store, live_text.HISTORY + plan.sha256)
            original.update(state="RETIRED", successor_plan_sha256=successor_sha)
            live_text._put(core.store, live_text.HISTORY + plan.sha256, original)
            handoff = LiveTextHandoff(
                predecessor_plan_sha256=plan.sha256, predecessor_window_id=prior.window_id,
                budget_journal_head_sha256="a" * 64, bridge_journal_head_sha256="b" * 64,
                identity_sha256=plan.value["identity_sha256"], protocol_epoch=config.protocol_epoch,
                privacy_epoch=0, erasure_ledger_sha256="c" * 64, evidence_sha256="d" * 64,
            )
            rotated = Ed25519PrivateKey.generate().public_key().public_bytes_raw().hex()
            successor = prior.model_copy(update={"plan_sha256": successor_sha,
                "window_id": "win_" + "2" * 32, "handoff": handoff,
                "capture_public_key": rotated})
            source = input_recall.canonical_source(bot_qq_id=config.bot_qq_id,
                owner_qq_id=config.owner_qq_id, protocol_epoch=config.protocol_epoch,
                provider_message_id="1")
            assert input_protocol._capture_matches(core.store, core.settings, successor,
                                                    source, event["event_id"], now.isoformat())
            stale_epoch = successor.model_copy(update={"handoff": handoff.model_copy(
                update={"protocol_epoch": "previous-epoch"})})
            assert not input_protocol._capture_matches(core.store, core.settings, stale_epoch,
                                                        source, event["event_id"], now.isoformat())
            original["successor_plan_sha256"] = "3" * 64
            live_text._put(core.store, live_text.HISTORY + plan.sha256, original)
            assert not input_protocol._capture_matches(core.store, core.settings, successor,
                                                        source, event["event_id"], now.isoformat())
    run(scenario())
