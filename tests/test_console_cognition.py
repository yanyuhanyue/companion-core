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


def test_tool_list_shows_the_exact_scope_an_approval_releases(client, core):
    from tests.test_executive import configured, proposal

    executive = configured(core, allow_changes=True)
    task = executive.submit(proposal(2, "windows.write_text", text="明天的购物清单"))
    headers = login(client)
    value = client.get("/v1/console/tools").json()
    item = next(row for row in value["items"] if row["task_id"] == task["task_id"])
    assert value["enabled"] is True and "scope_json" not in item
    scope = item["scope"]
    assert scope["state"] == "RECORDED" and scope["capability"] == "windows.write_text"
    assert scope["path"]["value"] == "result.txt"
    assert scope["text"]["value"] == "明天的购物清单"
    assert scope["sources_live"] and scope["source_count"] == 1
    assert "127.0.0.1" not in json.dumps(value) and "executor-test-token" not in json.dumps(value)
    url = f"/v1/console/tools/{task['task_id']}/decision"
    body = {"decision": "APPROVE", "scope_sha256": item["scope_sha256"]}
    response = client.post(url, json=body, headers={**headers, "Idempotency-Key": "approve-1"})
    assert response.status_code == 200, response.text
    assert core.store.one("SELECT state FROM tool_tasks WHERE task_id=?",
                          (task["task_id"],))["state"] == "QUEUED"


def test_tool_scope_that_no_longer_matches_its_digest_is_not_shown(client, core):
    from tests.test_executive import configured, proposal

    executive = configured(core, allow_changes=True)
    task = executive.submit(proposal(3, "windows.write_text", text="原始内容"))
    scope = json.loads(task["scope_json"])
    scope["arguments"]["text"] = "被改过的内容"
    core.store.execute("UPDATE tool_tasks SET scope_json=? WHERE task_id=?",
                       (json.dumps(scope, ensure_ascii=False), task["task_id"]))
    login(client)
    item = next(row for row in client.get("/v1/console/tools").json()["items"]
                if row["task_id"] == task["task_id"])
    assert item["scope"] == {"state": "MISMATCH"}


def test_affect_view_distinguishes_disabled_from_neutral(client, core):
    login(client)
    core.settings.affect_enabled = False
    value = client.get("/v1/console/affect").json()
    assert value["enabled"] is False and value["reset_at"] is None
    assert value["state"]["engagement"] == "neutral"
