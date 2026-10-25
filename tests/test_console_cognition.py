"""Console session access to cognition, life and executive services reuses native records."""

import json

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from companion.api import create_app
from companion.retrieval import SemanticIndex
from companion.store import after
from tests.conftest import run
from tests.test_cognition_goal_steps import finish_record, retained_plan
from tests.test_consolidation import enable_nightly
from tests.test_diary import DAY

ORIGIN = "https://console.example.test"
PASSWORD = "owner-console-cognition-password-" * 2


@pytest.fixture
def client(core, monkeypatch):
    core.settings.console.enabled = True
    core.settings.console.public_origin = ORIGIN
    core.settings.control_token = SecretStr(PASSWORD)
    monkeypatch.setattr(SemanticIndex, "warmup", lambda self: None)
    app = create_app(core.settings)
    with TestClient(app, base_url=ORIGIN) as http:
        app.state.core.clock = core.clock
        app.state.core.goals.clock = core.clock
        yield http


def login(client):
    csrf = client.post("/v1/console/session", json={"password": PASSWORD},
                       headers={"Origin": ORIGIN}).json()["csrf_token"]
    return {"Origin": ORIGIN, "X-CSRF-Token": csrf}


@pytest.mark.parametrize("path", ["/cognition?kind=goal", "/affect", "/goals", "/reflections",
                                  "/life/topics", "/interests", "/expression-learning", "/tools",
                                  "/source-grants"])
def test_reads_require_the_owner_session(client, path):
    assert client.get("/v1/console" + path).status_code == 401
    login(client)
    response = client.get("/v1/console" + path)
    assert response.status_code == 200, response.text
    assert response.headers["cache-control"] == "no-store"


def test_cognition_goal_steps_without_scope_urls(client, core):
    goal, steps = retained_plan(core)
    finish_record(core, steps[0], 1)
    core.settings.control_token = SecretStr(PASSWORD)  # The observation helper sets its own.
    login(client)
    page = client.get("/v1/console/cognition", params={"kind": "goal"}).json()
    item = page["items"][0]
    assert item["id"] == goal and [s["state"] for s in item["steps"]][0] == "DONE"
    goals = client.get("/v1/console/goals").json()["items"]
    assert goals[0]["goal_id"] == goal and "example.org" not in json.dumps(goals)
    assert client.get("/v1/console/cognition", params={"kind": "other"}).status_code == 400


@pytest.mark.parametrize("decision", ["ACCEPT_AS_HYPOTHESIS", "REJECT", "NEED_MORE_EVIDENCE"])
def test_reflection_decision_uses_native_service_once(client, core, decision):
    enable_nightly(core)
    assert run(core.reflections.run_day(DAY))["state"] == "COMMITTED"
    headers = login(client)
    item = client.get("/v1/console/reflections").json()["items"][0]
    assert item["evidence"][0]["exact_quote"] and "evidence_json" not in item
    url = f"/v1/console/reflections/{item['suggestion_id']}/decision"
    body = {"decision": decision}
    assert client.post(url, json=body, headers=headers).status_code == 400  # Receipt key needed.
    keyed = {**headers, "Idempotency-Key": "reflection-decision-1"}
    first = client.post(url, json=body, headers=keyed)
    assert first.status_code == 200, first.text
    assert client.post(url, json=body, headers=keyed).json() == first.json()
    assert core.store.one("SELECT state FROM reflection_suggestions")["state"] == decision
    assert client.post(url, json=body, headers={"Origin": ORIGIN}).status_code == 403


def test_affect_reset_requires_confirmation(client):
    headers = {**login(client), "Idempotency-Key": "affect-reset-1"}
    assert client.post("/v1/console/affect/reset", json={}, headers=headers).status_code == 400


def test_goal_plan_console_wait_reorder_and_version_fence(client, core):
    goal, steps = retained_plan(core)
    core.settings.control_token = SecretStr(PASSWORD)
    headers = login(client)
    path = f"/v1/console/goals/{goal}/continuation"
    item = client.get("/v1/console/goals").json()["items"][0]
    assert item["allowed_actions"] == ["CONTINUE", "WAIT", "ABANDON"]
    assert item["version"] == 1 and [s["step_id"] for s in item["steps"]] == steps
    assert "example.org" not in json.dumps(item)
    wait = {"expected_version": item["version"], "action": "WAIT",
            "wait_until": after(core.clock(), 60)}
    first = client.post(path, json=wait,
        headers={**headers, "Idempotency-Key": "wait-plan"})
    assert first.status_code == 200, first.text
    assert first.json()["version"] == item["version"] + 1
    assert client.post(path, json=wait,
        headers={**headers, "Idempotency-Key": "wait-plan"}).json() == first.json()
    item = client.get("/v1/console/goals").json()["items"][0]
    assert item["allowed_actions"] == ["WAIT", "ABANDON"]
    order = list(reversed(steps))
    continuation = {"expected_version": item["version"] - 1, "action": "CONTINUE",
                    "step_order": order}
    stale = client.post(path, json=continuation,
        headers={**headers, "Idempotency-Key": "stale-plan"})
    assert stale.status_code == 409 and stale.json()["error"] == "GOAL_VERSION_CHANGED"
    assert not core.store.rows("SELECT * FROM exploration_requests")
    core.clock.advance(61)
    continuation["expected_version"] = item["version"]
    continued = client.post(path, json=continuation,
        headers={**headers, "Idempotency-Key": "continue-plan"})
    assert continued.status_code == 200, continued.text
    assert continued.json()["state"] == "QUEUED"
    actual = core.goals.plan(goal)
    assert [step["step_id"] for step in actual["steps"]] == order
    assert [step["state"] for step in actual["steps"]] == [
        "QUEUED", "WAITING_DECISION", "WAITING_DECISION"]


@pytest.mark.parametrize("blocked", ["disabled", "uncertain"])
def test_goal_console_keeps_history_without_continuation_authority(client, core, blocked):
    goal, steps = retained_plan(core)
    core.settings.control_token = SecretStr(PASSWORD)
    if blocked == "disabled":
        core.settings.goals_enabled = False
    else:
        core.store.execute("UPDATE goal_steps SET state='EXECUTION_UNCERTAIN' WHERE step_id=?",
                           (steps[0],))
    login(client)
    item = client.get("/v1/console/goals").json()["items"][0]
    assert item["goal_id"] == goal and item["steps"]
    assert item["allowed_actions"] == []
