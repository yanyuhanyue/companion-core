"""Native image clues → scoped retrieval → approved facts and originals in final vision."""

import base64
import io
import json
from contextlib import asynccontextmanager

import httpx
import pytest
from PIL import Image

from companion import budget, image_spool, live_text, paid_budget, qq_images
from companion.contracts import CoreError
from companion.model import ModelClient
from companion.store import Store, encode
from tests.conftest import run
from tests.test_live_image_input import options
from tests.test_memory_commands import fact
from tests.test_onebot_capture import capture_approval, source_window
from tests.test_onebot_direct import raw_event
from tests.test_onebot_images import image_segment
from tests.test_onebot_window import reply_approval

PREFERENCE = "我挑选耳机优先考虑轻便和隔音。"
CUES = "图片1：红色轻便头戴耳机；图片2：蓝色头戴耳机。检索主题：耳机、轻便、隔音。"
QUESTION = "结合我以前的需求，这两张图里的哪一个更合适？"


@asynccontextmanager
async def image_memory_window(
    core, tmp_path, *, mutation=None, no_candidates=False, model_attempts=None,
    scope_options=None, reply=None,
):
    memory_id = fact(core, 900, PREFERENCE)
    outsider = fact(core, 901, "未批准的秘密偏好：紫色键盘。")
    pin = live_text.memory_fact(core.store, core.settings, core.clock, memory_id)
    opts = options()
    opts.update(memory_scope="approved_facts", memory_facts=[pin], memory_approval_sha256="f" * 64)
    opts["core_options"].update(memory_enabled=True)
    opts["paid_options"].update(per_request_calls=3)
    if model_attempts is not None:
        opts["max_model_attempts"] = model_attempts
        opts["paid_options"].update(window_calls=model_attempts, daily_calls=model_attempts)
    opts["paid_options"]["image_input"] = opts["paid_options"]["image_input"].model_copy(
        update={"max_images_per_request": 2},
    )
    if scope_options:
        opts["core_options"].update(scope_options.get("core_options", {}))
        opts.update({key: value for key, value in scope_options.items() if key != "core_options"})
    calls, downloads, queries = [], [], []
    originals = []
    for color in ("red", "blue"):
        stream = io.BytesIO()
        Image.new("RGB", (3, 2), color).save(stream, format="PNG")
        originals.append(stream.getvalue())
    original_scores = core.memory.index.scores_async

    async def scores(query, rows, **kwargs):
        queries.append((query, [row["memory_id"] for row in rows]))
        if no_candidates:
            return {row["memory_id"]: -1 for row in rows}
        return await original_scores(query, rows, **kwargs)

    core.memory.index.scores_async = scores

    number = 0

    async def source(ws):
        nonlocal number
        number += 1
        await ws.send(json.dumps(raw_event(number, message=[
            {"type": "text", "data": {"text": QUESTION}},
            image_segment("https://gchat.qpic.cn/red.png"),
            image_segment("https://gchat.qpic.cn/blue.png"),
        ])))
        await ws.wait_closed()

    async def resolve(host, port):
        return ["1.1.1.1"]

    def cdn(request):
        downloads.append(request)
        return httpx.Response(
            200, content=originals[len(downloads) - 1], headers={"Content-Type": "image/png"},
        )

    def model(request):
        payload = json.loads(request.content)
        calls.append(payload)
        if payload["messages"][0]["role"] == "user":
            content = CUES
        elif payload.get("response_format"):
            review = json.loads(payload["messages"][-1]["content"])
            content = json.dumps({"relevant": [item["ref"] for item in review["memories"]]})
            if mutation == "unknown_review":
                raise httpx.ReadTimeout("synthetic review timeout", request=request)
            if mutation == "erased_current":
                event_id = core.store.one(
                    "SELECT owner_event_id FROM content_items",
                )["owner_event_id"]
                with core.store.transaction():
                    core.ledger.expire_events(core.store, [event_id], core.clock())
        else:
            content = reply or (
                "图1可见红色。按你以前明确提到的轻便需求，我更倾向图1；隔音仅凭图片无法确认。"
            )
        return httpx.Response(200, json={
            "choices": [{"message": {"content": content}}],
            "usage": {"prompt_tokens": 200, "completion_tokens": 30},
        })

    async def mutate_final_request(request):
        payload = json.loads(request.content)
        if payload["messages"][0]["role"] != "system" or not isinstance(
            payload["messages"][-1]["content"], list,
        ):
            return
        if mutation == "raw_hash":
            path = sorted(image_spool.directory(core.store).glob("*.raw"))[1]
            value = path.read_bytes()
            path.write_bytes(value[:-1] + bytes([value[-1] ^ 1]))

    async with (
        source_window(core, tmp_path, source, live=True, approved=True,
                      rich_input_scope="references_v1", scope_options=opts)
        as (window, client, protocol, _, sends),
        httpx.AsyncClient(transport=httpx.MockTransport(model),
                          event_hooks={"request": [mutate_final_request]}) as model_http,
    ):
        core.model = ModelClient(core.settings, model_http)
        core.content.options = core.settings.content
        await window.capture_once(client, protocol)
        try:
            await window.approve_captured(
                client, capture_approval(window), image_resolver=resolve,
                image_transport=httpx.MockTransport(cdn),
            )
        except httpx.HTTPStatusError as error:
            raise AssertionError(error.response.text) from error
        job = core.store.one(
            "SELECT j.* FROM jobs j JOIN content_items c ON c.owner_event_id=j.event_id LIMIT 1",
        )
        yield (
            window, client, protocol, sends, calls, downloads, queries, originals,
            memory_id, outsider, job,
        )
    core.memory.index.scores_async = original_scores


