"""One native Core worker process; only the synthetic provider transport is replaced."""

import asyncio
import json
import os
import sys
from pathlib import Path

import httpx

from companion.config import Settings
from companion.core import Core
from companion.model import ModelClient
from companion.retrieval import SemanticIndex

settings_path, now, output, kind = sys.argv[1:]
settings = Settings.model_validate_json(Path(settings_path).read_text("utf-8"))
if settings.model_url != "https://synthetic.invalid/v1":
    raise ValueError("SYNTHETIC_PROVIDER_REQUIRED")
SemanticIndex.warmup = lambda self: None
calls = []


def provider(request):
    calls.append(json.loads(request.content))
    if kind == "unknown":
        raise httpx.ReadTimeout("synthetic unknown poke model response", request=request)
    decision = {"kind": kind, **({"text": "在呢。"} if kind == "text" else {})}
    return httpx.Response(200, json={"choices": [{"message": {
        "content": json.dumps(decision)}, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 100, "completion_tokens": 20}})


async def run():
    async with httpx.AsyncClient(transport=httpx.MockTransport(provider)) as client:
        with Core(settings, ModelClient(settings, client), lambda: now) as worker:
            progressed = await worker.run_once("native-poke-process")
            return {"pid": os.getpid(), "progressed": progressed, "calls": calls}


Path(output).write_text(json.dumps(asyncio.run(run())), encoding="utf-8")
