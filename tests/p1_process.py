"""Independent Core process; the only provider transport is a synthetic in-memory mock."""

import argparse
import asyncio
import json
import sys
from pathlib import Path
from unittest.mock import patch

import httpx

from companion.config import Settings
from companion.core import Core
from companion.model import ModelClient


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True)
    parser.add_argument("--now", required=True)
    parser.add_argument("--mode", choices=("fence", "command", "inspect"), required=True)
    args = parser.parse_args()
    raw = await asyncio.to_thread(Path(args.config).read_text, "utf-8")
    settings = Settings.model_validate_json(raw)
    if (settings.model_url != "https://synthetic.invalid/v1"
        or settings.model_key.get_secret_value() != "synthetic-key"):
        raise ValueError("SYNTHETIC_PROCESS_ONLY")
    calls = []

    def provider(request):
        calls.append(str(request.url))
        return httpx.Response(200, json={"choices": [{"message": {"content": "SYNTHETIC"},
            "finish_reason": "stop"}], "usage": {"prompt_tokens": 20, "completion_tokens": 2}})

    async def pause(_request):
        print("CONTEXT_READY", flush=True)
        async with asyncio.timeout(20):
            assert (await asyncio.to_thread(sys.stdin.readline)).strip() == "continue"

    async with httpx.AsyncClient(transport=httpx.MockTransport(provider), trust_env=False,
        event_hooks={"request": [pause]} if args.mode == "fence" else {}) as client:
        with patch("companion.retrieval.SemanticIndex.warmup", lambda self: None), Core(
            settings, ModelClient(settings, client), clock=lambda: args.now,
        ) as core:
            if args.mode != "inspect":
                await core.run_once("synthetic-p1-process")
            print(json.dumps({"calls": calls, "affect": core.affect.snapshot(),
                "jobs": core.store.rows("SELECT state,last_error_code FROM jobs"),
                "outbox": core.store.rows("SELECT content_json FROM outbox")}), flush=True)


if __name__ == "__main__":
    asyncio.run(main())
