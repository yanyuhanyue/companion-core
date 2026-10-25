"""Real Core SDK entry in an independent process; only provider HTTP is synthetic."""

import asyncio
import json
import sys
from pathlib import Path

import httpx

from companion import owner_input
from companion.config import Settings
from companion.core import Core
from companion.model import ModelClient
from companion.retrieval import SemanticIndex
from companion.token_measurement import measure_request

settings = Settings.model_validate_json(Path(sys.argv[1]).read_text("utf-8"))
now, job_id, report_path = sys.argv[2:]
SemanticIndex.warmup = lambda self: None
payloads = []


async def provider(request):
    payloads.append(json.loads(request.content))
    return httpx.Response(200, json={"choices": [{"message": {
        "role": "assistant", "content": "synthetic continuation reply"}}],
        "usage": {"prompt_tokens": 100, "completion_tokens": 10}})


async def run():
    async with httpx.AsyncClient(transport=httpx.MockTransport(provider)) as client:
        with Core(settings, ModelClient(settings, client), lambda: now) as core:
            queued = core.store.one("SELECT j.*,e.conversation_id FROM jobs j "
                                    "JOIN events e USING(event_id) WHERE job_id=?", (job_id,))
            available = owner_input.history_sources(core.store, settings, core.clock, queued)
            assert len(available) == 8
            assert await core.run_once("hot-context-worker", job_id=job_id)
            job = core.store.one("SELECT * FROM jobs WHERE job_id=?", (job_id,))
            assert job["state"] == "SUCCEEDED", dict(job)
            assert len(payloads) == 1
            payload = payloads[0]
            sources = [row["event_id"] for row in core.store.rows(
                "SELECT event_id FROM job_sources WHERE job_id=? AND event_id<>?",
                (job_id, job["event_id"]))]
            measurement = measure_request(settings, payload["messages"],
                                          tools=payload.get("tools"))
            return {"payload": payload, "sources": sources,
                    "input_tokens": measurement.input_tokens}


Path(report_path).write_text(json.dumps(asyncio.run(run())), encoding="utf-8")
