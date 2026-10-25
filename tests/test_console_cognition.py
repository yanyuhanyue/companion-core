"""Console session access to cognition, life and executive services reuses native records."""

import json

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from companion.api import create_app
from companion.retrieval import SemanticIndex
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


def test_reflection_decision_uses_native_service_once(client, core):
    enable_nightly(core)
    assert run(core.reflections.run_day(DAY))["state"] == "COMMITTED"
    headers = login(client)
    item = client.get("/v1/console/reflections").json()["items"][0]
    assert item["evidence"][0]["exact_quote"] and "evidence_json" not in item
    url = f"/v1/console/reflections/{item['suggestion_id']}/decision"
    body = {"decision": "REJECT"}
    assert client.post(url, json=body, headers=headers).status_code == 400  # Receipt key needed.
    keyed = {**headers, "Idempotency-Key": "reject-reflection-1"}
    first = client.post(url, json=body, headers=keyed)
    assert first.status_code == 200, first.text
    assert client.post(url, json=body, headers=keyed).json() == first.json()
    assert core.store.one("SELECT state FROM reflection_suggestions")["state"] == "REJECT"
    assert client.post(url, json=body, headers={"Origin": ORIGIN}).status_code == 403


def test_affect_reset_requires_confirmation(client):
    headers = {**login(client), "Idempotency-Key": "affect-reset-1"}
    assert client.post("/v1/console/affect/reset", json={}, headers=headers).status_code == 400
