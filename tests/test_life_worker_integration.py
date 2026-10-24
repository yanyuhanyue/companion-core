"""Real TCP entrypoints and an independent native Life worker; provider I/O is mocked.

Owner inputs/Reader receipts are synthetic retained sources explicitly admitted to a
subsequent private-life SHADOW window. Foreground reply jobs are cancelled to isolate
background work; no fake successful reply or delivery is manufactured.
"""

import asyncio
import json
from datetime import timedelta
from functools import partial
from zoneinfo import ZoneInfo

import httpx
import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from pydantic import SecretStr

from companion import life_worker, live_text
from companion.core import Core
from companion.life_protocol import (
    LifeLoopConfig,
    LifeQZoneDraft,
    LifeQZoneSourceGrant,
    LifeShadowApproval,
)
from companion.life_topics import _source
from companion.model import ModelClient
from tests.conftest import event, run
from tests.test_exploration import URL, configure
from tests.test_input_protocol import admit
from tests.test_live_text_window import live_fixture
from tests.test_onebot_capture import real_core_http
from tests.test_private_companion import companion_options
from tests.test_reading_protocol import envelope


async def native_worker(core, provider, monkeypatch):
    finished = asyncio.Event()
    original = Core.run_once

    async def observe(instance, worker_id="worker", **kwargs):
        assert instance.store is not core.store
        result = await original(instance, worker_id, **kwargs)
        if instance.store.one(
            "SELECT 1 FROM jobs j JOIN life_activity_results r USING(job_id) "
            "WHERE j.state IN ('SUCCEEDED','FAILED','CANCELLED')"
        ):
            finished.set()
        return result

    async with httpx.AsyncClient(transport=httpx.MockTransport(provider)) as model_http:
        with monkeypatch.context() as patch:
            patch.setattr(life_worker, "Core", partial(Core, clock=core.clock))
            patch.setattr(life_worker, "ModelClient", lambda cfg, _: ModelClient(cfg, model_http))
            patch.setattr(Core, "run_once", observe)
            worker = asyncio.create_task(life_worker.life_worker(core.settings))
            waiter = asyncio.create_task(finished.wait())
            try:
                done, _ = await asyncio.wait(
                    (worker, waiter), timeout=10, return_when=asyncio.FIRST_COMPLETED
                )
                if worker in done:
                    worker.result()
                assert waiter in done, core.store.rows("SELECT * FROM jobs")
            finally:
                worker.cancel()
                waiter.cancel()
                await asyncio.gather(worker, waiter, return_exceptions=True)


