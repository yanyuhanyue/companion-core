"""Run one original worker iteration in an OS process with a synthetic model endpoint."""

import asyncio
import json
import os
from functools import partial
from pathlib import Path
from types import SimpleNamespace

import httpx

from companion import life_worker as entrypoint
from companion.config import Settings
from companion.core import Core


def run_worker(settings_path, now, text):
    settings = Settings.model_validate_json(Path(settings_path).read_text("utf-8"))
    calls = []
    original_client = httpx.AsyncClient

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

    async def stop(*args, **kwargs):
        raise Finished

    entrypoint.Core = partial(Core, clock=lambda: now)
    entrypoint.httpx = SimpleNamespace(AsyncClient=offline_client)
    entrypoint.Wakeup.wait = stop
    try:
        asyncio.run(entrypoint.life_worker(settings))
    except Finished:
        pass
    print(json.dumps({"pid": os.getpid(), "parent_pid": os.getppid(), "calls": calls}), flush=True)
