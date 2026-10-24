"""Authenticated TCP input -> original chat settlement -> separate Life process/restart."""

import asyncio
import json
import os
import sys
from pathlib import Path

import httpx

from companion.memory_context import MemoryContext
from companion.model import ModelClient
from companion.store import encode
from tests.conftest import event, run
from tests.test_diary import DAY
from tests.test_onebot_capture import real_core_http
from tests.test_paid_model_gate import approved_live


def test_authenticated_ingress_and_independent_nightly_steps_restart(core, tmp_path):
    approved_live(core, tmp_path)
    cfg = core.settings
    cfg.memory_enabled = cfg.diary_enabled = True
    cfg.reflection_enabled = cfg.consolidation_enabled = True
    workspace = Path(__file__).resolve().parents[1]
    cfg.extraction_min_events = 3
    cfg.extraction_idle_seconds = 0
    cfg.consolidation_catchup_days = 1
    cfg.reflection_daily_micro_usd = 50000
    cfg.reflection_monthly_micro_usd = 100000

    def chat_response(request):
        assert request.url.host == "synthetic.invalid"
        return httpx.Response(200, json={
            "choices": [{"message": {"content": "已收到这条项目记录。"}}],
            "usage": {"prompt_tokens": 100, "completion_tokens": 20},
        })

    async def scenario():
        async with (
            real_core_http(core) as port,
            httpx.AsyncClient(
                base_url=f"http://127.0.0.1:{port}", trust_env=False,
                headers={"Authorization": "Bearer " + cfg.bridge_token.get_secret_value()},
            ) as client,
            httpx.AsyncClient(transport=httpx.MockTransport(chat_response)) as transport,
        ):
            core.model = ModelClient(cfg, transport)
            texts = ("青禾项目先检查配色", "松林项目整理阅读笔记", "周末再核对资料")
            for number, text in enumerate(texts, 1):
                payload = event(core, number, text, at=DAY.isoformat() + "T04:00:00+00:00")
                accepted = await client.post("/internal/v1/inbox/events",
                    headers={"X-Idempotency-Key": payload.event_id},
                    json=payload.model_dump(mode="json"))
                assert accepted.status_code == 202, accepted.text
                assert await core.run_once()
            queued_before = core.store.one("SELECT COUNT(*) AS n FROM outbox")["n"]
            config = cfg.model_dump(mode="json")
            config.update(model_key=cfg.model_key.get_secret_value(),
                          bridge_token=cfg.bridge_token.get_secret_value())
            path = tmp_path / "nightly-settings.json"
            await asyncio.to_thread(path.write_text, encode(config), encoding="utf-8")
            results = []
            for attempt in range(3):
                child = await asyncio.create_subprocess_exec(
                    sys.executable, "-B", "-c",
                    "import sys; from tests.support.nightly_process import run_worker; "
                    "run_worker(sys.argv[1], sys.argv[2], sys.argv[3]=='1')",
                    str(path), core.clock(), "1" if attempt == 0 else "0",
                    cwd=workspace,
                    stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
                )
                try:
                    stdout, stderr = await asyncio.wait_for(child.communicate(), 30)
                except BaseException:
                    child.kill()
                    await child.communicate()
                    raise
                assert child.returncode == 0, stderr.decode("utf-8", errors="replace")
                result = json.loads(stdout.decode("utf-8").splitlines()[-1])
                assert result["pid"] != os.getpid()
                assert all(unit["admissions"] <= 1 for unit in result["units"])
                results.append(result)
            assert results[0]["calls"] == ["memory_extract"]
            assert results[1]["calls"] == ["memory_review", "diary", "reflection"]
            assert results[2]["calls"] == []
            assert core.consolidation.status(DAY)["state"] == "COMPLETE"
            assert core.store.one("SELECT COUNT(*) AS n FROM outbox")["n"] == queued_before
            conversation = core.store.one("SELECT conversation_id FROM events LIMIT 1")
            assert MemoryContext(core.memory).continuation(
                conversation["conversation_id"], core.clock(),
            )["items"]
            assert len(core.store.rows("SELECT * FROM memories")) == 2
            assert not core.store.one("SELECT 1 FROM model_usage WHERE state!='SETTLED'")
    run(scenario())
