"""Native evidence transitions, reconstruction and withdrawal of attention views."""

from contextlib import closing

import pytest
from cryptography.fernet import Fernet

from companion.backup import backup, restore
from companion.contracts import CoreError, DeliveryResult, LeaseRequest
from companion.memory import Memory
from companion.memory_projections import MemoryProjections
from companion.store import Store
from companion.transport import begin_send, delivery_result, lease_deliveries, reconcile_delivery
from tests.conftest import run
from tests.test_goals import setup_goal
from tests.test_m1 import remember
from tests.test_next_memory_context import episode
from tests.test_observations import enable_observations, observation


def fetched(core, goal):
    enable_observations(core, "web")
    core.settings.web_reading_enabled = True
    core.settings.observation_adapters["reader"].allowed_hosts = ["example.org"]
    item = observation(core, source="web", source_event_ids=core.goals.source_ids(goal))
    core.observations.append("reader", item, reading=True)
    return item.event_id


def test_temperature_is_rebuilt_without_changing_recall_or_facts(core):
    mid = remember(core, 1, "项目封面采用松绿色", confirmed=True)
    eid = episode(core, 1)
    view = MemoryProjections(core.memory)
    scope = {"memory_ids": [mid], "episode_ids": [eid]}
    before = core.store.db.total_changes
    assert view.snapshot(core.clock(), **scope)["memories"][0]["temperature"] == "Warm"
    hot = view.snapshot(core.clock(), current_event_ids=["evt_test_1"], **scope)
    assert hot["memories"][0]["temperature"] == "Hot"
    assert hot["episodes"][0]["temperature"] == "Hot"
    core.clock.advance(8 * 86400)
    cold = view.snapshot(core.clock(), **scope)
    assert cold["memories"][0]["temperature"] == "Cold"
    assert cold["episodes"][0]["temperature"] == "Cold"
    assert core.store.db.total_changes == before
    assert mid in {row["memory_id"] for row in core.memory.search("松绿色", now=core.clock())}
    assert view.snapshot(core.clock(), used_memory_ids=[mid], **scope)["memories"][0][
        "temperature"] == "Hot"
    with closing(Store(core.settings.database)) as reopened:
        rebuilt = MemoryProjections(Memory(reopened, core.settings, core.ledger))
        assert rebuilt.snapshot(core.clock(), **scope) == cold
    assert view.snapshot(core.clock(), memory_ids=[mid, mid], episode_ids=[eid, eid]) == cold


def test_goal_lifecycle_promotes_attention_without_inventing_completion(core):
    goal = setup_goal(core)
    core.settings.memory_enabled = True
    eid = episode(core, 1)
    view = MemoryProjections(core.memory)

    def read():
        return view.snapshot(core.clock(), episode_ids=[eid])["episodes"][0]

    assert read()["goals"][0]["state"] == "discussed"
    core.clock.advance(8 * 86400)
    assert read()["temperature"] == "Cold"
    core.goals.accept(goal)
    assert read()["goals"][0]["state"] == "planned"
    assert read()["temperature"] == "Hot"
    step = core.goals.claim()
    assert read()["goals"][0]["state"] == "running"
    core.store.execute("UPDATE goal_steps SET state='EXECUTION_UNCERTAIN' WHERE goal_id=?", (goal,))
    assert read()["goals"][0]["state"] == "UNKNOWN"
    core.store.execute("UPDATE goal_steps SET state='RUNNING' WHERE goal_id=?", (goal,))
    result = fetched(core, goal)
    core.goals.finish(step, result)
    completed = read()["goals"][0]
    assert completed["state"] == "done" and not completed["told"] and not completed["closed"]
    core.store.execute("UPDATE observations SET result_status='UNKNOWN' WHERE event_id=?",
                       (result,))
    assert read()["goals"][0]["state"] == "UNKNOWN"
    assert read()["temperature"] == "Hot"


@pytest.mark.parametrize("invalid", ["expired", "erased", "upstream", "scope", "future"])
def test_projection_cannot_revive_unavailable_sources(core, invalid):
    mid = remember(core, 1, "项目封面采用松绿色", confirmed=True)
    eid = episode(core, 1)
    view = MemoryProjections(core.memory)
    scope = {"memory_ids": [mid], "episode_ids": [eid]}
    assert view.snapshot(core.clock(), **scope)["episodes"]
    if invalid == "expired":
        core.clock.advance((core.settings.raw_event_retention_days + 1) * 86400)
    elif invalid == "erased":
        core.memory.decide(mid, "forget", core.clock())
    elif invalid == "scope":
        core.store.execute("UPDATE conversations SET scope='group'")
    elif invalid == "future":
        core.store.execute("UPDATE events SET occurred_at='2099-01-01T00:00:00+00:00'")
    else:
        remember(core, 2, "独立的上游原文", confirmed=True)
        core.store.insert("event_dependencies", event_id="evt_test_1", source_event_id="evt_test_2")
        core.store.execute("UPDATE events SET redacted=1 WHERE event_id='evt_test_2'")
    result = view.snapshot(core.clock(), **scope)
    assert result["memories"] == result["episodes"] == []


