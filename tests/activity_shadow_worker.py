"""Independent native Core; all external provider transport is synthetic."""

import asyncio
import json
import sys
from pathlib import Path
from unittest.mock import patch

import httpx

from companion import owner_authority
from companion.config import Settings
from companion.core import Core
from companion.model import ModelClient
from companion.owner_authority_protocol import AuthorityStop


async def exercise(value):
    settings = Settings.model_validate(value["settings"])
    if settings.model_key.get_secret_value() != "synthetic-key":
        raise ValueError("SYNTHETIC_PROCESS_ONLY")
    calls = []

    def provider(request):
        calls.append({"payload": json.loads(request.content),
                      "wire_utf8": request.content.decode("utf-8")})
        if value["boundary"] == "unknown":
            raise httpx.ReadTimeout("synthetic unknown after dispatch", request=request)
        usage = {"prompt_tokens": 100, "completion_tokens": 4}
        if value.get("cache_usage") is not None:
            usage["prompt_cache_hit_tokens"] = value["cache_usage"]
        return httpx.Response(200, json={"choices": [{"message": {
            "content": "SYNTHETIC shadow comparison reply"}, "finish_reason": "stop"}],
            "usage": usage})

    async def before_request(request):
        if value["boundary"] == "cancel":
            view = owner_authority.current(core.store, settings, core.clock, purpose="chat")
            owner_authority.stop(core.store, settings, core.clock, AuthorityStop(
                operation_id="synthetic-shadow-cancel", authority=view.authority.as_dict()))

    async with httpx.AsyncClient(transport=httpx.MockTransport(provider), trust_env=False,
        event_hooks={"request": [before_request]}) as client:
        with patch("companion.retrieval.SemanticIndex.warmup", lambda self: None), Core(
            settings, ModelClient(settings, client), clock=lambda: value["now"],
        ) as core:
            worked = await core.run_once("activity-shadow-worker", job_id=value["job_id"])
            state = core.store.one("SELECT state,last_error_code FROM jobs WHERE job_id=?",
                                    (value["job_id"],))
    return {"worked": worked, "requests": calls, "job": state}


if __name__ == "__main__":
    print(json.dumps(asyncio.run(exercise(json.loads(Path(sys.argv[1]).read_text("utf-8"))))))
