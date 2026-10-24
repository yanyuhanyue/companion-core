"""Endpoint editing must not silently move an existing write-only credential."""

import pytest
from pydantic import SecretStr

from companion import operator_config
from tests import test_owner_console


@pytest.fixture
def client(core, monkeypatch):
    yield from test_owner_console.client.__wrapped__(core, monkeypatch)


@pytest.mark.parametrize("target", ["chat", "vision"])
@pytest.mark.parametrize("key_choice", ["omitted", "null", "same", "fresh", "clear"])
def test_changed_endpoint_requires_explicit_new_credential_scope(client, core, target, key_choice):
    original = "synthetic-old-provider-key"
    core.settings.model_key = SecretStr(original)
    core.settings.content.vision.key = SecretStr(original)
    core.settings.content.vision.url = "https://old.example.test/v1"
    changes = {"model_url": "https://new.example.test/v1"} if target == "chat" else {
        "content": {"vision": {"url": "https://new.example.test/v1"}}}
    fields = changes if target == "chat" else changes["content"]["vision"]
    if key_choice != "omitted":
        fields["model_key" if target == "chat" else "key"] = {
            "null": None, "same": original, "fresh": "synthetic-new-provider-key", "clear": "",
        }[key_choice]
    response = client.put("/v1/console/configuration", json={
        "expected_version": 0, "changes": changes,
    }, headers=test_owner_console.login(client))
    assert response.status_code == (200 if key_choice in {"fresh", "clear"} else 400)
    assert original not in response.text and "synthetic-new-provider-key" not in response.text
    if response.status_code == 400:
        assert not core.store.rows("SELECT * FROM operator_configuration")
    else:
        desired = operator_config.load(core.settings)
        key = desired.model_key if target == "chat" else desired.content.vision.key
        expected = "" if key_choice == "clear" else "synthetic-new-provider-key"
        assert key.get_secret_value() == expected
    assert not core.store.rows("SELECT * FROM model_usage")


def test_console_rejects_plain_http_model_endpoint_before_saving(client, core):
    response = client.put("/v1/console/configuration", json={
        "expected_version": 0, "changes": {"model_url": "http://new.example.test/v1"},
    }, headers=test_owner_console.login(client))
    assert response.status_code == 400
    assert response.json()["error"] == "CONFIGURATION_ENDPOINT_HTTPS_REQUIRED"
    assert not core.store.rows("SELECT * FROM operator_configuration")


def test_endpoint_change_stays_fenced_in_real_api_and_independent_worker(core, tmp_path):
    import asyncio
    import json
    import subprocess
    import sys

    import httpx

    from companion import console_api
    from tests.conftest import ingest
    from tests.test_onebot_capture import real_core_http
    from tests.test_paid_model_gate import approved_live

    job = ingest(core, text="SYNTHETIC pending owner chat")
    approved_live(core, tmp_path)
    core.settings.console = operator_config.ConsoleConfig(
        enabled=True, public_origin=test_owner_console.ORIGIN,
    )
    core.settings.control_token = SecretStr(test_owner_console.PASSWORD)
    base = core.settings.model_dump(mode="json")
    for name in ("model_key", "bridge_token", "control_token"):
        base[name] = getattr(core.settings, name).get_secret_value()
    assert base["model_key"] == "synthetic-key"
    config = tmp_path / "synthetic-worker.json"
    config.write_text(json.dumps(base), encoding="utf-8")

    async def save():
        async with real_core_http(core) as port, httpx.AsyncClient(
            base_url=f"http://127.0.0.1:{port}", trust_env=False,
        ) as http:
            session = await http.post("/v1/console/session", json={
                "password": test_owner_console.PASSWORD,
            }, headers={"Origin": test_owner_console.ORIGIN})
            assert session.status_code == 200
            response = await http.put("/v1/console/configuration", json={
                "expected_version": 0, "changes": {"model_url": "https://new.example.test/v1",
                                                    "model_key": "synthetic-new-provider-key"},
            }, headers={
                "Origin": test_owner_console.ORIGIN,
                "X-CSRF-Token": session.json()["csrf_token"],
                "Cookie": console_api.COOKIE + "=" + session.cookies[console_api.COOKIE],
                "Idempotency-Key": "synthetic-endpoint-edit",
            })
            assert response.status_code == 200, response.text
    asyncio.run(save())
    script = '''
import asyncio,json,sys
from pathlib import Path
import httpx
from companion.config import Settings
from companion.core import Core
from companion.model import ModelClient
from companion.operator_config import load
settings = load(Settings.model_validate(json.loads(Path(sys.argv[1]).read_text("utf-8"))))
calls = []
def provider(request):
    calls.append(1)
    raise AssertionError("Old paid contract must fence the changed endpoint")
async def run():
    async with httpx.AsyncClient(transport=httpx.MockTransport(provider)) as http:
        with Core(settings, ModelClient(settings,http), clock=lambda:sys.argv[2]) as core:
            await core.run_once("independent-console-check")
            row=core.store.one("SELECT state,last_error_code FROM jobs WHERE job_id=?",
                               (sys.argv[3],))
            usage=len(core.store.rows("SELECT 1 FROM model_usage"))
            print(json.dumps({"job":row,"calls":len(calls),"usage":usage}))
asyncio.run(run())
'''
    process = subprocess.run([sys.executable, "-B", "-c", script, str(config), core.clock(),
                              job["job_id"]], capture_output=True, text=True,
                             encoding="utf-8", timeout=20, check=True)
    observed = json.loads(process.stdout.strip().splitlines()[-1])
    assert observed == {"job": {"state": "FAILED",
                                "last_error_code": "PAID_PROVIDER_CONTRACT_MISMATCH"},
                        "calls": 0, "usage": 0}