@pytest.mark.parametrize("no_candidates", [False, True])
def test_originals_and_image_topic_retrieval_share_paid_job_and_verified_reply(
    core, tmp_path, no_candidates,
):
    async def scenario():
        async with image_memory_window(core, tmp_path, no_candidates=no_candidates) as values:
            (window, client, protocol, sends, calls, downloads, queries, originals,
             memory_id, _, job) = values
            assert len(calls) == 1 and len(downloads) == 2
            reopened = Store(core.store.path)
            try:
                parts = qq_images.original_parts(reopened, core.settings, core.clock, job)
                assert [base64.b64decode(part["image_url"]["url"].split(",", 1)[1])
                        for part in parts] == originals
            finally:
                reopened.close()
            assert await core.run_once()
            assert len(calls) == (2 if no_candidates else 3), core.store.one(
                "SELECT last_error_code FROM jobs WHERE job_id=?", (job["job_id"],),
            )
            assert queries and all(CUES in query and ids == [memory_id] for query, ids in queries)
            assert "秘密偏好" not in encode(calls)
            final = calls[-1]["messages"]
            assert final[-1]["content"][0]["text"] == QUESTION
            assert [base64.b64decode(part["image_url"]["url"].split(",", 1)[1])
                    for part in final[-1]["content"][1:]] == originals
            assert (PREFERENCE in encode(final)) == (not no_candidates)
            if not no_candidates:
                review = json.loads(calls[1]["messages"][-1]["content"])
                assert review["image_observations"] == CUES and review["query"] == QUESTION
            scenarios = ["content_vision", *([] if no_candidates else ["memory_review"]), "chat"]
            assert core.store.rows("SELECT scenario,state FROM model_usage ORDER BY rowid") == [
                {"scenario": scenario, "state": "SETTLED"} for scenario in scenarios
            ]
            admissions = core.store.rows("SELECT * FROM model_admissions ORDER BY rowid")
            assert {row["logical_request_id"] for row in admissions} == {job["job_id"]}
            assert [row["input_bound"] for row in admissions] == [
                paid_budget.estimate_input_tokens(core.settings.paid_model, call["messages"], stage)
                for call, stage in zip(calls, scenarios, strict=True)
            ]
            assert list(image_spool.directory(core.store).glob("*.raw")) == []
            # Delivery still verifies hashes and paid sources after originals have been deleted.
            await window.send_once(client, protocol, reply_approval(window.plan, core))
            assert len(sends) == 1
            assert len(core.store.rows("SELECT * FROM memories")) == 2
            durable = encode(core.store.rows("SELECT body_json FROM events")) + encode(
                core.store.rows("SELECT value FROM runtime_meta"),
            )
            assert all(base64.b64encode(raw).decode() not in durable for raw in originals)

    run(scenario())


