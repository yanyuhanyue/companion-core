"""Real HTTP plus an independently opened native Core worker; only providers are mocked."""

import asyncio
import json
import threading

import httpx
import numpy as np
import pytest

from companion.core import Core
from companion.model import RECALL_RULES, ModelClient
from companion.retrieval import SemanticIndex
from tests.conftest import event, run
from tests.test_memory_commands import fact
from tests.test_onebot_capture import real_core_http
from tests.test_paid_model_gate import approved_live


@pytest.mark.parametrize("path", ["worker", "worker_forget", "search", "search_forget"])
def test_inference_keeps_http_responsive_and_worker_fenced(core, tmp_path, monkeypatch, path):
    mid = fact(core, 1, "青禾项目使用蓝色配色")
    approved_live(core, tmp_path)
    entered, release, finished = threading.Event(), threading.Event(), threading.Event()
    calls = []

    def infer(self, query, texts):
        entered.set()
        try:
            assert release.wait(5)
            return tuple(np.array([1., 0.]) for _ in texts), np.array([1., 0.])
        finally:
            finished.set()

    monkeypatch.setattr(SemanticIndex, "_infer", infer)

    def provider(request):
        messages = json.loads(request.content)["messages"]
        calls.append(messages)
        text = '{"relevant":["m0"]}' if messages[0]["content"] == RECALL_RULES else "记得蓝色配色。"
        return httpx.Response(200, json={"choices": [{"message": {"content": text}}],
                                        "usage": {"prompt_tokens": 100, "completion_tokens": 20}})

    async def scenario():
        async with real_core_http(core) as port, httpx.AsyncClient(
            base_url=f"http://127.0.0.1:{port}", trust_env=False,
            headers={"Authorization": "Bearer " + core.settings.bridge_token.get_secret_value()},
        ) as client, httpx.AsyncClient(transport=httpx.MockTransport(provider)) as model_http:
            with Core(core.settings, ModelClient(core.settings, model_http), core.clock) as worker:
                assert worker.store is not core.store
                if path.startswith("worker"):
                    response = await client.post("/internal/v1/inbox/events", json=event(
                        core, 2, "青禾项目使用什么配色").model_dump(mode="json"),
                        headers={"X-Idempotency-Key": "async-memory-input"})
                    assert response.status_code == 202, response.text
                    task = asyncio.create_task(worker.run_once("independent-memory-worker"))
                else:
                    task = asyncio.create_task(client.post("/internal/v1/memories/search", json={
                        "query": "青禾项目", "requester_actor_id": "owner", "limit": 6,
                    }))
                try:
                    assert await asyncio.to_thread(entered.wait, 3)
                    health = await asyncio.wait_for(client.get("/internal/v1/health/live"), 1)
                    assert health.status_code == 200 and not task.done()
                    if path.endswith("forget"):
                        core.memory.decide(mid, "forget", core.clock())
                    release.set()
                    result = await task
                    if path == "worker_forget":
                        assert result
                        job = core.store.one("SELECT state,last_error_code FROM jobs "
                                             "WHERE event_id='evt_test_2'")
                        assert job == {"state": "CANCELLED", "last_error_code": "REPLY_CANCELLED"}
                        assert not core.store.rows("SELECT * FROM memory_vectors")
                        assert not core.store.rows("SELECT * FROM model_usage")
                    elif path == "worker":
                        assert result
                        assert len(calls) == 2
                        assert "青禾项目使用蓝色配色" in json.dumps(calls[-1], ensure_ascii=False)
                        assert core.store.rows("SELECT scenario,state FROM model_usage") == [
                            {"scenario": "memory_review", "state": "SETTLED"},
                            {"scenario": "chat", "state": "SETTLED"},
                        ]
                    elif path == "search":
                        assert result.status_code == 200, result.text
                        assert result.json()["items"][0]["memory_id"] == mid
                    else:
                        assert result.status_code == 409, result.text
                        assert not core.store.rows("SELECT * FROM memory_vectors")
                    if path != "worker":
                        assert not calls
                finally:
                    release.set()
                    await asyncio.gather(task, return_exceptions=True)
                    assert await asyncio.to_thread(finished.wait, 3)

    run(scenario())
