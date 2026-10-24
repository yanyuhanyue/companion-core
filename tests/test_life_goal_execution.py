"""Life choices reuse the durable native goal queue without delivery authority."""

import json
from datetime import timedelta
from zoneinfo import ZoneInfo

import httpx
import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from companion import live_text
from companion.contracts import CoreError
from companion.core import Core
from companion.exploration import ReadResult
from companion.goals import GoalProposal
from companion.life_protocol import LifeLoopConfig, LifeShadowApproval
from companion.life_topics import _source, authority
from companion.store import encode
from source_reader.bilibili import BilibiliReader
from source_reader.network import PublicHTTP
from source_reader.worker_v1 import tick
from tests.conftest import event, run
from tests.test_exploration import URL, configure
from tests.test_heybox_reader import mock_pool
from tests.test_life_topics import assert_no_delivery, bug, due, proposal
from tests.test_life_worker_integration import native_worker
from tests.test_live_text_window import live_fixture
from tests.test_onebot_capture import real_core_http
from tests.test_private_companion import companion_options
from tests.test_source_reader import META, PLAYER, SUBTITLE, response


@pytest.mark.parametrize("action", ["CONTINUE", "EXPLORE"])
@pytest.mark.parametrize("enabled", [False, True])
def test_choice_submits_native_goal_once(core, action, enabled):
    bug(core)
    configure(core)
    core.settings.life_loop.read_goals_enabled = enabled
    native = GoalProposal(
        topic="阅读游戏介绍",
        source_event_ids=["evt_test_1"],
        scope={"operation": "read_content", "url": URL},
    )
    extra = (
        {"goal_id": core.goals.propose(native)}
        if action == "CONTINUE"
        else {"goal": native.model_dump(mode="json")}
    )
    proposal(core, action=action, destination="NONE", text="", **extra)
    job = due(core)
    assert job
    assert run(core.run_once(job_id=job))
    result = core.store.one("SELECT * FROM life_activity_results WHERE job_id=?", (job,))
    assert result["state"] == "SHADOW", core.store.rows("SELECT * FROM jobs")
    assert bool(result["goal_id"]) == enabled
    if enabled:
        goal = core.goals.get(result["goal_id"])
        assert goal["state"] == "QUEUED"
        assert core.exploration.schedule_goal()
        assert len(core.store.rows("SELECT * FROM exploration_requests")) == 1
        with Core(core.settings, core.model, core.clock) as restarted:
            assert not run(restarted.run_once(job_id=job))
            assert not restarted.exploration.schedule_goal()
        assert len(core.store.rows("SELECT * FROM goal_steps")) == 1
        assert len(core.store.rows("SELECT * FROM exploration_requests")) == 1
    else:
        assert not core.store.rows("SELECT * FROM goal_steps")
    assert_no_delivery(core)


