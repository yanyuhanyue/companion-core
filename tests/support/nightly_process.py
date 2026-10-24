"""Independent native Life worker; provider HTTP and pending Core delegates are test seams."""

import asyncio
import json
import os
from functools import partial
from pathlib import Path

import httpx

from companion import erasure, life_worker, memory_work
from companion.config import Settings
from companion.consolidation import NightlyConsolidator
from companion.core import Core
from companion.diary import DIARY_RULES
from companion.model import EXTRACT_RULES, REVIEW_RULES
from companion.reflection import REFLECTION_RULES
from companion.store import encode


def run_worker(settings_path, now, stop_after_extract):
    settings = Settings.model_validate_json(Path(settings_path).read_text("utf-8"))
    calls, units = [], []

    class Finished(Exception):
        pass

    def provider(request):
        assert request.url.host == "synthetic.invalid"
        messages = json.loads(request.content)["messages"]
        rules, data = messages[0]["content"], json.loads(messages[-1]["content"])
        if rules == EXTRACT_RULES:
            calls.append("memory_extract")
            value = {"memories": [
                {"subject": "project", "kind": "fact", "fact_key": "source-" + str(index),
                 "content": row["text"], "evidence_event_id": row["event_id"],
                 "evidence_quote": row["text"]} for index, row in enumerate(data[:2])
            ]}
        elif rules == REVIEW_RULES:
            calls.append("memory_review")
            value = {"relations": [{"same": None, "conflicts": []} for _ in data["candidates"]]}
        elif rules == DIARY_RULES:
            calls.append("diary")
            source = next(row for row in data["sources"] if row["kind"] == "owner_message")
            value = {"schema_version": 1, "local_date": data["local_date"],
                     "title": "真实交流记录", "worth_saving": True,
                     "sections": [{"kind": "observation", "text": source["text"],
                                   "evidence": [{"source_id": source["source_id"],
                                                 "exact_quote": source["text"]}]}]}
        else:
            assert rules == REFLECTION_RULES
            calls.append("reflection")
            value = {"suggestions": []}
        return httpx.Response(200, json={
            "choices": [{"message": {"content": encode(value)}}],
            "usage": {"prompt_tokens": 100, "completion_tokens": 30},
        })

    original_client = httpx.AsyncClient

    def offline_client(**kwargs):
        kwargs["transport"] = httpx.MockTransport(provider)
        return original_client(**kwargs)

    original_step = NightlyConsolidator.run_step

    async def measured(self, worker_id):
        before = self.store.one("SELECT COUNT(*) AS n FROM model_usage")["n"]
        result = await original_step(self, worker_id)
        admitted = self.store.one("SELECT COUNT(*) AS n FROM model_usage")["n"] - before
        assert admitted <= 1
        units.append({"progressed": result.progressed, "admissions": admitted})
        rows = self.store.rows("SELECT steps_json FROM consolidation_runs")
        if stop_after_extract and any(json.loads(row["steps_json"]).get(
            "memory_batch", {}).get("phase") == "REVIEW" for row in rows):
            raise Finished
        return result

    async def stop(*args, **kwargs):
        raise asyncio.CancelledError

    # Exactly the delegates/constructor binding and cleanup registration in the
    # separate Core-owner patches. No native persistence/accounting is mocked.
    Core.extract_once = memory_work.extract_once
    Core.extract_step = memory_work.extract_step
    Core.prepare_memories = memory_work.prepare_memories
    original_init = Core.__init__

    def initialize(self, *args, **kwargs):
        original_init(self, *args, **kwargs)
        self.consolidation.extract_step = self.extract_step

    Core.__init__ = initialize
    contracts = erasure.cleanup_contracts
    erasure.cleanup_contracts = lambda: (
        *contracts(), erasure.event_cleanup("memory-work", memory_work.erase_memory_work),
    )
    NightlyConsolidator.run_step = measured
    life_worker.Core = partial(Core, clock=lambda: now)
    life_worker.httpx.AsyncClient = offline_client
    life_worker.Wakeup.wait = stop
    try:
        asyncio.run(life_worker.life_worker(settings))
    except (Finished, asyncio.CancelledError):
        pass
    print(encode({"pid": os.getpid(), "calls": calls, "units": units}), flush=True)
