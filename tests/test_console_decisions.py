"""The decision observatory reads native records only and never invents reasoning."""

from fastapi.testclient import TestClient
from pydantic import SecretStr

from companion.api import create_app
from companion.retrieval import SemanticIndex
from tests.conftest import run
from tests.test_consolidation import enable_nightly
from tests.test_diary import DAY

ORIGIN = "https://console.example.test"
PASSWORD = "owner-decisions-test-password-" * 2


def test_decisions_list_wake_and_reflection_records_without_reasoning(core, monkeypatch):
    enable_nightly(core)
    assert run(core.reflections.run_day(DAY))["state"] == "COMMITTED"
    core.settings.wake_enabled = core.settings.goals_enabled = True
    core.wake.evaluate()
    core.settings.console.enabled = True
    core.settings.console.public_origin = ORIGIN
    core.settings.control_token = SecretStr(PASSWORD)
    monkeypatch.setattr(SemanticIndex, "warmup", lambda self: None)
    app = create_app(core.settings)
    with TestClient(app, base_url=ORIGIN) as client:
        app.state.core.clock = core.clock
        assert client.get("/v1/console/decisions").status_code == 401
        client.post("/v1/console/session", json={"password": PASSWORD}, headers={"Origin": ORIGIN})
        value = client.get("/v1/console/decisions").json()
    kinds = {item["kind"]: item for item in value["items"]}
    assert kinds["wake"]["status"] == "SKIP" and kinds["wake"]["detail"]["trigger"] == "schedule"
    assert kinds["reflection"]["status"] == "COMMITTED"
    assert kinds["reflection"]["detail"]["suggestions"] == 1
    assert value["reasoning"]["state"] == "NOT_REQUESTED"
    assert "下次可以再了解" not in str(value)  # Suggestion text stays on the life page.
