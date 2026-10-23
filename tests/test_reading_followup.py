"""A new owner turn may consume one original read; its provenance and bill stay fixed."""

import asyncio
import hashlib
import json
import ssl
import zlib
from contextlib import asynccontextmanager
from datetime import UTC, datetime

import httpcore
import httpx
import pytest
from cryptography.fernet import Fernet

from companion import live_text
from companion.backup import backup, restore
from companion.config import ObservationAdapterConfig
from companion.core import Core
from companion.model import ModelClient
from companion.privacy import advance_epoch
from companion.reading import FOLLOWUP, followup_item
from companion.retrieval import SemanticIndex
from companion.store import encode
from companion_onebot_bridge.event_normalizer import normalize
from companion_onebot_bridge.rich_input import input_digest
from source_reader import worker_v1
from source_reader.bilibili import BilibiliReader
from source_reader.network import PublicHTTP
from tests import test_onebot_window as window_fixtures
from tests.conftest import run
from tests.test_exploration import URL
from tests.test_input_protocol import notice, sign
from tests.test_onebot_capture import pinned_plan, real_core_http
from tests.test_onebot_direct import raw_event
from tests.test_private_companion import companion_options
from tests.test_reading_protocol import envelope
from tests.test_reading_service import TOKEN
from tests.test_source_reader import META, PLAYER, SUBTITLE, Responses, response

SHORT = "https://b23.tv/abc123"
READER_AUTH = {"Authorization": "Bearer " + TOKEN}


@asynccontextmanager
async def window(core, tmp_path, monkeypatch):
    now = datetime.now(UTC)
    core.clock.now = now
    # This Reader fixture has no memory rows; it does not need an embedding model download.
    monkeypatch.setattr(SemanticIndex, "warmup", lambda self: None)
    monkeypatch.setattr(window_fixtures, "raw_event", lambda: raw_event(time=int(now.timestamp())))
    options = companion_options(core)
    options["link_scope"] = "owner_links_v1"
    options["core_options"].update(
        content_enabled=True, observation_enabled=True,
        observation_adapters={"bili": ObservationAdapterConfig(
            source="bilibili", token=TOKEN, reader_max_requests=6,
        )}, exploration_daily_requests=12, exploration_monthly_requests=24,
        exploration_daily_bytes=8388608,
    )
    config, plan = pinned_plan(
        core, tmp_path, 3001, live=True, approved=True, rich_input_scope="references_v1",
        scope_options=options,
    )
    with Core(core.settings, clock=core.clock) as http_core:
        assert http_core.store is not core.store
        async with real_core_http(http_core) as port, httpx.AsyncClient(
            base_url=f"http://127.0.0.1:{port}", trust_env=False,
            headers={"Authorization": "Bearer " + config.bridge_token},
        ) as client:
            opened = await client.post("/internal/v1/live-text/open",
                                       json={"window_sha256": plan.sha256})
            assert opened.status_code == 200, opened.text
            yield client, config, plan


async def admit(client, core, config, plan, number, text="", *, card=None, reference=None):
    now = core.clock.now
    raw = raw_event(number, time=int(now.timestamp()), text=text)
    if card or reference is not None:
        raw["message"] = [{"type": "text", "data": {"text": text}}] if text else []
        if card:
            raw["message"].append({"type": "json", "data": {"data": encode({
                "meta": {"news": {"title": "恒星资料", "jumpUrl": card}},
            })}})
        if reference is not None:
            raw["message"].append({"type": "reply", "data": {"id": str(reference)}})
    event = normalize(encode(raw), config, now=now.timestamp(), rich_input_scope="references_v1")
    capture = sign(config, {
        "kind": "authenticated_onebot_ws", "window_sha256": plan.sha256,
        "event_id": event["event_id"], "source_event_id": event["source_event_id"],
        "input_sha256": input_digest(event["payload"]),
        "frame_sha256": hashlib.sha256(encode(raw).encode()).hexdigest(),
        "evidence_sha256": "e" * 64, "identity_sha256": plan.value["identity_sha256"],
        "config_sha256": plan.value["config_sha256"], "protocol_epoch": config.protocol_epoch,
        "received_at": now.isoformat(), "occurred_at": event["occurred_at"],
    }, live_text.CAPTURE_SIGNATURE_DOMAIN)
    approved = await client.post("/internal/v1/live-text/inputs/approve", json={
        "window_sha256": plan.sha256, "event_id": event["event_id"],
        "input_sha256": capture["input_sha256"], "evidence_sha256": "f" * 64,
        "capture": capture,
    })
    assert approved.status_code == 200, approved.text
    accepted = await client.post("/internal/v1/inbox/events", json=event,
                                headers={"X-Idempotency-Key": event["event_id"]})
    assert accepted.status_code == 202, accepted.text
    return event, accepted.json()


