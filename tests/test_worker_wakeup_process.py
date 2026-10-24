"""Real CLI processes share one SQLite authority; only external model transport is mocked."""

import asyncio
import hashlib
import json
import os
import sys
from datetime import datetime
from pathlib import Path
from time import monotonic
from zoneinfo import ZoneInfo

import httpx
from pydantic import SecretStr

from companion import owner_authority, owner_input
from companion.contracts import Candidate
from companion.owner_authority_protocol import OwnerCapture, OwnerGrantPolicy
from companion.store import after, utcnow
from companion_delivery_protocol.authority import capture_signing_payload
from tests.conftest import ingest, run
from tests.test_onebot_capture import pinned_plan, real_core_http
from tests.test_owner_authority import activate
from tests.test_owner_input import captured, inbound
from tests.test_private_companion import companion_options


async def start(
    core, directory, role="worker", *, duration=5, settle=0.4, controlled=False,
):
    directory.mkdir(parents=True)
    config, ready, report = (directory / name for name in ("config.json", "ready", "result.json"))
    config.write_text(
        json.dumps(
            {
                "settings": core.settings.model_dump(mode="python"),
                "role": role,
                "duration": duration,
                "settle": settle,
                "ready": str(ready),
                "report": str(report),
                "release": str(directory / "release") if controlled else None,
                "scan_progress": str(directory / "scans") if controlled else None,
            },
            default=lambda v: v.get_secret_value() if isinstance(v, SecretStr) else str(v),
        ),
        encoding="utf-8",
    )
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-B",
        "-m",
        "tests.worker_wakeup_process",
        str(config),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    return process, ready, report


async def ready(child):
    process, path, _ = child
    async with asyncio.timeout(15):
        while not path.exists():
            if process.returncode is not None:
                _, error = await process.communicate()
                raise AssertionError(error.decode(errors="replace"))
            await asyncio.sleep(0.02)


async def finish(child, *, code=0):
    process, _, report = child
    output, error = await asyncio.wait_for(process.communicate(), 15)
    assert process.returncode == code, (
        output.decode(errors="replace"),
        error.decode(errors="replace"),
    )
    result = json.loads(report.read_text(encoding="utf-8"))
    assert result["pid"] != os.getpid()
    assert result["watchers"] == result["closed_watchers"]
    return result


async def close_child(child):
    process = child[0]
    if process.returncode is None:
        process.kill()
    await process.communicate()


def signed_input(core, template, key, number):
    value = template.model_dump(mode="json", exclude={"signature"})
    source = template.source_event_id.rpartition(":")[0] + ":" + str(number)
    stamp = core.clock()
    value.update(source_event_id=source,
                 event_id="evt_" + hashlib.sha256(source.encode()).hexdigest(),
                 occurred_at=stamp, received_at=stamp)
    value["signature"] = key.sign(capture_signing_payload(value)).hex()
    capture = OwnerCapture.model_validate(value)
    message = inbound(core, capture).model_copy(update={"occurred_at": capture.occurred_at})
    return capture, message


async def state(core, job_id, wanted="SUCCEEDED", seconds=3):
    started = monotonic()
    async with asyncio.timeout(seconds):
        while core.store.one("SELECT state FROM jobs WHERE job_id=?", (job_id,))["state"] != wanted:  # noqa: ASYNC110 - observes an independent process
            await asyncio.sleep(0.01)
    return monotonic() - started


def receipt(name, value):
    directory = os.getenv("WORKER_WAKEUP_RECEIPTS")
    if directory:
        path = Path(directory)
        path.mkdir(parents=True, exist_ok=True)
        (path / (name + ".json")).write_text(json.dumps(value, indent=2), encoding="utf-8")


def test_four_native_entrypoints_use_shared_scan_and_unit_boundary(core, tmp_path):
    core.settings.content_enabled = True
    core.settings.executive = core.settings.executive.model_copy(update={"enabled": True})
    roles = ("worker", "content-worker", "executive-worker", "life-worker")

    async def scenario():
        children = await asyncio.gather(*[
            start(core, tmp_path / role, role, duration=3, settle=0.4) for role in roles
        ])
        rows = await asyncio.gather(*[finish(child) for child in children])
        for row in rows:
            assert row["total"]["scan_contexts"] > 0
            assert row["total"]["native_units"] > 0
            assert row["total"]["provider_calls"] == 0
            assert row["watchers"] == row["closed_watchers"] == 1
        receipt("shared-boundary-four-workers", rows)

    run(scenario())


def test_real_http_enqueue_new_owner_and_independent_worker_latency(core, tmp_path):
    core.clock = utcnow
    template, key = captured(core, tmp_path)

    async def scenario():
        async with real_core_http(core) as port:
            child = await start(core, tmp_path / "chat", duration=4)
            try:
                await ready(child)
                latencies = []
                async with httpx.AsyncClient(
                    base_url=f"http://127.0.0.1:{port}",
                    trust_env=False,
                    headers={
                        "Authorization": "Bearer " + core.settings.bridge_token.get_secret_value()
                    },
                ) as api:
                    for number in (1, 2):
                        capture, message = signed_input(core, template, key, number)
                        approved = await api.post("/internal/v1/owner-authority/inputs/approve",
                                                  json=capture.model_dump(mode="json"))
                        assert approved.status_code == 200, approved.text
                        response = await api.post(
                            "/internal/v1/inbox/events",
                            json=message.model_dump(mode="json"),
                            headers={"X-Idempotency-Key": f"wake-{number}"},
                        )
                        assert response.status_code == 202, response.text
                        latency = await state(core, response.json()["job_id"])
                        assert latency < 1.5
                        latencies.append(latency)
                result = await finish(child)
                assert result["total"]["provider_calls"] == 2
                receipt("enqueue-latency", {"latencies_seconds": latencies, "process": result})
            finally:
                await close_child(child)

    run(scenario())


