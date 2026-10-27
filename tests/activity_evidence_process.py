"""Independent native producer or read-only evidence consumer; external I/O is mocked."""

import asyncio
import json
import sqlite3
import sys
from contextlib import closing
from pathlib import Path


async def reader(value):
    from datetime import datetime

    import httpcore
    import httpx

    from source_reader import worker_v1
    from source_reader.web import WebHTTP, WebReader

    raw = bytes.fromhex(value["html"])
    wire = (b"HTTP/1.1 200 OK\r\nContent-Type: text/html; charset=utf-8\r\nContent-Length: "
            + str(len(raw)).encode() + b"\r\nConnection: close\r\n\r\n" + raw)
    pool = httpcore.AsyncConnectionPool(network_backend=httpcore.AsyncMockBackend([wire]))
    # The original web tick constructs its own WebHTTP. Inject at that transport
    # factory so the real lease/validation/read/commit path stays intact.
    worker_v1.WebHTTP = lambda hosts: WebHTTP(hosts, pool)
    async with httpx.AsyncClient(
        base_url=value["base"], trust_env=False,
        headers={"Authorization": "Bearer " + value["token"]},
    ) as client:
        if value.get("lease_only"):
            response = await client.post("/internal/v3/readers/web/lease", json={})
            response.raise_for_status()
            assert response.json()["item"]
            return {"worked": True, "leased_only": True}
        source = WebReader(None, clock=lambda: value["now"])
        return {"worked": await worker_v1.tick(client, source, "web", source="web",
            local_hosts=["articles.example"], clock=lambda: datetime.fromisoformat(value["now"]))}


async def chat(value):
    import httpx

    from companion.config import Settings
    from companion.core import Core
    from companion.model import ModelClient
    from companion.retrieval import SemanticIndex
    from tests.sdk_reader_fixture import answer, call

    settings = Settings.model_validate(value["settings"])
    SemanticIndex.warmup = lambda self: None
    calls = []

    def provider(request):
        payload = json.loads(request.content)
        calls.append(payload)
        selected = value.get("memory") and not any(
            item["role"] == "tool" for item in payload["messages"])
        return httpx.Response(200, json=answer(calls=[call("remember-root", "memory_recall",
            {"query": "*"})]) if selected else answer("收到这项阅读的原始输入。"))

    async with httpx.AsyncClient(transport=httpx.MockTransport(provider)) as client:
        with Core(settings, ModelClient(settings, client), lambda: value["now"]) as core:
            worked = await core.run_once("activity-evidence-producer", job_id=value["job_id"])
            job = core.store.one("SELECT state,last_error_code FROM jobs WHERE job_id=?",
                                 (value["job_id"],))
            assert job["state"] == "SUCCEEDED", job
            return {"worked": worked, "calls": len(calls)}


def evidence(value):
    from companion.activity_evidence import SourceAdmissions, read_goal_execution
    from companion.config import Settings
    from companion.store import Store

    settings = Settings.model_validate(value["settings"])
    with closing(Store(settings.database)) as store:
        job = store.one("SELECT j.*,e.body_json,e.conversation_id,e.trace_id FROM jobs j "
                        "JOIN events e USING(event_id) WHERE job_id=?", (value["job_id"],))
        admissions = SourceAdmissions(**{**value["admissions"],
                                        "origins": tuple(value["admissions"]["origins"])})
        before = list(store.db.iterdump())
        blocked = {sqlite3.SQLITE_INSERT, sqlite3.SQLITE_UPDATE, sqlite3.SQLITE_DELETE,
                   sqlite3.SQLITE_CREATE_TABLE, sqlite3.SQLITE_DROP_TABLE}
        store.db.set_authorizer(lambda action, *args: sqlite3.SQLITE_DENY
                                if action in blocked else sqlite3.SQLITE_OK)
        try:
            with store.transaction():
                result = read_goal_execution(store, settings, lambda: value["now"], job=job,
                    goal_id=value["goal_id"], admissions=admissions)
        finally:
            store.db.set_authorizer(None)
        assert before == list(store.db.iterdump())
        return {"result": result, "read_only": True}


if __name__ == "__main__":
    mode, config, output = sys.argv[1:]
    data = json.loads(Path(config).read_text("utf-8"))
    result = evidence(data) if mode == "evidence" else asyncio.run(
        reader(data) if mode == "reader" else chat(data))
    Path(output).write_text(json.dumps(result, ensure_ascii=False), "utf-8")
