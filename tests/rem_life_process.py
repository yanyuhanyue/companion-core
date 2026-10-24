"""Observe original Life entry point and REM selector in an independent process."""

import argparse
import asyncio
import json
from functools import partial
from pathlib import Path

import httpx

from companion import life_worker, rem
from companion.config import Settings
from companion.core import Core
from companion.store import encode


def main(settings, now):
    calls, units, records = [], [], []
    original_selector = life_worker.native_selector

    class Finished(Exception):
        pass

    def provider(request):
        assert request.url.host == "synthetic.invalid"
        calls.append(json.loads(request.content))
        return httpx.Response(200, json={"choices": [{"message": {
            "role": "assistant", "content": '{"decision":"SKIP"}'}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 100, "completion_tokens": 20}})

    class OfflineClient(httpx.AsyncClient):
        def __init__(self, *args, **kwargs):
            kwargs.setdefault("transport", httpx.MockTransport(provider))
            super().__init__(*args, **kwargs)

    def observe(core, worker_id):
        selector = original_selector(core, worker_id)
        step = selector.step

        async def measured():
            before = core.store.one("SELECT COUNT(*) n FROM model_usage")["n"]
            result = await step()
            admitted = core.store.one("SELECT COUNT(*) n FROM model_usage")["n"] - before
            assert admitted <= 1
            units.append({"lane": result.lane, "admissions": admitted,
                          "progressed": result.result.progressed})
            records[:] = rem.records(core)
            if records and records[0]["state"] == "SUCCEEDED":
                raise Finished
            return result

        selector.step = measured
        return selector

    async def stop(*args, **kwargs):
        raise Finished

    # Only time, provider HTTP, termination and observation are controlled.
    # All selection, scheduling, admission, execution and persistence stay native.
    life_worker.Core = partial(Core, clock=lambda: now)
    life_worker.httpx.AsyncClient = OfflineClient
    life_worker.native_selector = observe
    life_worker.Wakeup.wait = stop
    try:
        asyncio.run(life_worker.life_worker(settings))
    except Finished:
        pass
    print(encode({"calls": calls, "units": units, "records": records}), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True)
    parser.add_argument("--now", required=True)
    args = parser.parse_args()
    main(Settings.model_validate_json(Path(args.config).read_text("utf-8")), args.now)