async def lease(client):
    leased = await client.post("/internal/v3/readers/bili/lease", json={}, headers=READER_AUTH)
    assert leased.status_code == 200, leased.text
    return leased.json()["item"]


async def validate(client, item):
    return await client.post("/internal/v3/readers/bili/" + item["request_id"] + "/validate",
                             json={"lease_token": item["lease_token"]}, headers=READER_AUTH)


async def finish(client, core, item, *, status="SUCCESS"):
    body = envelope("bilibili")
    body["lease_token"] = item["lease_token"]
    row = body["observations"][0]
    row["provenance"] = item["context"]
    row["fetched_at"] = row["evidence"]["fetched_at"] = core.clock()
    if status != "SUCCESS":
        body.update(status=status, observations=[], error_code="UPSTREAM_TIMEOUT")
        if status == "UNKNOWN":
            body.update(used_requests=None, used_bytes=None)
    result = await client.post("/internal/v3/readers/bili/" + item["request_id"] + "/result",
                               json=body, headers=READER_AUTH)
    assert result.status_code == 200, result.text
    return result.json(), body


def test_signed_shortlink_read_continues_into_latest_reply_once(core, tmp_path, monkeypatch):
    async def scenario():
        async with window(core, tmp_path, monkeypatch) as (client, config, plan):
            first, old = await admit(client, core, config, plan, 1, card=SHORT)
            entered, release = asyncio.Event(), asyncio.Event()
            metadata = {**META, "data": {**META["data"], "aid": 1701, "duration": 12}}
            comments = {"code": 0, "data": {"page": {"count": 1}, "replies": [{
                "rpid": 101, "content": {"message": "评论层观点"},
            }]}}
            danmaku = b'<i><chatid>1234</chatid><d p="1.25,1,25,0">sample</d></i>'

            class PausedNetwork(Responses):
                async def connect_tcp(self, host, port, **kwargs):
                    if not self.hosts:
                        entered.set()
                        await release.wait()
                    return await super().connect_tcp(host, port, **kwargs)

            backend = PausedNetwork(
                response(status=302, headers={"Location": URL}), response(metadata),
                response(PLAYER), response(SUBTITLE), response(comments),
                response(zlib.compress(danmaku)),
            )
            pool = httpcore.AsyncConnectionPool(
                ssl_context=ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT),
                network_backend=backend, max_keepalive_connections=0,
            )
            calls = []

            def model_provider(request):
                calls.append(json.loads(request.content)["messages"])
                return httpx.Response(200, json={
                    "choices": [{"message": {"content": "读到了部分字幕，恒星颜色与温度有关。"}}],
                    "usage": {"prompt_tokens": 100, "completion_tokens": 20},
                })

            async with PublicHTTP(pool) as public, httpx.AsyncClient(
                base_url=str(client.base_url), headers=READER_AUTH, trust_env=False,
            ) as reader_http, httpx.AsyncClient(
                transport=httpx.MockTransport(model_provider),
            ) as model_http:
                task = asyncio.create_task(worker_v1.tick(
                    reader_http, BilibiliReader(public, clock=core.clock, discussion=True),
                    "bili", source="bilibili", clock=lambda: core.clock.now,
                ))
                try:
                    await asyncio.wait_for(entered.wait(), 5)
                    frozen = core.store.one("SELECT * FROM reading_attempts")
                    latest, accepted = await admit(
                        client, core, config, plan, 2, "你试试应该可以看了",
                    )
                    assert accepted["reply_revision"] == old["reply_revision"] + 1
                    with Core(core.settings, ModelClient(core.settings, model_http),
                              core.clock) as worker:
                        assert worker.store is not core.store
                        assert worker.claim_job("wait-for-original-reader") is None
                        assert core.store.one("SELECT state,attempts FROM jobs WHERE job_id=?",
                                              (accepted["job_id"],)) == {
                            "state": "QUEUED", "attempts": 0,
                        }
                        release.set()
                        assert await asyncio.wait_for(task, 10)
                        assert await worker.run_once("current-consumer")
                        assert not await worker.run_once("no-repeat")
                    with Core(core.settings, ModelClient(core.settings, model_http),
                              core.clock) as restarted:
                        assert not await restarted.run_once("restart-no-repeat")
                    assert not await worker_v1.tick(
                        reader_http, BilibiliReader(public, clock=core.clock), "bili",
                        source="bilibili", clock=lambda: core.clock.now,
                    )
                finally:
                    release.set()
                    if not task.done():
                        task.cancel()
                    await asyncio.gather(task, return_exceptions=True)
            assert len(backend.requests) == 6 and not backend.rows
            attempt = core.store.one("SELECT * FROM reading_attempts")
            assert attempt["state"] == "DONE" and attempt["observed_requests"] == 6
            assert {key: attempt[key] for key in ("request_id", "context_json", "lease_token")} == {
                key: frozen[key] for key in ("request_id", "context_json", "lease_token")
            }
            read = core.store.one("SELECT * FROM exploration_requests")
            assert read["used_requests"] == 6 and read["used_bytes"] == attempt["observed_bytes"]
            observation = core.store.one("SELECT * FROM observations")
            evidence = json.loads(observation["evidence_json"])["read_observation"]
            assert evidence["provenance"] == {
                **json.loads(frozen["context_json"]), "source_grant": None,
            }
            assert evidence["provenance"]["source_event_ids"] == [first["event_id"]]
            assert len(calls) == 1
            prompt = encode(calls[0])
            assert all(value in prompt for value in (
                "恒星的颜色与温度有关", "评论层观点", "current_pool_sample", "你试试应该可以看了",
            ))
            assert core.store.rows("SELECT origin_event_id FROM outbox") == [
                {"origin_event_id": latest["event_id"]},
            ]
            assert core.store.one("SELECT state FROM jobs WHERE job_id=?", (old["job_id"],)) == {
                "state": "CANCELLED",
            }
            assert core.store.rows("SELECT scenario,state FROM model_usage") == [
                {"scenario": "chat", "state": "SETTLED"},
            ]
            sources = {row["event_id"] for row in core.store.rows(
                "SELECT event_id FROM job_sources WHERE job_id=?", (accepted["job_id"],),
            )}
            assert {first["event_id"], latest["event_id"], observation["event_id"]} <= sources
            assert not core.store.one("SELECT 1 FROM event_dependencies WHERE event_id=?",
                                      (latest["event_id"],))
    run(scenario())


