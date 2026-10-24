"""Run one original worker iteration in an OS process with a synthetic model endpoint."""

import asyncio
import json
import os
from functools import partial
from pathlib import Path

import httpx

from companion import __main__ as entrypoint
from companion import core as core_module
from companion.config import Settings
from companion.core import Core


def run_worker(settings_path, now, text):
    settings = Settings.model_validate_json(Path(settings_path).read_text("utf-8"))
    calls = []
    original_client, original_run = httpx.AsyncClient, Core.run_once

    class Finished(Exception):
        pass

    def provider(request):
        assert request.url.host == "synthetic.invalid"
        payload = json.loads(request.content)
        material = json.loads(payload["messages"][-1]["content"])
        assert material["kind"] == "commitment"
        assert material["outcome"] == "EXPLAINED_FAILURE"
        calls.append(material["outcome"])
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": text}}],
                "usage": {"prompt_tokens": 100, "completion_tokens": 20},
            },
        )

    def offline_client(**kwargs):
        kwargs.setdefault("transport", httpx.MockTransport(provider))
        return original_client(**kwargs)

    async def once(self, *args, **kwargs):
        await original_run(self, *args, **kwargs)
        raise Finished

    Core.run_once = once
    core_module.Core = partial(Core, clock=lambda: now)
    entrypoint.httpx.AsyncClient = offline_client
    try:
        asyncio.run(entrypoint.worker(settings))
    except Finished:
        pass
    print(json.dumps({"pid": os.getpid(), "parent_pid": os.getppid(), "calls": calls}), flush=True)
