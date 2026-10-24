"""One native chat-worker iteration in a child process, with offline provider transport."""

import asyncio
import json
import os
import sys
import threading
from functools import partial
from pathlib import Path

import httpx

from companion import __main__ as entrypoint
from companion import core as core_module
from companion.config import Settings
from companion.core import Core
from companion.model import RECALL_RULES
from companion.retrieval import SemanticIndex
from companion.store import Store


def run_worker(settings_path, now):
    settings = Settings.model_validate_json(Path(settings_path).read_text("utf-8"))
    owner_thread = threading.get_ident()
    calls, inference_threads, violations = [], [], []
    original_client, original_run, original_infer = (
        httpx.AsyncClient,
        Core.run_once,
        SemanticIndex._infer,
    )

    class Finished(Exception):
        pass

    def guard(method):
        def checked(self, *args, **kwargs):
            if threading.get_ident() != owner_thread:
                violations.append(method.__name__)
                raise AssertionError("Store crossed the inference thread boundary")
            return method(self, *args, **kwargs)

        return checked

    for name in ("execute", "rows", "one", "transaction"):
        setattr(Store, name, guard(getattr(Store, name)))

    def infer(self, query, contents):
        inference_threads.append(threading.get_ident())
        assert threading.get_ident() != owner_thread
        assert isinstance(query, str) and isinstance(contents, tuple)
        assert all(isinstance(text, str) for text in contents)
        print(json.dumps({"phase": "inference_wait", "pid": os.getpid(),
                          "parent_pid": os.getppid()}), flush=True)
        assert sys.stdin.readline().strip() == "continue"
        # Run the actual cached local encoder. Only the timing gate is synthetic.
        return original_infer(self, query, contents)

    def provider(request):
        assert request.url.host == "synthetic.invalid"
        messages = json.loads(request.content)["messages"]
        reviewing = messages[0]["content"] == RECALL_RULES
        calls.append("memory_review" if reviewing else "chat")
        if not reviewing:
            assert "青禾项目使用蓝色配色" in json.dumps(messages, ensure_ascii=False)
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": '{"relevant":["m0"]}'
                            if reviewing
                            else "青禾项目使用蓝色配色。"
                        }
                    }
                ],
                "usage": {"prompt_tokens": 100, "completion_tokens": 20},
            },
        )

    def offline_client(**kwargs):
        kwargs.setdefault("transport", httpx.MockTransport(provider))
        return original_client(**kwargs)

    async def once(self, *args, **kwargs):
        await original_run(self, *args, **kwargs)
        raise Finished

    SemanticIndex._infer = infer
    Core.run_once = once
    core_module.Core = partial(Core, clock=lambda: now)
    entrypoint.httpx.AsyncClient = offline_client
    try:
        asyncio.run(entrypoint.worker(settings))
    except Finished:
        pass
    print(
        json.dumps(
            {
                "phase": "finished",
                "pid": os.getpid(),
                "owner_thread": owner_thread,
                "inference_threads": inference_threads,
                "store_thread_violations": violations,
                "calls": calls,
            }
        ),
        flush=True,
    )
