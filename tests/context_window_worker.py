"""Independent Core worker for loaded joint-window settings; provider transport is synthetic."""

import asyncio
import json
import sys
from datetime import datetime
from pathlib import Path

import httpx

from companion.config import Settings
from companion.core import Core
from companion.model import ModelClient

value = json.loads(Path(sys.argv[1]).read_text("utf-8"))
settings = Settings.model_validate(value["settings"])
calls = []


def provider(request):
    calls.append(json.loads(request.content))
    return httpx.Response(200, json={"choices": [{"message": {"content": "synthetic reply"}}],
        "usage": {"prompt_tokens": 100, "completion_tokens": 4}})


async def exercise():
    async with httpx.AsyncClient(transport=httpx.MockTransport(provider)) as client:
        with Core(settings, ModelClient(settings, client), clock=lambda: value["now"]) as core:
            assert datetime.fromisoformat(core.clock()).tzinfo is not None
            assert await core.run_once("context-window-worker", job_id=value["job_id"])


asyncio.run(exercise())
print(json.dumps({"mock_provider_requests": len(calls)}))
