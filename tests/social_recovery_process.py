"""Crash at native planning boundaries in a separate synthetic-only process."""

import asyncio
import json
import os
import sys
from pathlib import Path

import httpx

from companion import budget
from companion.config import Settings
from companion.model import ModelClient
from companion.social_autonomy import SocialAutonomy
from companion.social_autonomy_runtime import run_once
from companion.store import Store, encode


async def main():
    args = json.loads(await asyncio.to_thread(Path(sys.argv[1]).read_text, encoding="utf-8"))
    settings = Settings.model_validate(args["settings"])
    if settings.mode == "live" and settings.model_url != "https://synthetic.invalid/v1":
        raise ValueError("SYNTHETIC_PROVIDER_REQUIRED")
    mode = args["mode"].removeprefix("retry_")
    original_reserve, original_complete = budget.reserve, budget.complete

    def reserve(*args, **kwargs):
        if mode == "before_reserve":
            os._exit(23)
        rid = original_reserve(*args, **kwargs)
        if mode == "reserved":
            os._exit(23)
        return rid

    async def complete(*args, **kwargs):
        result = await original_complete(*args, **kwargs)
        if mode == "settled":
            os._exit(23)
        return result

    def provider(request):
        return httpx.Response(200, json={
            "choices": [{"message": {"content": encode({
                "kind": "qzone_publish", "text": "合成公开阅读想法。",
                "source_refs": [args["source_id"]],
            })}}], "usage": {"prompt_tokens": 100, "completion_tokens": 20},
        })

    budget.reserve, budget.complete = reserve, complete
    store = Store(settings.database)
    try:
        async with httpx.AsyncClient(transport=httpx.MockTransport(provider)) as client:
            module = SocialAutonomy(store, settings, ModelClient(settings, client),
                                    lambda: args["now"])
            if args["mode"].startswith("retry_"):
                await run_once(store, settings, module.model, module.clock, worker_id="child")
            else:
                await module.plan("crashed-social-plan", source_ids=[args["source_id"]])
    finally:
        store.close()


if __name__ == "__main__":
    asyncio.run(main())