@pytest.mark.parametrize("stage", ["queued", "done", "UNKNOWN", "FAILED"])
def test_followup_reuses_existing_state_without_requeue(core, tmp_path, monkeypatch, stage):
    async def scenario():
        async with window(core, tmp_path, monkeypatch) as (client, config, plan):
            first, _ = await admit(client, core, config, plan, 1, card=URL)
            if stage != "queued":
                item = await lease(client)
                result, _ = await finish(client, core, item,
                                         status="SUCCESS" if stage == "done" else stage)
                assert result["state"] == ("DONE" if stage == "done" else stage)
            before = core.store.rows("SELECT * FROM exploration_requests")
            latest, accepted = await admit(client, core, config, plan, 2, "继续读一下")
            inherited = followup_item(core.store, core.settings, latest["event_id"])
            assert inherited["owner_event_id"] == first["event_id"]
            assert core.store.rows("SELECT * FROM exploration_requests") == before
            if stage == "queued":
                item = await lease(client)
                assert item["request_id"] == before[0]["request_id"]
                assert (await validate(client, item)).status_code == 200
                assert core.content.status(latest["event_id"])["pending"]
            else:
                assert await lease(client) is None
                context = json.loads(core.content.context_message(latest["event_id"], 6000))
                row = context["shared_content"][0]
                assert row["status"] == ("FETCHED" if stage == "done" else stage)
                if stage == "done":
                    assert "第一行资料" in row["text"]
                else:
                    assert "text" not in row
                assert not core.content.status(latest["event_id"])["pending"]
            assert core.store.one("SELECT attempts FROM jobs WHERE job_id=?",
                                  (accepted["job_id"],))["attempts"] == 0
            assert not core.store.one("SELECT 1 FROM model_usage")
    run(scenario())