def test_live_authority_full_loop_independent_workers(core, tmp_path, monkeypatch):
    configure(core)
    core.settings.observation_adapters["bili"].reader_max_requests = 4
    core.settings.life_loop = LifeLoopConfig(mode="SHADOW", read_goals_enabled=True)
    calls = []

    def model(request):
        material = json.loads(json.loads(request.content)["messages"][-1]["content"])
        assert material["kind"] == "life"
        calls.append(material)
        ids = [source["event_id"] for source in material["sources"]]
        choice = (
            {
                "action": "EXPLORE",
                "destination": "NONE",
                "source_event_ids": ids,
                "reason": "查看原文",
                "goal": {
                    "topic": "阅读星空介绍",
                    "source_event_ids": ids,
                    "scope": {"operation": "read_content", "url": URL},
                },
            }
            if len(calls) == 1
            else {
                "action": "SHARE",
                "destination": "PRIVATE_MESSAGE_DRAFT",
                "source_event_ids": ids,
                "reason": "实际阅读结果",
                "text": "读到恒星颜色与温度有关。",
            }
        )
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": encode(choice)}}],
                "usage": {"prompt_tokens": 180, "completion_tokens": 60},
            },
        )

    async def exercise():
        async def require_success(response):
            await response.aread()
            assert response.status_code < 400, (
                response.text,
                core.store.rows("SELECT scenario,state FROM model_usage"),
                core.store.rows(
                    "SELECT key,value FROM runtime_meta WHERE key LIKE '%stop%' "
                    "OR key LIKE '%blocked%'"
                ),
            )

        async with (
            real_core_http(core) as port,
            httpx.AsyncClient(
                base_url=f"http://127.0.0.1:{port}",
                trust_env=False,
                event_hooks={"response": [require_success]},
            ) as client,
        ):
            inbound = event(core, text="有空可以看看这个星空介绍，明天聊聊。")
            admitted = await client.post(
                "/internal/v1/inbox/events",
                json=inbound.model_dump(mode="json"),
                headers={
                    "Authorization": "Bearer " + core.settings.bridge_token.get_secret_value(),
                    "X-Idempotency-Key": "life-execution-owner-input",
                },
            )
            assert admitted.status_code == 202, admitted.text
            core.store.execute(
                "UPDATE jobs SET state='CANCELLED',last_error_code='SYNTHETIC_BACKGROUND_ONLY'"
            )
            options = companion_options(core)
            options.update(rich_input_scope="references_v1", link_scope="owner_links_v1")
            options["core_options"].update(rich_input_scope="references_v1", content_enabled=True)
            options["core_options"]["extraction_min_events"] = 100
            options["core_options"]["diary_min_source_events"] = 100
            options["core_options"]["reflection_min_sources"] = 100
            options["bridge_options"] = {
                "capture_signing_key": Ed25519PrivateKey.generate().private_bytes_raw().hex(),
            }
            options["window_options"]["ends_at"] = (
                (core.clock.now + timedelta(days=2))
                .astimezone(ZoneInfo("Asia/Shanghai"))
                .isoformat()
            )
            options["core_options"]["life_loop"] = core.settings.life_loop
            options["life_shadow"] = LifeShadowApproval(
                scope="owner_private_life_shadow_v1",
                authorization_sha256="e" * 64,
                conversation_id=core.store.one(
                    "SELECT conversation_id FROM events WHERE event_id=?", (inbound.event_id,)
                )["conversation_id"],
                source_event_ids=[inbound.event_id],
                purposes=["PRIVATE_MESSAGE_DRAFT"],
                expires_at=core.clock.now + timedelta(days=2),
                max_items=2,
            )
            _, plan = live_fixture(core, tmp_path, **options)
            live_text.open_window(
                core.store,
                core.settings,
                core.clock,
                live_text.OpenWindow(window_sha256=plan.sha256),
            )
            core.life_topics.synchronize()
            core.clock.advance(86400)
            await native_worker(core, model, monkeypatch)
            result = core.store.one("SELECT * FROM life_activity_results")
            assert result["goal_id"], core.store.rows("SELECT state,last_error_code FROM jobs")
            pool, backend = mock_pool(response(META), response(PLAYER), response(SUBTITLE))
            client.headers["Authorization"] = (
                "Bearer " + core.settings.observation_adapters["bili"].token.get_secret_value()
            )
            async with PublicHTTP(pool) as public:
                assert await tick(
                    client,
                    BilibiliReader(public, clock=core.clock),
                    "bili",
                    source="bilibili",
                    clock=lambda: core.clock.now,
                )
            assert len(backend.requests) == 3
            goal = core.goals.get(result["goal_id"])
            assert goal["state"] == "DONE"
            core.clock.advance(3600)
            await native_worker(core, model, monkeypatch)
            shared = core.store.one("SELECT * FROM life_activity_results WHERE action='SHARE'")
            assert shared and shared["state"] == "SHADOW", core.store.rows("SELECT * FROM jobs")
            assert [source["event_id"] for source in calls[1]["sources"]] == [
                goal["result_event_id"]
            ]
            assert "QZONE_DRAFT" not in calls[1]["allowed_destinations"]
            assert len(calls) == 2
            assert_no_delivery(core)
            sources = [_source(core.store, core.settings, goal["result_event_id"], core.clock())]
            scope = core.settings.live_text_window.life_shadow

            def current_authority():
                return authority(
                    core.store,
                    core.settings,
                    scope.conversation_id,
                    sources,
                    core.clock(),
                    check_limit=False,
                )

            assert current_authority()["destinations"] == ["PRIVATE_MESSAGE_DRAFT"]
            for mutation in (
                "UPDATE life_activity_results SET goal_id=NULL WHERE action='EXPLORE'",
                "UPDATE life_activity_results SET authority_sha256='changed' "
                "WHERE action='EXPLORE'",
                "UPDATE life_activity_results SET source_fingerprints_json='{}' "
                "WHERE action='EXPLORE'",
                "UPDATE goals SET state='FAILED'",
                "UPDATE goal_steps SET outcome_event_id=NULL",
                "UPDATE model_usage SET state='UNKNOWN'",
                "UPDATE events SET body_json='{\"text\":\"changed\"}' WHERE event_id='evt_test_1'",
                "UPDATE events SET redacted=1 WHERE event_id='evt_test_1'",
            ):
                core.store.execute("SAVEPOINT authority_negative")
                try:
                    core.store.execute(mutation)
                    with pytest.raises(CoreError, match="LIFE_SHADOW_SCOPE_REQUIRED"):
                        current_authority()
                finally:
                    core.store.execute("ROLLBACK TO authority_negative")
                    core.store.execute("RELEASE authority_negative")
            with core.store.transaction():
                core.ledger.expire_events(core.store, [inbound.event_id], core.clock())
            assert not core.store.rows("SELECT * FROM life_activity_results WHERE state='SHADOW'")
            with pytest.raises(CoreError, match="GOAL_NOT_FOUND"):
                core.goals.get(goal["goal_id"])

    run(exercise())


