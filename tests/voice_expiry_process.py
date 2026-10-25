"""Run the real chat-worker entry point in an isolated synthetic-only process."""

import asyncio
import json
import os
import sys
from pathlib import Path

import httpx

from companion.config import Settings
from companion.core import Core
from companion.model import ModelClient


async def main():
    args = json.loads(await asyncio.to_thread(Path(sys.argv[1]).read_text, encoding="utf-8"))
    settings = Settings.model_validate(args["settings"])
    calls = []

    def provider(request):
        calls.append(request.url.path)
        return httpx.Response(503, json={"error": "unexpected synthetic provider call"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(provider)) as client:
        with Core(settings, ModelClient(settings, client), lambda: args["now"]) as worker:
            worked = await worker.run_once("voice-expiry-independent-chat-worker")
    print(json.dumps({"pid": os.getpid(), "worked": worked, "provider_calls": calls}))


if __name__ == "__main__":
    asyncio.run(main())
