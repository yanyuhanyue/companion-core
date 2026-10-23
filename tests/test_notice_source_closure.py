"""Every goal evidence source revokes its notice through the real recall boundary."""

import json
from datetime import UTC, datetime, timedelta

import httpx
import pytest

from companion import live_text, notices
from companion.core import Core
from companion.goals import CommitmentRequest, GoalProposal, GoalScope
from companion.model import ModelClient
from tests.conftest import run
from tests.test_input_interrupt_bridge import model_response
from tests.test_input_protocol import environment, notice
from tests.test_reply_revision_api import admit


@pytest.mark.parametrize("recall_at", ["queued", "provider", "ready"])
def test_additional_goal_evidence_recall_cancels_notice_and_keeps_window_open(
    core, tmp_path, monkeypatch, recall_at,
):
    async def scenario():
        async with environment(core, tmp_path, monkeypatch) as (client, config, plan, _):
            provider_calls = []
            recall_body = None

            async def recall():
                nonlocal recall_body
                recall_body = notice(config, plan, datetime.now(UTC), 1)
                response = await client.post("/internal/v1/live-text/inputs/recall",
                                              json=recall_body)
                assert response.status_code == 200, response.text
                assert response.json()["state"] == "RECALLED", response.text
                assert response.json()["duplicate"] is False

            async def provider(request):
                messages = json.loads(request.content)["messages"]
                if '"kind":"commitment"' in messages[-1]["content"]:
                    provider_calls.append("notice")
                    if recall_at == "provider":
                        await recall()
                    return model_response("这次没能在约定时间内完成资料阅读。")
                return model_response("我收到这条请求了。")

            async with httpx.AsyncClient(transport=httpx.MockTransport(provider)) as model_http:
                core.model = ModelClient(core.settings, model_http)
                inputs = []
                for number, text in [(1, "这份资料的主题是合成天文项目"),
                                     (2, "请稍后帮我阅读并通知结果")]:
                    event, _ = await admit(client, config, plan, number, text)
                    inputs.append(event)
                    assert await core.run_once()
                commitment = core.goals.accept_commitment(CommitmentRequest(
                    proposal=GoalProposal(topic="合成天文项目",
                        source_event_ids=[event["event_id"] for event in inputs],
                        scope=GoalScope(operation="read_content",
                            url="https://www.bilibili.com/video/BV1xx411c7mD")),
                    accepted_event_id=inputs[1]["event_id"],
                    deadline=core.clock.now + timedelta(seconds=60),
                ))
                core.clock.advance(61)
                assert core.goals.claim() is None
                job = core.store.one("SELECT * FROM jobs WHERE kind='activity'")
                expected_sources = {event["event_id"] for event in inputs}
                assert {row["source_event_id"] for row in core.store.rows(
                    "SELECT source_event_id FROM event_dependencies WHERE event_id=?",
                    (job["event_id"],),
                )} == expected_sources
                assert {row["event_id"] for row in core.store.rows(
                    "SELECT event_id FROM job_sources WHERE job_id=?", (job["job_id"],),
                )} == expected_sources | {job["event_id"]}
                if recall_at == "queued":
                    await recall()
                with Core(core.settings, core.model, core.clock) as worker:
                    assert worker.store is not core.store
                    assert await worker.run_once("notice-source-closure") is (recall_at != "queued")
                if recall_at == "ready":
                    assert core.store.one("SELECT state FROM outbox WHERE origin_event_id=?",
                                          (job["event_id"],)) == {"state": "PENDING"}
                    await recall()

                assert core.store.one("SELECT state,last_error_code FROM jobs WHERE job_id=?",
                                      (job["job_id"],)) == (
                    {"state": "SUCCEEDED", "last_error_code": None} if recall_at == "ready" else
                    {"state": "CANCELLED", "last_error_code": "PRIVACY_CHANGED"}
                )
                assert core.store.one("SELECT redacted,body_json FROM events WHERE event_id=?",
                                      (job["event_id"],)) == {"redacted": 1, "body_json": "{}"}
                assert core.store.one("SELECT state,redacted FROM goals") == {
                    "state": "CANCELLED", "redacted": 1,
                }
                assert core.store.one("SELECT state FROM commitments") == {"state": "ERASED"}
                assert not core.store.rows("SELECT * FROM outbox WHERE origin_event_id=? "
                                           "AND state<>'CANCELLED'", (job["event_id"],))
                usage = core.store.rows("SELECT state,billed_micro_usd FROM model_usage "
                                        "WHERE scenario='activity'")
                assert usage == ([] if recall_at == "queued" else [
                    {"state": "SETTLED", "billed_micro_usd": 140},
                ])
                assert provider_calls == ([] if recall_at == "queued" else ["notice"])
                assert not live_text.stopped(core.store, plan.sha256)

                repeated = await client.post("/internal/v1/live-text/inputs/recall",
                                             json=recall_body)
                assert repeated.status_code == 200 and repeated.json()["duplicate"]
                conversation_id = core.store.one("SELECT conversation_id FROM events "
                    "WHERE event_id=?", (job["event_id"],))["conversation_id"]
                assert notices.schedule(core.store, core.settings, "commitment",
                    commitment["commitment_id"], conversation_id, core.clock()) == (
                        job["job_id"])
                with Core(core.settings, core.model, core.clock) as worker:
                    assert await worker.run_once("notice-recall-replay") is False
                assert core.store.one("SELECT COUNT(*) AS n FROM jobs WHERE kind='activity'")[
                    "n"] == 1
                assert not live_text.stopped(core.store, plan.sha256)

    run(scenario())
