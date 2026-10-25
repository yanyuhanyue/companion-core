"""One native chat worker; only its external model transport is synthetic."""

import asyncio
import json
import sys
from pathlib import Path

import httpx

from companion.config import Settings
from companion.core import Core
from companion.model import ModelClient
from companion.retrieval import SemanticIndex

config, now, candidate, output = sys.argv[1:]
settings = Settings.model_validate_json(Path(config).read_text("utf-8"))
SemanticIndex.warmup = lambda self: None
calls = []


def provider(request):
    payload = json.loads(request.content)
    if candidate != "-":
        assert candidate in json.dumps(payload, ensure_ascii=False)
    calls.append(payload)
    return httpx.Response(200, json={"choices": [{"message": {
        "content": "太棒了，恭喜你！" + ("⬡" + candidate + "⬡" if candidate != "-" else "")},
        "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 100, "completion_tokens": 20}})


async def main():
    async with httpx.AsyncClient(transport=httpx.MockTransport(provider)) as client:
        with Core(settings, ModelClient(settings, client), lambda: now) as core:
            worked = await core.run_once("native-meme-host")
            return {"worked": worked, "calls": calls, "jobs": core.store.rows(
                "SELECT kind,state,last_error_code,next_attempt_at FROM jobs")}


Path(output).write_text(json.dumps(asyncio.run(main())), encoding="utf-8")