def test_explicit_scopes_and_owner_are_required(core):
    mid = remember(core, 1, "项目封面采用松绿色", confirmed=True)
    eid = episode(core, 1)
    view = MemoryProjections(core.memory)
    assert view.snapshot(core.clock(), memory_ids=[mid], episode_ids=[eid], event_ids=[])[
        "episodes"] == []
    with pytest.raises(CoreError, match="ACCESS_DENIED"):
        view.snapshot(core.clock(), actor="stranger", memory_ids=[mid])
    with pytest.raises(CoreError, match="INVALID_PROJECTION_SCOPE"):
        view.snapshot(core.clock(), used_memory_ids=[mid])
    with pytest.raises(CoreError, match="INVALID_PROJECTION_SCOPE"):
        view.snapshot(core.clock(), memory_ids=[mid] * 101)


def test_failure_and_cancellation_do_not_mean_told(core):
    goal = setup_goal(core)
    core.settings.memory_enabled = True
    eid = episode(core, 1)
    core.goals.accept(goal)
    step = core.goals.claim()
    core.goals.fail(step, "SOURCE_FAILED")
    view = MemoryProjections(core.memory)
    failed = view.snapshot(core.clock(), episode_ids=[eid])["episodes"][0]["goals"][0]
    assert failed["state"] == "failed" and not failed["told"] and not failed["closed"]
    other = setup_goal(core, number=2)
    other_episode = episode(core, 2)
    core.goals.cancel(other)
    cancelled = view.snapshot(core.clock(), episode_ids=[other_episode])["episodes"][0]["goals"][0]
    assert cancelled["state"] == "closed" and cancelled["closed"] and not cancelled["told"]


def test_commitment_closure_requires_actual_sent_feedback(core):
    goal = setup_goal(core, commitment=True)
    core.settings.memory_enabled = True
    eid = episode(core, 1)
    step = core.goals.claim()
    core.goals.finish(step, fetched(core, goal))
    assert run(core.run_once())
    view = MemoryProjections(core.memory)

    def item():
        return view.snapshot(core.clock(), episode_ids=[eid])["episodes"][0]["goals"][0]

    assert item()["state"] == "done" and not item()["told"] and not item()["closed"]
    # Native acknowledgement must settle before its dependent commitment feedback.
    for outcome in ("SENT", "DELIVERY_UNCERTAIN"):
        delivery = lease_deliveries(core.store, core.settings, LeaseRequest(worker_id="bridge"),
                                    core.clock())["items"][0]
        begin_send(core.store, delivery["message_id"], delivery["lease_token"],
                   core.clock(), core.settings)
        delivery_result(core.store, core.settings, delivery["message_id"],
                        DeliveryResult(lease_token=delivery["lease_token"], result=outcome),
                        core.clock())
    assert not item()["told"] and not item()["closed"]
    reconcile_delivery(core.store, delivery["message_id"], "SENT", core.clock())
    assert item()["told"] and item()["closed"]


def test_restore_replays_erasure_before_projection_reconstruction(core, tmp_path):
    mid = remember(core, 1, "项目封面采用松绿色", confirmed=True)
    eid = episode(core, 1)
    key = Fernet.generate_key()
    archive, restored = tmp_path / "before.enc", tmp_path / "restored.db"
    backup(core.settings.database, archive, key)
    core.memory.decide(mid, "forget", core.clock())
    restore(archive, restored, key, core.settings.erasure_ledger)
    with closing(Store(str(restored))) as store:
        view = MemoryProjections(Memory(store, core.settings, core.ledger))
        result = view.snapshot(core.clock(), memory_ids=[mid], episode_ids=[eid])
        assert result["memories"] == result["episodes"] == []


@pytest.mark.parametrize("damage", ["simulation", "missing_step", "unrelated", "result_scope"])
def test_goal_done_label_alone_is_insufficient(core, damage):
    goal = setup_goal(core)
    core.settings.memory_enabled = True
    eid = episode(core, 1)
    core.goals.accept(goal)
    step = core.goals.claim()
    result = fetched(core, goal)
    core.goals.finish(step, result)
    if damage == "simulation":
        core.store.execute("UPDATE events SET source='observation.simulation' WHERE event_id=?",
                           (result,))
    elif damage == "missing_step":
        core.store.execute("DELETE FROM goal_steps WHERE goal_id=?", (goal,))
    elif damage == "unrelated":
        core.store.execute("DELETE FROM event_dependencies WHERE event_id=?", (result,))
    else:
        core.store.execute("UPDATE events SET privacy_scope='public' WHERE event_id=?", (result,))
    snapshot = MemoryProjections(core.memory).snapshot(core.clock(), episode_ids=[eid])
    assert snapshot["episodes"][0]["goals"][0]["state"] == "UNKNOWN"