@pytest.mark.parametrize("gate", ["pause", "budget", "adapter", "erasure", "grant"])
def test_native_read_gates_still_block_life_choices(core, gate):
    bug(core)
    configure(core)
    core.settings.life_loop.read_goals_enabled = True
    proposal(
        core,
        action="EXPLORE",
        destination="NONE",
        text="",
        goal={
            "topic": "阅读星空介绍",
            "source_event_ids": ["evt_test_1"],
            "scope": {"operation": "read_content", "url": URL},
        },
    )
    job = due(core)
    assert run(core.run_once(job_id=job))
    if gate == "pause":
        core.goals.pause_autonomy(True)
    elif gate == "budget":
        core.settings.exploration_daily_requests = 0
    elif gate == "adapter":
        core.settings.observation_enabled = False
    elif gate == "erasure":
        with core.store.transaction():
            core.ledger.expire_events(core.store, ["evt_test_1"], core.clock())
    else:
        core.settings.observation_adapters["bili"].source_grant_required = True
        assert core.exploration.schedule_goal()
        with pytest.raises(CoreError):
            core.reading.lease("bili")
        assert not core.store.rows("SELECT * FROM reading_attempts")
        assert_no_delivery(core)
        return
    assert not core.exploration.schedule_goal()
    assert not core.store.rows("SELECT * FROM exploration_requests")
    assert_no_delivery(core)


@pytest.mark.parametrize("outcome", ["FAILED", "UNKNOWN"])
def test_unsuccessful_read_does_not_invent_share_or_repeat_receipt(core, outcome):
    bug(core)
    configure(core)
    core.settings.life_loop.read_goals_enabled = True
    proposal(
        core,
        action="EXPLORE",
        destination="NONE",
        text="",
        goal={
            "topic": "阅读星空介绍",
            "source_event_ids": ["evt_test_1"],
            "scope": {"operation": "read_content", "url": URL},
        },
    )
    assert run(core.run_once(job_id=due(core)))
    assert core.exploration.schedule_goal()
    item = core.exploration.lease("bili")
    receipt = ReadResult(
        lease_token=item["lease_token"],
        status=outcome,
        error_code="READ_TIMEOUT",
        used_requests=1 if outcome == "FAILED" else None,
        used_bytes=20 if outcome == "FAILED" else None,
    )
    core.exploration.complete("bili", item["request_id"], receipt)
    assert core.exploration.complete("bili", item["request_id"], receipt)["duplicate"]
    assert not core.store.rows("SELECT * FROM observations")
    assert core.store.one("SELECT state FROM exploration_requests")["state"] == outcome
    core.clock.advance(3600)
    assert core.life_topics.schedule() is None
    assert len(core.model.calls) == 1
    assert_no_delivery(core)


def test_native_goal_receipt_becomes_next_life_source(core):
    bug(core)
    configure(core)
    core.settings.life_loop.read_goals_enabled = True
    proposal(
        core,
        action="EXPLORE",
        destination="NONE",
        text="",
        goal={
            "topic": "阅读星空介绍",
            "source_event_ids": ["evt_test_1"],
            "scope": {"operation": "read_content", "url": URL},
        },
    )
    core.settings.observation_adapters["bili"].reader_max_requests = 4
    job = due(core)
    assert run(core.run_once(job_id=job))
    assert core.exploration.schedule_goal()

    async def read():
        pool, backend = mock_pool(response(META), response(PLAYER), response(SUBTITLE))
        async with (
            real_core_http(core) as port,
            httpx.AsyncClient(
                base_url=f"http://127.0.0.1:{port}",
                trust_env=False,
                headers={
                    "Authorization": "Bearer "
                    + core.settings.observation_adapters["bili"].token.get_secret_value()
                },
            ) as client,
            PublicHTTP(pool) as public,
        ):
            assert await tick(
                client,
                BilibiliReader(public, clock=core.clock),
                "bili",
                source="bilibili",
                clock=lambda: core.clock.now,
            )
            assert not await tick(
                client,
                BilibiliReader(public, clock=core.clock),
                "bili",
                source="bilibili",
                clock=lambda: core.clock.now,
            )
        assert len(backend.requests) == 3

    run(read())
    goal = core.store.one("SELECT * FROM goals")
    assert goal["state"] == "DONE"
    source_id = goal["result_event_id"]
    core.clock.advance(3600)
    core.model.activity = encode(
        {
            "action": "SHARE",
            "destination": "PRIVATE_MESSAGE_DRAFT",
            "source_event_ids": [source_id],
            "reason": "实际阅读结果",
            "text": "恒星颜色与温度有关。",
        }
    )
    next_job = core.life_topics.schedule()
    assert next_job
    assert run(core.run_once(job_id=next_job))
    result = core.store.one("SELECT * FROM life_activity_results WHERE job_id=?", (next_job,))
    assert result["state"] == "SHADOW", core.store.rows("SELECT * FROM jobs")
    assert result["action"] == "SHARE"
    assert_no_delivery(core)
