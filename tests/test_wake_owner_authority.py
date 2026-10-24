"""Real native goals cannot advance through a Wake purpose alone."""

import pytest

from companion.autonomous_selector import native_selector
from tests.conftest import ingest, run
from tests.test_goals import setup_goal
from tests.test_owner_authority import activate, prepare


def invoke(core, entry):
    if entry == "direct":
        return bool(core.wake.evaluate())
    lane = next(lane for lane in native_selector(core, "wake-purpose").lanes
                if lane.name == "wake")
    return run(lane.run()).progressed


@pytest.mark.parametrize("entry", ["direct", "selector"])
@pytest.mark.parametrize("missing", ["wake", "goals", "exploration"])
def test_wake_missing_purpose_preserves_real_goal_and_steps(core, tmp_path, entry, missing):
    goal_id = setup_goal(core)
    policy = prepare(core, tmp_path).model_copy(update={
        "purposes": [p for p in ("wake", "goals", "exploration") if p != missing],
    })
    activate(core, policy)
    before = core.store.one("SELECT * FROM goals WHERE goal_id=?", (goal_id,))
    changes = core.store.db.total_changes
    assert not invoke(core, entry)
    assert core.store.one("SELECT * FROM goals WHERE goal_id=?", (goal_id,)) == before
    assert core.store.rows("SELECT * FROM goal_steps") == []
    assert core.store.rows("SELECT * FROM wake_decisions") == []
    assert core.store.db.total_changes == changes


@pytest.mark.parametrize("entry", ["direct", "selector"])
def test_authorized_wake_without_affect_does_not_read_it(core, tmp_path, monkeypatch, entry):
    goal_id = setup_goal(core)
    core.settings.affect_enabled = True
    policy = prepare(core, tmp_path).model_copy(update={
        "purposes": ["wake", "goals", "exploration"],
    })
    activate(core, policy)

    def forbidden_snapshot():
        pytest.fail("Affect read without its independent purpose")

    monkeypatch.setattr(core.wake.affect, "snapshot", forbidden_snapshot)
    assert invoke(core, entry)
    assert core.goals.get(goal_id)["state"] == "QUEUED"
    assert core.store.one("SELECT goal_id,state FROM goal_steps") == {
        "goal_id": goal_id, "state": "QUEUED",
    }
    assert core.store.one("SELECT decision,reason_code FROM wake_decisions") == {
        "decision": "EXPLORE", "reason_code": "GOAL_DUE",
    }
    assert core.store.rows("SELECT * FROM model_usage") == []
    assert core.store.rows("SELECT * FROM outbox") == []


def test_authorized_affect_pause_still_stops_wake(core, tmp_path):
    goal_id = setup_goal(core)
    core.settings.affect_enabled = True
    ingest(core, 2, "先别聊这个")
    core.store.execute("UPDATE jobs SET state='CANCELLED',lease_token=NULL WHERE state='QUEUED'")
    policy = prepare(core, tmp_path).model_copy(update={
        "purposes": ["wake", "goals", "exploration", "affect"],
    })
    activate(core, policy)
    assert core.wake.affect.snapshot()["state"]["engagement"] == "pause"
    assert core.wake.evaluate() == []
    assert core.goals.get(goal_id)["state"] == "CANDIDATE"
    assert core.store.rows("SELECT * FROM goal_steps") == []
    assert core.store.one("SELECT reason_code FROM wake_decisions") == {
        "reason_code": "OWNER_REQUESTED_PAUSE",
    }