def test_independent_retry_due_without_new_write(core, tmp_path):
    core.clock = utcnow
    template, key = captured(core, tmp_path)
    capture, message = signed_input(core, template, key, 1)
    owner_input.approve(core.store, core.settings, core.clock, capture)
    item = core.ingest(message, "wake-retry")
    # Keep the job pending while the independent interpreter imports/starts.
    due = after(utcnow(), 3600)
    core.store.execute(
        "UPDATE jobs SET state='RETRY_WAIT',next_attempt_at=? WHERE job_id=?", (due, item["job_id"])
    )

    async def scenario():
        child = await start(core, tmp_path / "retry", duration=5)
        try:
            await ready(child)
            assert core.store.one("SELECT state FROM jobs WHERE job_id=?", (item["job_id"],))[
                "state"
            ] == ("RETRY_WAIT")
            core.store.execute("UPDATE jobs SET next_attempt_at=? WHERE job_id=?",
                               (after(utcnow(), 1), item["job_id"]))
            # No writes after scheduling: only the native deadline can make it due.
            await state(core, item["job_id"], seconds=5)
            result = await finish(child)
            assert result["total"]["provider_calls"] == 1
            receipt("retry-without-writer", result)
        finally:
            await close_child(child)

    run(scenario())


def test_real_http_forget_wakes_native_control_without_provider(core, tmp_path):
    core.clock = utcnow
    core.settings.memory_enabled = True
    source = ingest(core, 1, "I prefer green tea.")
    with core.store.transaction():
        memory_id = core.memory.add(
            Candidate(
                subject="user",
                kind="preference",
                fact_key="tea",
                content="I prefer green tea.",
                evidence_event_id=source["event_id"],
                evidence_quote="I prefer green tea.",
            ),
            core.clock(),
            confirmed=True,
        )
        core.store.execute("UPDATE jobs SET state='SUCCEEDED'")
    captured(core, tmp_path)
    view = owner_authority.current(core.store, core.settings, core.clock)
    policy = OwnerGrantPolicy.model_validate({**view.policy.model_dump(),
        "purposes": ["chat", "memory"], "memory_scope": "owner_private"})
    activate(core, policy, revision=view.authority.revision, operation="wakeup-memory-policy")

    async def scenario():
        async with real_core_http(core) as port:
            child = await start(core, tmp_path / "forget", duration=3)
            try:
                await ready(child)
                async with httpx.AsyncClient(
                    base_url=f"http://127.0.0.1:{port}", trust_env=False
                ) as api:
                    response = await api.post(
                        "/internal/v1/memories/" + memory_id + "/decisions",
                        json={"actor_id": "owner", "decision": "forget"},
                        headers={
                            "Authorization": "Bearer "
                            + core.settings.bridge_token.get_secret_value()
                        },
                    )
                    assert response.status_code == 200, response.text
                result = await finish(child)
                assert result["total"]["provider_calls"] == 0
                assert result["steady"]["scans"] >= 1
                assert core.store.one(
                    "SELECT status FROM memories WHERE memory_id=?", (memory_id,)
                ) == {"status": "FORGOTTEN"}
            finally:
                await close_child(child)

    run(scenario())


def test_stop_api_wakes_independent_worker_and_lock_remains_exclusive(core, tmp_path):
    core.clock.now = datetime.fromisoformat(utcnow())

    async def scenario():
        options = companion_options(core)
        options["window_options"].update(
            starts_at=datetime.fromisoformat(after(utcnow(), -60))
            .astimezone(ZoneInfo("Asia/Shanghai"))
            .isoformat(),
            ends_at=datetime.fromisoformat(after(utcnow(), 3600))
            .astimezone(ZoneInfo("Asia/Shanghai"))
            .isoformat(),
        )
        async with real_core_http(core) as port:
            cfg, plan = pinned_plan(
                core,
                tmp_path,
                3001,
                core_port=port,
                live=True,
                approved=True,
                scope_options=options,
            )
            core.clock.now = datetime.fromisoformat(utcnow())
            async with httpx.AsyncClient(
                base_url=cfg.core_url,
                trust_env=False,
                headers={"Authorization": "Bearer " + cfg.bridge_token},
            ) as api:
                opened = await api.post(
                    "/internal/v1/live-text/open", json={"window_sha256": plan.sha256}
                )
                assert opened.status_code == 200, opened.text
                directory = tmp_path / "stop"
                child = await start(core, directory, duration=30, controlled=True)
                try:
                    await ready(child)
                    duplicate = await start(core, tmp_path / "duplicate", duration=1)
                    denied = await finish(duplicate, code=2)
                    assert denied["watchers"] == 0
                    scans = (directory / "scans").read_text(encoding="utf-8")
                    stopped = await api.post(
                        "/internal/v1/live-text/stop", json={"window_sha256": plan.sha256}
                    )
                    assert stopped.status_code == 200, stopped.text
                    async with asyncio.timeout(5):
                        while (directory / "scans").read_text(encoding="utf-8") == scans:  # noqa: ASYNC110 - independent worker progress
                            await asyncio.sleep(0.02)
                finally:
                    (directory / "release").touch()
                    await child[0].communicate()
                result = await finish(child)
                assert result["steady"]["scans"] >= 1
                assert result["total"]["provider_calls"] == 0
                restarted = await start(core, tmp_path / "restart", duration=1, settle=0.1)
                assert (await finish(restarted))["watchers"] == 1

    run(scenario())
