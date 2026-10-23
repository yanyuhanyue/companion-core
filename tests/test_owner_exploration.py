"""Natural owner browsing requests reach a real Reader queue, never an invented tool."""

import json
import ssl

import httpcore
import httpx
import pytest

from companion.core import Core
from companion.model import ModelClient
from companion.owner_exploration import requested_source
from companion.store import encode
from source_reader import worker_v1
from source_reader.bilibili import BilibiliReader
from source_reader.network import PublicHTTP
from tests.conftest import run
from tests.test_reading_followup import READER_AUTH, admit, window
from tests.test_source_reader import META, Responses, response


@pytest.mark.parametrize("text,source", [
    ("你现在应该可以刷b站了吧", "bilibili"), ("去看看小黑盒吧", "heybox"),
    ("请浏览 bilibili", "bilibili"), ("刷B站", "bilibili"),
    ("不要刷B站", None), ("他说‘刷B站’", None), ("我昨天刷B站", None),
    ("刷B站后给别人发消息", None), ("我想知道为什么他说不能刷B站", None),
])
def test_browsing_request_requires_an_explicit_whole_owner_utterance(text, source):
    assert requested_source(text) == source


@pytest.mark.parametrize("context_soft_tokens", [3200, 16000])
def test_signed_owner_request_runs_one_discovery_and_reports_its_real_coverage(
    core, tmp_path, monkeypatch, context_soft_tokens,
):
    async def scenario():
        async with window(core, tmp_path, monkeypatch,
                          context_soft_tokens=context_soft_tokens) as (client, config, plan):
            event, accepted = await admit(client, core, config, plan, 1, "你现在可以刷B站了吧")
            queued = core.store.one("SELECT * FROM exploration_requests")
            assert queued["operation"] == "discover" and queued["state"] == "QUEUED"
            assert queued["request_key"] == "owner-browse:" + event["event_id"]
            assert core.store.rows("SELECT event_id FROM exploration_evidence") == [
                {"event_id": event["event_id"]},
            ]
            assert core.claim_job("not-until-source-result") is None
            backend = Responses(response({"code": 0, "data": {"list": [
                {**META["data"], "bvid": "BV1xx411c7mD", "title": "发现的恒星故事"},
            ]}}))
            pool = httpcore.AsyncConnectionPool(
                ssl_context=ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT),
                network_backend=backend, max_keepalive_connections=0,
            )
            calls = []

            def provider(request):
                payload = json.loads(request.content)
                calls.append(payload)
                assert "tools" not in payload
                return httpx.Response(200, json={
                    "choices": [{"message": {"content": "发现了一个恒星故事的标题，还没读正文。"}}],
                    "usage": {"prompt_tokens": 100, "completion_tokens": 20},
                })

            async with PublicHTTP(pool) as public, httpx.AsyncClient(
                base_url=str(client.base_url), headers=READER_AUTH, trust_env=False,
            ) as reader_http, httpx.AsyncClient(
                transport=httpx.MockTransport(provider),
            ) as model_http:
                reader = BilibiliReader(public, clock=core.clock)
                assert await worker_v1.tick(reader_http, reader, "bili", source="bilibili",
                                            clock=lambda: core.clock.now)
                assert not await worker_v1.tick(reader_http, reader, "bili", source="bilibili",
                                                clock=lambda: core.clock.now)
                with Core(core.settings, ModelClient(core.settings, model_http),
                          core.clock) as independent:
                    assert await independent.run_once("browsing-reply")
                    assert not await independent.run_once("no-repeat")
            assert len(backend.requests) == 1 and not backend.rows
            assert len(calls) == 1
            prompt = encode(calls[0]["messages"])
            assert "发现的恒星故事" in prompt and "DISCOVERED" in prompt
            assert "只表示发现标题" in prompt and "系统未启用外部执行工具" not in prompt
            assert core.store.one("SELECT state FROM exploration_requests")["state"] == "DONE"
            assert core.store.one("SELECT state FROM jobs WHERE job_id=?", (accepted["job_id"],))[
                "state"] == "SUCCEEDED"
            sources = core.store.rows("SELECT event_id FROM job_sources WHERE job_id=?",
                                      (accepted["job_id"],))
            assert {row["event_id"] for row in sources} >= {
                event["event_id"], core.store.one("SELECT event_id FROM observations")["event_id"],
            }
    run(scenario())


def test_paused_life_does_not_schedule_natural_browsing(core, tmp_path, monkeypatch):
    async def scenario():
        async with window(core, tmp_path, monkeypatch) as (client, config, plan):
            core.store.execute("INSERT OR REPLACE INTO runtime_meta VALUES ('life_paused','1')")
            await admit(client, core, config, plan, 1, "刷B站")
            assert not core.store.rows("SELECT * FROM exploration_requests")
    run(scenario())


def test_uncertain_browsing_keeps_the_original_attempt_and_expense(core, tmp_path, monkeypatch):
    async def scenario():
        async with window(core, tmp_path, monkeypatch) as (client, config, plan):
            event, _ = await admit(client, core, config, plan, 1, "刷B站")
            row = core.store.one("SELECT * FROM exploration_requests")
            core.store.execute("UPDATE exploration_requests SET state='UNKNOWN',"
                               "last_error_code='READER_NETWORK_UNKNOWN' WHERE request_id=?",
                               (row["request_id"],))
            await admit(client, core, config, plan, 1, "刷B站")
            assert len(core.store.rows("SELECT * FROM exploration_requests")) == 1
            assert core.store.one("SELECT state FROM exploration_requests")["state"] == "UNKNOWN"
            saved = core.store.one("SELECT request_key FROM exploration_requests")
            assert saved["request_key"] == "owner-browse:" + event["event_id"]
    run(scenario())