@pytest.mark.parametrize("limit", [14, 15])
def test_five_image_memory_inputs_obey_the_explicit_model_attempt_cap(core, tmp_path, limit):
    async def resolve(host, port):
        return ["1.1.1.1"]

    async def scenario():
        async with image_memory_window(core, tmp_path, model_attempts=limit) as values:
            window, client, protocol, sends, calls, _, _, originals, _, _, _ = values

            async def read_response(response):
                await response.aread()

            client.event_hooks["response"].append(read_response)

            def cdn(request):
                data = originals[0 if request.url.path.endswith("red.png") else 1]
                return httpx.Response(200, content=data, headers={"Content-Type": "image/png"})

            for number in range(1, 6):
                if number > 1:
                    await window.capture_once(client, protocol)
                    if number == 5 and limit == 14:
                        with pytest.raises(httpx.HTTPStatusError) as error:
                            await window.approve_captured(
                                client, capture_approval(window), image_resolver=resolve,
                                image_transport=httpx.MockTransport(cdn),
                            )
                        assert error.value.response.status_code == 429
                        assert "LIVE_IMAGE_TWO_CALLS_REQUIRED" in error.value.response.text
                        assert len(calls) == 12
                        break
                    await window.approve_captured(
                        client, capture_approval(window), image_resolver=resolve,
                        image_transport=httpx.MockTransport(cdn),
                    )
                assert await core.run_once()
                assert len(calls) == min(number * 3, limit)
                if number == 5:
                    job = core.store.one("SELECT * FROM jobs ORDER BY rowid DESC LIMIT 1")
                    with pytest.raises(CoreError, match="LIVE_TEXT_MODEL_ATTEMPT_CAP"):
                        budget.reserve(core.store, core.settings, job["job_id"], "chat",
                                       [{"role": "user", "content": "sixteenth attempt"}],
                                       core.clock(), clock=core.clock)
                    assert len(calls) == limit
                    assert core.store.one("SELECT COUNT(*) n FROM model_admissions")["n"] == limit
                await window.send_once(client, protocol, reply_approval(window.plan, core))
            assert len(sends) == (5 if limit == 15 else 4)
            assert window.status()["model_attempts"] == (15 if limit == 15 else 12)
            assert window.db.execute("SELECT COUNT(*) FROM window_inputs").fetchone()[0] == 5

    run(scenario())


@pytest.mark.parametrize("mutation", ["raw_hash", "expired", "erased_current", "unknown_review"])
def test_joint_image_memory_flow_stops_before_final_io_on_lost_evidence(core, tmp_path, mutation):
    async def scenario():
        async with image_memory_window(core, tmp_path, mutation=mutation) as values:
            _, _, _, sends, calls, _, _, _, _, _, job = values
            if mutation == "expired":
                core.clock.advance(image_spool.RETENTION_SECONDS + 1)
            assert await core.run_once()
            assert len(calls) <= 2
            assert not sends and not core.store.one("SELECT 1 FROM outbox")
            assert core.store.one(
                "SELECT state FROM jobs WHERE job_id=?", (job["job_id"],),
            )["state"] == ("CANCELLED" if mutation == "erased_current" else "FAILED")
            assert list(image_spool.directory(core.store).glob("*.raw")) == []
            assert not await core.run_once()

    run(scenario())
