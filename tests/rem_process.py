"""Run the original Core REM worker in a separate process; fake provider HTTP only."""

import argparse
import asyncio
import json
import os
from pathlib import Path

import httpx

from companion.config import Settings
from companion.core import Core
from companion.model import ModelClient
from companion.store import encode


async def main(args, settings):
    def provider(request):
        assert request.url.host == "synthetic.invalid"
        with Path(args.log).open("a", encoding="utf-8") as log:
            log.write(encode({"pid": os.getpid(), "request": json.loads(request.content)}) + "\n")
            log.flush()
            os.fsync(log.fileno())
        if args.crash:
            os._exit(23)
        return httpx.Response(200, json={"choices": [{"message": {
            "role": "assistant", "content": '{"decision":"SKIP"}'}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 100, "completion_tokens": 20}})

    async with httpx.AsyncClient(transport=httpx.MockTransport(provider)) as client:
        with Core(settings, ModelClient(settings, client), lambda: args.now) as core:
            await core.run_once(job_id=args.job)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    for argument in ("config", "now", "job", "log"):
        parser.add_argument("--" + argument, required=True)
    parser.add_argument("--crash", action="store_true")
    args = parser.parse_args()
    settings = Settings.model_validate_json(Path(args.config).read_text("utf-8"))
    asyncio.run(main(args, settings))
