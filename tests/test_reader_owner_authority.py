"""Native goal Reader checks the original grant before each external request."""

import json
import ssl

import httpcore
import httpx
import pytest

from companion import owner_authority
from companion.contracts import CoreError
from companion.goals import GoalProposal, GoalScope
from companion.owner_authority_protocol import AuthorityStop
from source_reader import worker_v1
from source_reader.bilibili import BilibiliReader
from source_reader.network import PublicHTTP
from tests.conftest import run
from tests.test_exploration import URL
from tests.test_owner_authority import activate
from tests.test_reading_followup import READER_AUTH, admit, window
from tests.test_source_reader import META, PLAYER, SUBTITLE, Responses, response


async def goal_read(core, client, config, plan):
    core.settings.goals_enabled = True
    plan.policy = plan.policy.model_copy(update={"purposes": [*plan.policy.purposes, "goals"]})
    changed, _ = activate(core, plan.policy, revision=1, operation="enable-native-goal-reader")
    plan.authority = changed["authority"]
    event, _ = await admit(client, core, config, plan, 1, "请读这份资料")
    goal_id = core.goals.propose(GoalProposal(topic="读取恒星资料",
        source_event_ids=[event["event_id"]], scope=GoalScope(operation="read_content", url=URL)))
    core.goals.accept(goal_id)
    assert core.exploration.schedule_goal()
    # This test exercises the independent goal lease, with no chat model worker pending.
    core.store.execute("UPDATE jobs SET state='CANCELLED' WHERE kind='chat'")
    return goal_id


@pytest.mark.parametrize("change", ["stop", "purpose", "revision"])
@pytest.mark.parametrize("before_first", [True, False])
def test_goal_worker_rechecks_original_authority_and_settles_started_io(
    core, tmp_path, monkeypatch, change, before_first,
):
    async def scenario():
        async with window(core, tmp_path, monkeypatch) as (client, config, plan):
            await goal_read(core, client, config, plan)
            original = core.store.one("SELECT authority_json FROM exploration_requests")
            backend = Responses(response(META), response(PLAYER), response(SUBTITLE))
            pool = httpcore.AsyncConnectionPool(
                ssl_context=ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT),
                network_backend=backend, max_keepalive_connections=0,
            )
            changed = False
            first_response_complete = False

            class PublicAfterResponse(PublicHTTP):
                async def get(self, *args, **kwargs):
                    nonlocal first_response_complete
                    result = await super().get(*args, **kwargs)
                    first_response_complete = True
                    return result

            async def before_request(request):
                nonlocal changed
                if request.url.path.endswith("/validate") and not changed and (
                    before_first or first_response_complete
                ):
                    changed = True
                    if change == "stop":
                        owner_authority.stop(core.store, core.settings, core.clock,
                            AuthorityStop(operation_id="stop-goal-reader",
                                          authority=plan.authority))
                    else:
                        policy = plan.policy
                        if change == "purpose":
                            policy = policy.model_copy(update={"purposes": [
                                p for p in policy.purposes if p != "content"]})
                        activate(core, policy, revision=2, operation="replace-goal-reader-grant")

            async with PublicAfterResponse(pool) as public, httpx.AsyncClient(
                base_url=str(client.base_url), headers=READER_AUTH, trust_env=False,
                event_hooks={"request": [before_request]},
            ) as reader_http:
                assert await worker_v1.tick(reader_http, BilibiliReader(public, clock=core.clock),
                                            "bili", source="bilibili", clock=lambda: core.clock.now)
            assert changed
            assert len(backend.requests) == (0 if before_first else 1)
            attempt = core.store.one("SELECT * FROM reading_attempts")
            read = core.store.one("SELECT * FROM exploration_requests")
            assert attempt["state"] == read["state"] == (
                "CANCELLED" if before_first else "UNKNOWN")
            assert attempt["observed_requests"] >= len(backend.requests)
            if before_first:
                assert read["used_requests"] == read["used_bytes"] == 0
            else:
                assert read["used_requests"] is read["used_bytes"] is None
                assert read["reserved_requests"] >= attempt["observed_requests"] >= 1
            assert (attempt["observed_bytes"] > 0) is (not before_first)
            assert read["authority_json"] == original["authority_json"]
            assert not core.store.one("SELECT 1 FROM observations")
    run(scenario())


@pytest.mark.parametrize("binding", ["goal", "request"])
def test_unbound_historical_work_cannot_receive_current_grant(core, tmp_path, monkeypatch, binding):
    async def scenario():
        async with window(core, tmp_path, monkeypatch) as (client, config, plan):
            goal_id = await goal_read(core, client, config, plan)
            table = "goals" if binding == "goal" else "exploration_requests"
            core.store.execute(f"UPDATE {table} SET authority_json='{{}}'")
            with pytest.raises(CoreError, match="OWNER_AUTHORITY_REFERENCE_INVALID"):
                if binding == "goal":
                    core.goals.validate_authority(core.goals.get(goal_id))
                else:
                    core.reading.lease("bili")
            assert not core.store.one("SELECT 1 FROM reading_attempts")
            assert json.loads(core.store.one(f"SELECT authority_json FROM {table}")[
                "authority_json"]) == {}
    run(scenario())
