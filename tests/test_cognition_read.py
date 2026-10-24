import json

import pytest
from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.testclient import TestClient

from companion.cognition_api import register_cognition_routes
from companion.cognition_read import KINDS, CognitionRead, measurement
from companion.contracts import CoreError
from companion.core import Core
from companion.privacy import advance_epoch
from tests.test_goals import setup_goal


@pytest.mark.parametrize("kind", KINDS)
def test_empty_native_collections_do_not_execute(core, kind):
    before = core.store.db.total_changes
    assert CognitionRead(core).page(kind)["items"] == []
    assert core.store.db.total_changes == before
    assert core.model.calls == []


def test_goal_native_sources_and_independent_reader(core):
    goal = setup_goal(core)
    reader = Core(core.settings, core.model, core.clock)
    try:
        result = CognitionRead(reader).page("goal")
        assert result["items"][0]["id"] == goal
        assert result["items"][0]["execution"]["state"] == "discussed"
        assert "天文" not in json.dumps(result, ensure_ascii=False)
        core.store.execute("UPDATE events SET redacted=1 WHERE event_id='evt_test_1'")
        assert CognitionRead(reader).page("goal")["items"] == []
    finally:
        reader.store.db.close()


def test_wake_native_choice_and_cursor(core):
    setup_goal(core)
    core.settings.exploration_enabled = False
    core.wake.evaluate("evt_test_1")
    core.wake.evaluate()
    read = CognitionRead(core)
    first = read.page("wake", limit=1)
    second = read.page("wake", limit=1, before=first["next_cursor"])
    assert first["truncated"] and not second["truncated"]
    assert first["items"][0]["id"] != second["items"][0]["id"]
    assert first["items"][0]["choice"] == "SKIP"
    assert first["candidate_set"] == "NOT_RECORDED"
    with pytest.raises(CoreError, match="CURSOR_UNAVAILABLE"):
        read.page("wake", before="absent")


def test_privacy_change_during_read_fails_closed(core, monkeypatch):
    setup_goal(core)
    view = CognitionRead(core)
    original = view._record

    def changed(*args):
        result = original(*args)
        advance_epoch(core.store)
        return result

    monkeypatch.setattr(view, "_record", changed)
    with pytest.raises(CoreError, match="PRIVACY_CHANGED"):
        view.page("goal")


def test_measurement_is_saved_numeric_metadata_only():
    assert measurement("{}") == {"state": "NOT_RECORDED"}
    value = measurement(json.dumps({"count_kind": "estimate", "input_tokens": 37,
                                   "endpoint": "secret", "method": "private prompt",
                                   "reservation_margin": 3, "output_allowance": 20}))
    assert value == {"state": "RECORDED", "count_kind": "estimate", "input_tokens": 37,
                     "reservation_margin": 3, "output_allowance": 20}


def test_explicit_route_contract(core):
    # Module contract only. Application composition is owned by the integration lane.
    app = FastAPI()
    app.state.core = core

    def owner(authorization: str = Header(default="")):
        if authorization != "Bearer synthetic-owner":
            raise HTTPException(401)

    register_cognition_routes(app, [Depends(owner)])
    with TestClient(app) as client:
        assert client.post("/internal/v2/cognition", json={"kind": "wake"}).status_code == 401
        headers = {"Authorization": "Bearer synthetic-owner"}
        response = client.post("/internal/v2/cognition", json={"kind": "wake"}, headers=headers)
        assert response.status_code == 200
        assert response.headers["cache-control"] == "no-store"
        for selection in ({"kind": "wake", "limit": 51}, {"kind": "wake", "limit": True},
                          {"kind": "wake", "actor": "owner"}, {"kind": "unknown"}):
            assert client.post("/internal/v2/cognition", json=selection,
                               headers=headers).status_code == 422
