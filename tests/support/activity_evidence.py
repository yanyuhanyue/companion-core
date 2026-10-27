"""Actual signed inputs, accepted Goal, Reading v3 receipt and SDK source proofs."""

import asyncio
import json
import sqlite3
import sys
from contextlib import asynccontextmanager
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import httpx

from companion import owner_authority
from companion.activity_evidence import SourceAdmissions, read_goal_execution
from companion.config import ObservationAdapterConfig
from companion.contracts import Candidate
from companion.paid_budget import sha
from companion.store import after, encode
from tests.support.native_consumer_entry import environment
from tests.test_next_reading import HTML, WEB

TOKEN = "synthetic-activity-evidence-reader-token-" * 3
ROOT = Path(__file__).resolve().parents[2]


def settings_json(core):
    values = core.settings.model_dump(mode="json")
    assert core.settings.model_key.get_secret_value() == "synthetic-key"
    for key in ("model_key", "bridge_token", "control_token"):
        values[key] = getattr(core.settings, key).get_secret_value()
    for key, adapter in core.settings.observation_adapters.items():
        assert adapter.token.get_secret_value() == TOKEN
        values["observation_adapters"][key]["token"] = adapter.token.get_secret_value()
    return values


async def process(tmp_path, mode, value):
    config, output = tmp_path / (mode + ".json"), tmp_path / (mode + "-result.json")
    config.write_text(encode(value), "utf-8")
    child = await asyncio.create_subprocess_exec(sys.executable, "-B", "-m",
        "tests.activity_evidence_process", mode, str(config), str(output), cwd=ROOT,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    try:
        async with asyncio.timeout(45):
            _, error = await child.communicate()
        assert child.returncode == 0, error.decode(errors="replace")
        return json.loads(output.read_text("utf-8"))
    finally:
        if child.returncode is None:
            child.kill()
            await child.wait()


def admitted(core, job):
    checkpoint = core.store.one("SELECT snapshots_json FROM agent_conversations WHERE job_id=?",
                                (job["job_id"],))
    snapshots = json.loads(checkpoint["snapshots_json"])
    origins = tuple(item for item in snapshots if item["kind"] in {"history", "memory", "reader"})
    return SourceAdmissions(job["job_id"], job["conversation_id"],
        sha(json.loads(job["authority_json"])), sha(origins), origins)


def journal_state(core, paths=None):
    if paths is None:
        authority = owner_authority.current(core.store, core.settings, core.clock)
        paths = (core.settings.erasure_ledger, core.settings.paid_model.journal_path,
                 authority.journal_path)
    return {str(path): Path(path).read_bytes() for path in paths}


def read_only(case, *, admissions=None):
    core, store = case.core, case.core.store
    before, journals = list(store.db.iterdump()), journal_state(core, case.journal_paths)
    blocked = {sqlite3.SQLITE_INSERT, sqlite3.SQLITE_UPDATE, sqlite3.SQLITE_DELETE,
               sqlite3.SQLITE_CREATE_TABLE, sqlite3.SQLITE_DROP_TABLE, sqlite3.SQLITE_TRANSACTION}
    try:
        with store.transaction():
            store.db.set_authorizer(lambda action, *args: sqlite3.SQLITE_DENY
                                    if action in blocked else sqlite3.SQLITE_OK)
            try:
                return read_goal_execution(store, core.settings, core.clock, job=case.job,
                    goal_id=case.goal_id, admissions=admissions or case.admissions)
            finally:
                store.db.set_authorizer(None)
    finally:
        assert list(store.db.iterdump()) == before
        assert journal_state(core, case.journal_paths) == journals


@asynccontextmanager
async def prepared(core, tmp_path, *, scope="current", commitment=False, lost=False):
    purposes = ("chat", "goals", "content", "exploration", "memory", *(
        ("history",) if scope != "memory" else ()))
    options = dict(goals_enabled=True, exploration_enabled=True, observation_enabled=True,
        content_enabled=True, web_reading_enabled=True, web_reading_hosts=("articles.example",),
        exploration_daily_requests=8, exploration_monthly_requests=30,
        exploration_daily_bytes=1048576,
        context_soft_tokens=12000, observation_adapters={"web": ObservationAdapterConfig(
            source="web", token=TOKEN)})
    async with environment(core, tmp_path, purposes=purposes, core_options=options,
        paid_options={"per_request_calls": 3, "window_calls": 8},
    ) as (entry, sends):
        source = await entry.admit(1, "请阅读 " + WEB
                                   + "。项目资料是恒星光谱。我接受今天完成这项阅读。")
        source_job = entry.accepted[source["event_id"]]["job_id"]
        assert (await process(tmp_path, "chat", {"settings": settings_json(core),
            "now": core.clock(), "job_id": source_job}))["calls"] == 1
        assert await entry.send(entry.client, entry.protocol) and len(sends) == 1
        async with httpx.AsyncClient(base_url=str(entry.client.base_url), trust_env=False,
            headers={"Authorization": "Bearer " + core.settings.control_token.get_secret_value()},
        ) as control:
            proposal = {"topic": "核验恒星资料", "source_event_ids": [source["event_id"]],
                        "scope": {"operation": "read_content", "url": WEB}}
            if commitment:
                response = await control.post("/internal/v2/commitments", json={
                    "proposal": proposal, "accepted_event_id": source["event_id"],
                    "deadline": after(core.clock(), 600)})
                assert response.status_code == 202, response.text
                goal_id = response.json()["goal_id"]
            else:
                response = await control.post("/internal/v2/goals", json=proposal)
                assert response.status_code == 202, response.text
                goal_id = response.json()["goal_id"]
                response = await control.post(f"/internal/v2/goals/{goal_id}/decision",
                                              json={"decision": "ACCEPT"})
                assert response.status_code == 200, response.text
            assert core.exploration.schedule_goal(), core.goals.get(goal_id)["state"]
            reader = await process(tmp_path, "reader", {"base": str(entry.client.base_url),
                "token": TOKEN, "now": core.clock(), "html": HTML.hex(), "lease_only": lost})
            assert reader == ({"worked": True, "leased_only": True} if lost else {"worked": True})
            attempt = core.store.one("SELECT * FROM reading_attempts")
            assert attempt["state"] == ("RUNNING" if lost else "DONE"), core.store.one(
                "SELECT state,last_error_code,used_requests,used_bytes FROM exploration_requests")
            core.clock.now = datetime.fromisoformat(after(attempt["lease_until"], 1))
            if lost:
                core.exploration.recover()
            step = core.store.one("SELECT * FROM goal_steps WHERE goal_id=?", (goal_id,))
            if not lost:
                assert step["state"] == "DONE" and step["lease_token"] is None
            consumer_job = source_job
            memory_id = None
            if scope != "current":
                if scope == "memory":
                    with core.store.transaction():
                        memory_id = core.memory.add(Candidate(subject="project", kind="fact",
                            fact_key="evidence-topic", content="项目资料是恒星光谱。",
                            evidence_event_id=source["event_id"],
                            evidence_quote="项目资料是恒星光谱。"),
                            core.clock(), confirmed=True)
                query = await entry.admit(2, "这项已经接受的阅读有什么原始结果？")
                consumer_job = entry.accepted[query["event_id"]]["job_id"]
                report = await process(tmp_path, "chat", {"settings": settings_json(core),
                    "now": core.clock(), "job_id": consumer_job, "memory": scope == "memory"})
                assert report["calls"] == (2 if scope == "memory" else 1)
            job = core.store.one("SELECT j.*,e.body_json,e.conversation_id,e.trace_id FROM jobs j "
                "JOIN events e USING(event_id) WHERE job_id=?", (consumer_job,))
            admissions = admitted(core, job)
            yield SimpleNamespace(core=core, entry=entry, control=control, goal_id=goal_id,
                job=job, source=source, attempt=attempt, step=step, admissions=admissions,
                memory_id=memory_id, journal_paths=tuple(journal_state(core)),
                worker_value={"settings": settings_json(core),
                    "now": core.clock(), "job_id": consumer_job, "goal_id": goal_id,
                    "admissions": asdict(admissions)})
