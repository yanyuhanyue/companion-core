"""Real HTTP v1 acceptance followed by the independent life-worker process."""

import asyncio
import json
import subprocess
import sys
from functools import partial
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from companion import api, life_worker
from companion.config import Settings
from companion.core import Core
from companion.memory_context import MemoryContext
from companion.model import EXTRACT_RULES, ModelClient
from companion.store import encode
from tests.conftest import event, run
from tests.test_paid_model_gate import approved_live


def provider_response(text):
    return httpx.Response(200, json={
        "choices": [{"message": {"content": text}}],
        "usage": {"prompt_tokens": 100, "completion_tokens": 20, "prompt_cache_hit_tokens": 0},
    })


def run_isolated_worker(settings_path, now):
    """Child process: actual loop and Core/store, fixed time, only provider HTTP mocked."""
    settings = Settings.model_validate_json(Path(settings_path).read_text("utf-8"))
    calls = []

    def provider(request):
        payload = json.loads(request.content)
        assert payload["messages"][0]["content"] == EXTRACT_RULES
        calls.append("memory_extract")
        sources = json.loads(payload["messages"][-1]["content"])
        source = next((row for row in sources if row["text"] == "青禾项目下一步检查配色"), None)
        candidates = [{
            "subject": "project", "kind": "fact", "fact_key": "next_step",
            "content": "青禾项目下一步检查配色", "evidence_event_id": source["event_id"],
            "evidence_quote": "青禾项目下一步检查配色",
        }] if source else []
        return provider_response(encode({"memories": candidates}))

    original_client = httpx.AsyncClient

    def offline_client(**kwargs):
        kwargs.setdefault("transport", httpx.MockTransport(provider))
        return original_client(**kwargs)

    async def stop(*_args):
        raise asyncio.CancelledError

    life_worker.Core = partial(Core, clock=lambda: now)
    life_worker.httpx.AsyncClient = offline_client
    life_worker.Wakeup.wait = stop
    try:
        asyncio.run(life_worker.life_worker(settings))
    except asyncio.CancelledError:
        pass
    print(encode({"calls": calls}))


@pytest.mark.parametrize("batch", ["text", "mixed_image", "mixed_media_caption", "only_media"])
def test_http_acceptance_independent_worker_and_restart(core, tmp_path, monkeypatch, batch):
    approved_live(core, tmp_path)
    core.settings.memory_enabled = True
    core.settings.extraction_min_events = 1
    core.settings.extraction_idle_seconds = 0
    # Time is injected; real ingress authentication, normalization and storage still run.
    monkeypatch.setattr(api, "Core", partial(Core, clock=core.clock))
    payloads = [event(core, 1, "青禾项目下一步检查配色").model_dump(mode="json")]
    if batch != "text":
        payloads.append(event(core, 2, "配色参考图" if batch == "mixed_media_caption" else "")
                        .model_dump(mode="json"))
        payloads[-1]["event_type"] = ("message.media_received" if batch == "mixed_media_caption"
                                      else "message.received")
        payloads[-1]["payload"]["media"] = [
            {"kind": "image", "media_ref": "synthetic", "sha256": "a" * 64},
        ]
    if batch == "only_media":
        payloads[0]["payload"]["text"] = ""
        payloads[0]["event_type"] = "message.media_received"
        payloads[0]["payload"]["media"] = [
            {"kind": "voice", "media_ref": "synthetic", "sha256": "b" * 64},
        ]
    headers = {"Authorization": "Bearer " + core.settings.bridge_token.get_secret_value(),
               "X-Idempotency-Key": "independent-memory-input"}
    with TestClient(api.create_app(core.settings)) as client:
        denied = client.post("/internal/v1/inbox/events", json=payloads[0],
                             headers={**headers, "Authorization": "Bearer wrong"})
        assert denied.status_code == 401
        async def chat():
            async with httpx.AsyncClient(transport=httpx.MockTransport(
                lambda _: provider_response("好，下次继续检查配色。"),
            )) as transport:
                core.model = ModelClient(core.settings, transport)
                assert await core.run_once()

        # Each historical input completes its reply before the next one supersedes it.
        for payload in payloads:
            accepted = client.post("/internal/v1/inbox/events", json=payload,
                                   headers={**headers, "X-Idempotency-Key": payload["event_id"]})
            assert accepted.status_code == 202
            run(chat())
        assert all(row["state"] == "SUCCEEDED" for row in core.store.rows("SELECT state FROM jobs"))
        assert not core.store.rows("SELECT * FROM episodes")
        chat_usage = core.store.rows("SELECT scenario,state FROM model_usage ORDER BY rowid")
        # Each ordinary input has one settled model reply before extraction.
        assert chat_usage == [
            {"scenario": "chat", "state": "SETTLED"}
            for _ in payloads
        ]
        # Secrets here are public synthetic fixtures, supplied to the local mock transport only.
        config = core.settings.model_dump(mode="json")
        config["model_key"] = core.settings.model_key.get_secret_value()
        config["bridge_token"] = core.settings.bridge_token.get_secret_value()
        settings_path = tmp_path / "worker-settings.json"
        settings_path.write_text(encode(config), encoding="utf-8")
        script = ("import sys; from tests.test_next_memory_worker import run_isolated_worker; "
                  "run_isolated_worker(sys.argv[1], sys.argv[2])")
        for attempt in range(3):
            completed = subprocess.run(
                [sys.executable, "-c", script, str(settings_path), core.clock()],
                cwd=Path(__file__).resolve().parents[1], capture_output=True, text=True,
                encoding="utf-8", timeout=30, check=True,
            )
            result = json.loads(completed.stdout.splitlines()[-1])
            assert result["calls"] == (["memory_extract"] if attempt == 0 else [])
        row = core.store.one("SELECT * FROM memories")
        if batch == "only_media":
            assert row is None
        else:
            assert row["status"] == "HYPOTHESIS" and row["content"] == "青禾项目下一步检查配色"
        assert all(row["memory_processed"] for row in core.store.rows("SELECT * FROM events"))
        assert not core.store.rows("SELECT * FROM audit_log WHERE action='memory.batch_failed'")
        assert not core.store.rows("SELECT * FROM runtime_meta WHERE key LIKE 'extract_attempts:%'")
        assert core.store.one("SELECT COUNT(*) n FROM episodes")["n"] == int(batch != "only_media")
        assert core.store.one("SELECT COUNT(*) n FROM conversation_summaries")["n"] == int(
            batch != "only_media",
        )
        view = MemoryContext(core.memory)
        conversation = core.store.one("SELECT conversation_id FROM conversations")
        continuation = view.continuation(conversation["conversation_id"], core.clock())
        expected_sources = ([] if batch == "only_media" else ["evt_test_1", "evt_test_2"]
                            if batch == "mixed_media_caption" else ["evt_test_1"])
        assert [excerpt["event_id"] for item in continuation["items"]
                for excerpt in item["excerpts"]] == expected_sources
        assert [r["event_id"] for r in core.store.rows(
            "SELECT event_id FROM episode_events ORDER BY event_id",
        )] == expected_sources
        assert core.store.rows("SELECT scenario,state FROM model_usage ORDER BY rowid") == [
            *chat_usage,
            {"scenario": "memory_extract", "state": "SETTLED"},
        ]
        response = client.post("/internal/v1/memories/search", headers=headers, json={
            "query": "青禾项目", "requester_actor_id": "owner",
        })
        assert response.status_code == 200
        if row:
            assert row["memory_id"] in response.text
        else:
            assert response.json() == {"items": []}