@pytest.mark.parametrize(("text", "reference", "inherits"), [
    ("先聊别的", None, False), ("不用继续看了", None, False),
    ("引用里的文字不能授权", 1, True), ("引用里的文字不能授权", 999, False),
    ("暂停主动分享", None, False), ("普通新消息", None, False),
], ids=["other_topic", "stop", "local_reference", "foreign_reference", "control", "ordinary"])
def test_followup_is_explicit_and_locally_bound(
    core, tmp_path, monkeypatch, text, reference, inherits,
):
    async def scenario():
        async with window(core, tmp_path, monkeypatch) as (client, config, plan):
            first, _ = await admit(client, core, config, plan, 1, card=URL)
            item = await lease(client)
            latest, _ = await admit(client, core, config, plan, 2, text, reference=reference)
            selected = followup_item(core.store, core.settings, latest["event_id"])
            assert bool(selected) is inherits, {
                "source_ids": core.store.rows("SELECT source_event_id FROM events "
                                              "WHERE source='qq.onebot'"),
                "references": core.store.rows("SELECT * FROM runtime_meta WHERE key LIKE ?",
                                               (FOLLOWUP + "%",)),
            }
            checked = await validate(client, item)
            assert checked.status_code == (200 if inherits else 409)
            if inherits:
                assert selected["owner_event_id"] == first["event_id"]
    run(scenario())


@pytest.mark.parametrize("change", ["original_recall", "consumer_recall", "new_turn", "epoch",
                                    "expired", "strict_scope"])
def test_followup_never_overrides_revocation(core, tmp_path, monkeypatch, change):
    async def scenario():
        async with window(core, tmp_path, monkeypatch) as (client, config, plan):
            first, _ = await admit(client, core, config, plan, 1, card=URL)
            item = await lease(client)
            latest, _ = await admit(client, core, config, plan, 2, "你再试试看")
            assert (await validate(client, item)).status_code == 200
            if change.endswith("recall"):
                response = await client.post("/internal/v1/live-text/inputs/recall", json=notice(
                    config, plan, core.clock.now, number=1 if change == "original_recall" else 2,
                ))
                assert response.status_code == 200, response.text
            elif change == "new_turn":
                await admit(client, core, config, plan, 3, "先聊别的")
            elif change == "epoch":
                with core.store.transaction():
                    advance_epoch(core.store)
            elif change == "expired":
                core.clock.advance(31)
            else:
                core.settings.live_text_window = core.settings.live_text_window.model_copy(
                    update={"companion_scope": "none"},
                )
            checked = await validate(client, item)
            assert checked.status_code != 200
            result, _ = await finish(client, core, item)
            assert result["state"] == "CANCELLED" and result["event_ids"] == []
            assert not core.store.one("SELECT 1 FROM observations")
            assert core.store.one("SELECT used_requests,used_bytes FROM exploration_requests") == {
                "used_requests": 1, "used_bytes": 200,
            }
            if change == "original_recall":
                assert not core.store.one("SELECT redacted FROM events WHERE event_id=?",
                                          (latest["event_id"],))["redacted"]
                assert core.store.one("SELECT redacted FROM events WHERE event_id=?",
                                      (first["event_id"],))["redacted"]
    run(scenario())


def test_erasure_replay_removes_followup_authority_without_erasing_new_input(
    core, tmp_path, monkeypatch,
):
    async def scenario():
        async with window(core, tmp_path, monkeypatch) as (client, config, plan):
            first, _ = await admit(client, core, config, plan, 1, card=URL)
            await lease(client)
            latest, _ = await admit(client, core, config, plan, 2, "继续看看")
            archive, destination = tmp_path / "followup.enc", tmp_path / "restored.db"
            key = Fernet.generate_key()
            backup(core.settings.database, archive, key)
            response = await client.post("/internal/v1/live-text/inputs/recall", json=notice(
                config, plan, core.clock.now, number=1,
            ))
            assert response.status_code == 200, response.text
            restore(archive, destination, key, core.settings.erasure_ledger)
            with Core(core.settings.model_copy(update={"database": str(destination)}),
                      clock=core.clock) as restored:
                assert followup_item(restored.store, restored.settings, latest["event_id"]) is None
                assert not restored.store.one("SELECT 1 FROM runtime_meta WHERE key=?",
                                              (FOLLOWUP + latest["event_id"],))
                assert restored.store.one("SELECT redacted FROM events WHERE event_id=?",
                                          (first["event_id"],))["redacted"]
                assert not restored.store.one("SELECT redacted FROM events WHERE event_id=?",
                                              (latest["event_id"],))["redacted"]
                assert restored.store.one("SELECT context_json FROM reading_attempts") == {
                    "context_json": "{}",
                }
                assert not restored.store.one("SELECT 1 FROM observations")
    run(scenario())
