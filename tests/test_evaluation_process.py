"""Actual TCP API and independent processes; provider transport remains mocked."""

import asyncio
import json
import sys
from datetime import datetime

import httpx
import pytest
from pydantic import SecretStr

from companion import evaluation, live_text, paid_budget
from companion.model import model_payload
from companion.store import after
from companion_onebot_bridge.spool import Spool
from companion_onebot_bridge.window import TestWindow
from tests.conftest import ingest
from tests.test_evaluation import authorize, prepare, result, work
from tests.test_live_text_window import live_fixture
from tests.test_onebot_capture import real_core_http
from tests.test_onebot_delivery import core_client


def process_config(core, path):
    values = core.settings.model_dump(mode="json")
    assert core.settings.model_key.get_secret_value() == "synthetic-key"
    for name in ("model_key", "bridge_token", "control_token"):
        values[name] = getattr(core.settings, name).get_secret_value()
    path.write_text(json.dumps(values), encoding="utf-8")


async def processes(core, directory, *, count=2, mode="normal"):
    directory.mkdir()
    config = directory / "synthetic-config.json"
    process_config(core, config)
    gate = directory / "go"
    children, logs = [], []
    try:
        for index in range(count):
            ready, log = directory / f"ready-{index}", directory / f"calls-{index}.jsonl"
            child = await asyncio.create_subprocess_exec(
                sys.executable, "-B", "-m", "tests.evaluation_process", "--config", str(config),
                "--now", core.clock(), "--ready", str(ready), "--gate", str(gate),
                "--log", str(log), "--mode", mode,
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
            children.append(child)
            logs.append(log)
        async with asyncio.timeout(30):
            while not all((directory / f"ready-{i}").exists() for i in range(count)):
                assert all(child.returncode is None for child in children), "worker setup failed"
                await asyncio.sleep(0.02)
            gate.write_text("go", encoding="utf-8")
            outputs = await asyncio.gather(*(child.communicate() for child in children))
        assert all(child.returncode == (23 if mode == "crash" else 0) for child in children), [
            (child.returncode, err.decode())
            for child, (_, err) in zip(children, outputs, strict=True)]
        return [json.loads(line)["payload_sha256"] for log in logs if log.exists()
                for line in log.read_text("utf-8").splitlines()]
    finally:
        for child in children:
            if child.returncode is None:
                child.kill()
                await child.wait()


def test_real_tcp_control_entry_and_two_independent_workers_share_one_job(core, tmp_path):
    manifest = prepare(core, tmp_path)
    core.settings.control_token = SecretStr("synthetic-control-" * 3)
    async def scenario():
        async with real_core_http(core) as port, httpx.AsyncClient(
            base_url=f"http://127.0.0.1:{port}", trust_env=False,
            headers={"Authorization": "Bearer " + core.settings.control_token.get_secret_value()},
        ) as client:
            preview = await client.post("/internal/v2/evaluations/preview",
                                        json=manifest.model_dump())
            assert preview.status_code == 200, preview.text
            granted = await client.post("/internal/v2/consents", json={
                **preview.json()["consent_scope"], "duration_seconds": 600})
            assert granted.status_code == 200
            submitted = await client.post("/internal/v2/evaluations", json={
                "manifest": manifest.model_dump(), "consent_id": granted.json()["consent_id"]})
            assert submitted.status_code == 202, submitted.text
            value = submitted.json()
            hashes = await processes(core, tmp_path / "parallel")
            assert hashes == [row["payload_sha256"] for row in manifest.plan["requests"]]
            observed = await client.get("/internal/v2/evaluations/" + value["evaluation_id"])
            assert observed.status_code == 200 and observed.headers["Cache-Control"] == "no-store"
            assert observed.json()["state"] == "SUCCEEDED"
            assert all(row["complete_response"] for row in observed.json()["requests"])
    asyncio.run(scenario())
    assert core.store.one("SELECT COUNT(*) n FROM model_usage")["n"] == 6
    assert core.store.rows("SELECT * FROM outbox") == []
    assert core.store.rows("SELECT * FROM memories") == []


def test_process_death_after_dispatch_preserves_unknown_and_no_repayment(core, tmp_path):
    manifest = prepare(core, tmp_path)
    submitted = evaluation.submit(core, authorize(core, manifest))
    initial = asyncio.run(processes(core, tmp_path / "crashed", count=1, mode="crash"))
    assert len(initial) == 1
    assert core.store.one("SELECT state FROM model_usage")["state"] == "RESERVED"
    core.clock.advance(core.settings.lease_seconds + 1)
    restarted = asyncio.run(processes(core, tmp_path / "restarted", count=2))
    assert restarted == []
    assert core.store.one("SELECT state FROM model_usage")["state"] == "UNKNOWN"
    assert result(core, submitted)["state"] == "CANCELLED"
    assert core.store.rows("SELECT * FROM outbox") == []


def test_two_workers_normal_chat_and_experiment_compete_for_original_cloud_budget(core, tmp_path):
    manifest = prepare(core, tmp_path)
    core.settings.cloud_budget = core.settings.cloud_budget.model_copy(
        update={"daily_micro_cny": 30000})
    normal = ingest(core, 2, text="Other SYNTHETIC chat", origin="other:FriendMessage:10001")
    submitted = evaluation.submit(core, authorize(core, manifest))
    hashes = asyncio.run(processes(core, tmp_path / "competing"))
    assert len(hashes) == 1
    usage = core.store.rows("SELECT job_id,state,billed_micro_usd FROM model_usage")
    assert len(usage) == 1 and usage[0]["state"] == "SETTLED"
    eval_event = core.store.one("SELECT event_id FROM jobs WHERE job_id=?", (submitted["job_id"],))
    assert not core.store.one("SELECT 1 FROM outbox WHERE origin_event_id=?",
                              (eval_event["event_id"],))
    assert result(core, submitted)["state"] == "FAILED"
    normal_state = core.store.one("SELECT state FROM jobs WHERE job_id=?", (normal["job_id"],))
    assert normal_state["state"] in {"SUCCEEDED", "FAILED"}


def bind_live_window(core, tmp_path, *, companion=False):
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    from tests.test_private_companion import companion_options

    manifest = prepare(core, tmp_path)
    core.settings.control_token = SecretStr("synthetic-control-" * 3)
    options = companion_options(core) if companion else {}
    if companion:
        options["bridge_options"] = {"capture_signing_key": (
            Ed25519PrivateKey.generate().private_bytes_raw().hex())}
    options["max_model_attempts"] = 20
    options["core_options"] = {**options.get("core_options", {}),
                               "evaluation_enabled": True, "max_output_tokens": 2048}
    options["paid_options"] = {**options.get("paid_options", {}),
        "provider": "deepseek", "model": "deepseek-flash", "window_calls": 20,
        "max_output_tokens": 2048, "input_rate": 300000, "cached_input_rate": 6000,
        "output_rate": 1200000}
    cfg, plan = live_fixture(core, tmp_path, **options)
    async def opening():
        async with core_client(core) as client:
            client.base_url = cfg.core_url
            with Spool(cfg) as spool:
                await TestWindow(spool, plan, clock=lambda: core.clock.now).open(client)
    asyncio.run(opening())
    return manifest


def test_private_companion_scope_uses_same_native_accounting_without_delivery(core, tmp_path):
    manifest = bind_live_window(core, tmp_path, companion=True)
    async def scenario():
        async with real_core_http(core) as port, httpx.AsyncClient(
            base_url=f"http://127.0.0.1:{port}", trust_env=False,
            headers={"Authorization": "Bearer " + core.settings.control_token.get_secret_value()},
        ) as client:
            proposed = await client.post("/internal/v2/evaluations/preview",
                                         json=manifest.model_dump())
            assert proposed.status_code == 200, proposed.text
            granted = await client.post("/internal/v2/consents", json={
                **proposed.json()["consent_scope"], "duration_seconds": 600})
            submitted = await client.post("/internal/v2/evaluations", json={
                "manifest": manifest.model_dump(), "consent_id": granted.json()["consent_id"]})
            assert submitted.status_code == 202, submitted.text
            hashes = await processes(core, tmp_path / "companion-bound")
            assert hashes == [row["payload_sha256"] for row in manifest.plan["requests"]]
            observed = await client.get(
                "/internal/v2/evaluations/" + submitted.json()["evaluation_id"])
            assert observed.status_code == 200 and observed.json()["state"] == "SUCCEEDED"
            assert all(row["evidence_type"] == "SYNTHETIC_MODEL_RESPONSE"
                       for row in observed.json()["requests"])
    asyncio.run(scenario())
    assert core.store.rows("SELECT * FROM outbox") == []
    assert core.store.rows("SELECT * FROM memories") == []


@pytest.mark.parametrize("boundary", ["unchanged", "stop", "window_change", "expiry"])
def test_active_live_window_keeps_original_guard_and_stops_old_experiment(core, tmp_path, boundary):
    manifest = bind_live_window(core, tmp_path)
    submitted = evaluation.submit(core, authorize(core, manifest))
    if boundary == "stop":
        live_text.stop_window(core.store, live_text.WindowReference(
            window_sha256=core.settings.live_text_window.plan_sha256), core.clock)
    elif boundary == "window_change":
        core.settings.live_text_window = core.settings.live_text_window.model_copy(
            update={"plan_sha256": "f" * 64})
    elif boundary == "expiry":
        core.clock.now = core.settings.live_text_window.ends_at
    hashes = []
    if boundary == "unchanged":
        hashes = asyncio.run(processes(core, tmp_path / "live-bound", count=2))
        observed = result(core, submitted)
        assert observed["state"] == "SUCCEEDED", observed["error_code"]
    else:
        work(core, lambda req: hashes.append(req))
    assert len(hashes) == (6 if boundary == "unchanged" else 0)
    if boundary == "unchanged":
        assert result(core, submitted)["state"] == "SUCCEEDED"
        assert core.store.one("SELECT COUNT(*) n FROM model_admissions")["n"] == 6
    assert core.store.rows("SELECT * FROM outbox") == []


def test_consent_expiry_during_final_budget_sync_never_dispatches(core, tmp_path, monkeypatch):
    from companion import cloud_budget

    manifest = prepare(core, tmp_path)
    request = authorize(core, manifest)
    evaluation.submit(core, request)
    original = cloud_budget.mark_dispatched
    def advance(store, kind, rid, now):
        original(store, kind, rid, now)
        core.clock.now = datetime.fromisoformat(after(now, 601))
    monkeypatch.setattr(cloud_budget, "mark_dispatched", advance)
    calls = []
    work(core, lambda req: calls.append(req))
    assert calls == []


def test_signed_successor_handoff_keeps_old_evaluation_cancelled_and_same_journal(core, tmp_path):
    from tests.test_live_text_handoff import (
        bridge_receipt,
        configure,
        initial,
        reference,
    )

    manifest = prepare(core, tmp_path)
    core.settings.control_token = SecretStr("synthetic-control-" * 3)
    core.settings.max_output_tokens = 768
    old = initial(core, tmp_path)
    plan = manifest.plan
    plan.update(provider=core.settings.provider, model=core.settings.model_name,
                rates_micro_usd_per_million={"input": core.settings.input_rate,
                    "cached_input": core.settings.cached_input_rate,
                    "output": core.settings.output_rate})
    for row in plan["requests"]:
        row["payload"] = model_payload(core.settings, row["payload"]["messages"], "evaluation")
        row["payload_sha256"] = paid_budget.sha(row["payload"])
        row["planned_max_output_tokens"] = 768
    plan["plan_sha256"] = paid_budget.sha({k: v for k, v in plan.items() if k != "plan_sha256"})
    submitted = evaluation.submit(core, authorize(core, manifest))
    journal_id = core.store.one("SELECT value FROM runtime_meta "
                                "WHERE key='model_budget_journal_id'")["value"]
    async def scenario():
        async with real_core_http(core) as port, httpx.AsyncClient(
            base_url=f"http://127.0.0.1:{port}", trust_env=False,
            headers={"Authorization": "Bearer " + core.settings.bridge_token.get_secret_value()},
        ) as client:
            stopped = await client.post("/internal/v1/live-text/stop",
                                         json=reference(old).model_dump())
            assert stopped.status_code == 200, stopped.text
            new = configure(core, tmp_path, successor=True)
            prepared = await client.post("/internal/v1/live-text/handoff",
                                          json=reference(new).model_dump())
            assert prepared.status_code == 200, prepared.text
            opened = await client.post("/internal/v1/live-text/open", json={
                "window_sha256": new.plan_sha256,
                "handoff_receipt": bridge_receipt(new, prepared.json())})
            assert opened.status_code == 200 and opened.json()["approved"], opened.text
            assert await processes(core, tmp_path / "after-handoff") == []
    asyncio.run(scenario())
    assert core.store.one("SELECT state FROM jobs WHERE job_id=?", (submitted["job_id"],))[
        "state"] == "CANCELLED"
    assert core.store.one("SELECT value FROM runtime_meta "
                          "WHERE key='model_budget_journal_id'")["value"] == journal_id
    assert core.store.rows("SELECT * FROM model_usage") == []
    assert core.store.rows("SELECT * FROM outbox") == []