@pytest.mark.parametrize(
    "scenario",
    ["bug", "reading", "qzone", "qzone_revoked", "preempt", "pause", "reading_preempt", "unknown"],
)
def test_real_http_source_to_independent_worker_shadow(core, tmp_path, monkeypatch, scenario):
    calls = []
    source_id = "evt_test_1"
    live_client = live_config = live_plan = None
    next_owner = None
    reading = scenario in {"reading", "reading_preempt"}
    preempt = scenario in {"preempt", "reading_preempt"}
    interrupted = preempt or scenario == "pause"
    core.settings.control_token = SecretStr("synthetic-life-control-" * 3)
    core.settings.life_loop = LifeLoopConfig(mode="SHADOW")
    if reading:
        configure(core)
        core.settings.content_enabled = True

    async def provider(request):
        nonlocal next_owner
        payload = json.loads(request.content)
        material = json.loads(payload["messages"][-1]["content"])
        assert material["kind"] == "life"
        calls.append(material)
        if preempt:
            next_owner, response = await admit(
                live_client, live_config, live_plan, core.clock.now, number=2
            )
            assert response.status_code == 202, response.text
        elif scenario == "pause":
            core.goals.pause_autonomy(True)
        if scenario == "qzone_revoked":
            approval = core.settings.live_text_window
            core.settings.live_text_window = approval.model_copy(
                update={
                    "life_shadow": approval.life_shadow.model_copy(
                        update={"qzone_draft_sources": []}
                    ),
                }
            )
        response = httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(
                                {
                                    "action": "FOLLOW_UP"
                                    if scenario in {"bug", "preempt", "pause"}
                                    else "SHARE",
                                    "destination": "QZONE_DRAFT"
                                    if scenario.startswith("qzone")
                                    else "PRIVATE_MESSAGE_DRAFT",
                                    "source_event_ids": [source_id],
                                    "reason": "仍未结束" if scenario == "bug" else "实际阅读结果",
                                    "text": "昨天那个导出问题有新线索了吗？"
                                    if scenario == "bug"
                                    else "今天讨论的问题留下了一个值得继续探索的想法。"
                                    if scenario.startswith("qzone")
                                    else "读到的这段介绍值得再看看。",
                                },
                                ensure_ascii=False,
                            )
                        }
                    }
                ],
                "usage": {"prompt_tokens": 180, "completion_tokens": 60},
            },
        )
        if scenario == "unknown":
            body = response.json()
            del body["usage"]
            return httpx.Response(200, json=body)
        return response

    async def exercise():
        nonlocal source_id, live_client, live_config, live_plan, next_owner
        async with (
            real_core_http(core) as port,
            httpx.AsyncClient(
                base_url=f"http://127.0.0.1:{port}",
                trust_env=False,
            ) as http,
        ):
            inbound = event(
                core,
                text=URL if reading else "导出Bug还没解决，明天接着看",
                origin=f"onebot-direct:FriendMessage:{core.settings.owner_qq_id}"
                if interrupted
                else None,
            )
            response = await http.post(
                "/internal/v1/inbox/events",
                json=inbound.model_dump(mode="json"),
                headers={
                    "Authorization": "Bearer " + core.settings.bridge_token.get_secret_value(),
                    "X-Idempotency-Key": "life-synthetic-inbound",
                },
            )
            assert response.status_code == 202, response.text
            if reading:
                headers = {
                    "Authorization": "Bearer "
                    + core.settings.observation_adapters["bili"].token.get_secret_value()
                }
                leased = await http.post("/internal/v3/readers/bili/lease", headers=headers)
                assert leased.status_code == 200, leased.text
                item = leased.json()["item"]
                body = envelope("bilibili", level="L1")
                body["lease_token"] = item["lease_token"]
                body["observations"][0]["provenance"] = item["context"]
                body["observations"][0]["fetched_at"] = core.clock()
                body["observations"][0]["evidence"]["fetched_at"] = core.clock()
                completed = await http.post(
                    f"/internal/v3/readers/bili/{item['request_id']}/result",
                    headers=headers,
                    json=body,
                )
                assert completed.status_code == 200, completed.text
                source_id = completed.json()["event_ids"][0]
            core.store.execute(
                "UPDATE jobs SET state='CANCELLED',last_error_code='SYNTHETIC_BACKGROUND_ONLY'"
            )
            core.life_topics.synchronize()
            if reading:
                conversation_topic = core.store.one(
                    "SELECT topic_id FROM life_topics WHERE kind='conversation'"
                )
                core.life_topics.close(conversation_topic["topic_id"], "evt_test_1")
            conversation = core.store.one(
                "SELECT conversation_id FROM events WHERE event_id='evt_test_1'"
            )["conversation_id"]
            options = companion_options(core)
            if reading:
                options.update(rich_input_scope="references_v1", link_scope="owner_links_v1")
                options["core_options"]["rich_input_scope"] = "references_v1"
            if preempt:
                options["core_options"]["proactive_idle_seconds"] = 0
            options["bridge_options"] = {
                "capture_signing_key": Ed25519PrivateKey.generate().private_bytes_raw().hex(),
            }
            options["window_options"]["ends_at"] = (
                (core.clock.now + timedelta(days=2))
                .astimezone(ZoneInfo("Asia/Shanghai"))
                .isoformat()
            )
            options["paid_options"]["expires_at"] = (core.clock.now + timedelta(days=3)).isoformat()
            options["paid_options"]["rates_valid_until"] = (
                core.clock.now + timedelta(days=3)
            ).isoformat()
            options["core_options"]["life_loop"] = core.settings.life_loop
            options["life_shadow"] = LifeShadowApproval(
                scope="owner_private_life_shadow_v1",
                authorization_sha256="e" * 64,
                conversation_id=conversation,
                source_event_ids=[source_id],
                purposes=["PRIVATE_MESSAGE_DRAFT"],
                expires_at=core.clock.now + timedelta(days=2),
                max_items=1,
            )
            if scenario.startswith("qzone"):
                binding = _source(core.store, core.settings, source_id, core.clock())
                options["life_shadow"] = options["life_shadow"].model_copy(
                    update={
                        "qzone_draft_sources": [
                            LifeQZoneSourceGrant(
                                **{
                                    "grant_id": "synthetic-qzone-purpose-one",
                                    "revision": 1,
                                    "purpose": "qzone_draft",
                                    "event_id": source_id,
                                    "source_sha256": binding["source_sha256"],
                                    "expires_at": core.clock.now + timedelta(days=2),
                                    "audience": "owner_only",
                                    "owner_qq_id": core.settings.owner_qq_id,
                                }
                            )
                        ],
                    }
                )
                options["life_shadow"] = LifeShadowApproval.model_validate(
                    options["life_shadow"].model_dump(),
                )
            live_config, plan = live_fixture(core, tmp_path, **options)
            live_plan, live_client = plan, http
            http.headers["Authorization"] = (
                "Bearer " + core.settings.bridge_token.get_secret_value()
            )
            live_text.open_window(
                core.store,
                core.settings,
                core.clock,
                live_text.OpenWindow(window_sha256=plan.sha256),
            )
            core.clock.advance(86400)
            assert live_text.status(core.store, core.settings, core.clock)["approved"]
            await native_worker(core, provider, monkeypatch)
            result = core.store.one("SELECT * FROM life_activity_results")
            if scenario in {"qzone_revoked", "unknown"} or interrupted:
                assert result["text"] == ""
            else:
                assert result["state"] == "SHADOW", core.store.rows("SELECT * FROM jobs")
            assert len(calls) == 1 and calls[0]["sources"][0]["event_id"] == source_id
            usage = core.store.one("SELECT * FROM model_usage WHERE job_id=?", (result["job_id"],))
            assert usage["state"] == ("UNKNOWN" if scenario == "unknown" else "SETTLED")
            assert usage["billed_micro_usd"] > 0
            assert core.store.one(
                "SELECT io_started_at FROM model_admissions WHERE request_id=?",
                (usage["request_id"],),
            )["io_started_at"]
            owner = {"Authorization": "Bearer " + core.settings.control_token.get_secret_value()}
            preview = await http.get("/internal/v2/life/topics", headers=owner)
            assert preview.status_code == 200 and preview.headers["Cache-Control"] == "no-store"
            visible = [r for t in preview.json()["items"] for r in t["results"]]
            if scenario == "qzone_revoked":
                assert not visible
            elif interrupted or scenario == "unknown":
                assert not any(row["state"] == "SHADOW" for row in visible)
            else:
                assert visible
            if scenario == "qzone":
                draft = LifeQZoneDraft.model_validate(visible[0]["qzone_draft"])
                assert draft.purpose == "qzone_draft" and draft.audience == "owner_only"
                assert draft.sources[0].event_id == source_id
                assert draft.sources[0].revision == 1
            assert core.store.rows("SELECT * FROM outbox") == []
            assert core.store.rows("SELECT * FROM social_autonomy_plans") == []
            if scenario == "unknown":
                assert live_text.stopped(core.store, plan.sha256)
                assert not live_text.status(core.store, core.settings, core.clock)["approved"]
            if interrupted:
                assert (
                    core.store.one("SELECT state FROM jobs WHERE job_id=?", (result["job_id"],))[
                        "state"
                    ]
                    == "CANCELLED"
                )
                assert not live_text.stopped(core.store, plan.sha256)
                if next_owner is None:
                    next_owner, response = await admit(
                        http, live_config, plan, core.clock.now, number=3
                    )
                    assert response.status_code == 202, response.text
                assert live_text.status(core.store, core.settings, core.clock)["approved"]
                async with httpx.AsyncClient(
                    transport=httpx.MockTransport(
                        lambda request: httpx.Response(
                            200,
                            json={
                                "choices": [{"message": {"content": "收到，我们接着聊。"}}],
                                "usage": {"prompt_tokens": 180, "completion_tokens": 60},
                            },
                        )
                    )
                ) as foreground:
                    core.model = ModelClient(core.settings, foreground)
                    assert await core.run_once("next-foreground")
                assert (
                    core.store.one(
                        "SELECT state FROM jobs WHERE event_id=?", (next_owner["event_id"],)
                    )["state"]
                    == "SUCCEEDED"
                )
                assert not live_text.stopped(core.store, plan.sha256)
                assert all(
                    row["origin_event_id"] == next_owner["event_id"]
                    for row in core.store.rows("SELECT * FROM outbox")
                )

    run(exercise())
