"""A failed image input can retire only with settled, retained usage and zero sends."""

import asyncio
import json
from pathlib import Path

import httpx
import pytest

from companion import api
from companion.core import Core
from tests.conftest import run
from tests.test_animated_owner_images import animation
from tests.test_onebot_capture import capture_approval, source_window
from tests.test_onebot_direct import raw_event
from tests.test_onebot_images import image_segment
from tests.test_video_understanding import scope_options


@pytest.mark.parametrize("mutation", [
    None, "unknown", "reserved", "missing_admission", "admission_job", "fee_reduced",
    "media_lease", "forged_count", "forged_fee",
])
def test_real_api_retires_settled_failed_image_without_replay_or_refund(
    core, tmp_path, monkeypatch, mutation,
):
    calls, instances = [], []

    async def source(ws):
        await ws.send(json.dumps(raw_event(1, message=[image_segment()])))
        await ws.wait_closed()

    def construct(*args, **kwargs):
        instance = Core(*args, clock=core.clock, **kwargs)
        instances.append(instance)
        return instance

    def provider(request):
        calls.append(json.loads(request.content))
        return httpx.Response(200, json={
            "choices": [{"message": {"content": None, "tool_calls": [{
                "id": "call_invalid", "type": "function", "function": {
                    "name": "invalid_tool", "arguments": '{"question":"describe motion"}',
                },
            }]}}], "usage": {"prompt_tokens": 100, "completion_tokens": 20},
        })

    async def resolve(*_):
        return ["1.1.1.1"]

    async def scenario():
        monkeypatch.setattr(api, "Core", construct)
        monkeypatch.setattr(api, "AsyncClient", lambda **kwargs: httpx.AsyncClient(
            **kwargs, transport=httpx.MockTransport(provider)))
        async with source_window(
            core, tmp_path, source, live=True, approved=True,
            scope_options=scope_options(core, model="synthetic-video"),
            rich_input_scope="references_v1", independent_api=True,
        ) as (window, client, protocol, _, sends):
            await window.capture_once(client, protocol)
            with pytest.raises(httpx.HTTPStatusError):
                await window.approve_captured(
                    client, capture_approval(window), image_resolver=resolve,
                    image_transport=httpx.MockTransport(lambda _: httpx.Response(
                        200, content=animation(size=(3, 2)),
                        headers={"Content-Type": "image/gif"},
                    )),
                )
            await window.stop_live(client)
            core.clock.advance(61)
            assert len(instances) == 1 and instances[0].store is not core.store
            usage = core.store.one("SELECT * FROM model_usage")
            assert usage["state"] == "SETTLED" and usage["billed_micro_usd"] > 0
            assert core.store.one("SELECT state FROM jobs")["state"] == "CANCELLED"
            assert core.store.one("SELECT state FROM content_items")["state"] == "UNKNOWN"
            if mutation in {"unknown", "reserved"}:
                core.store.execute("UPDATE model_usage SET state=?", (mutation.upper(),))
            elif mutation == "missing_admission":
                core.store.execute("DELETE FROM model_admissions")
            elif mutation == "admission_job":
                core.store.execute("UPDATE model_admissions SET logical_request_id='other-job'")
            elif mutation == "fee_reduced":
                core.store.execute("UPDATE model_usage SET billed_micro_usd=0")
            elif mutation == "media_lease":
                core.store.execute("UPDATE content_items SET lease_until=?", (
                    core.settings.live_text_window.ends_at.isoformat(),
                ))
            elif mutation in {"forged_count", "forged_fee"}:
                original = window.core_post

                async def forged(*args):
                    result = await original(*args)
                    return {**result, **({"model_attempts": False} if mutation == "forged_count"
                                         else {"billed_micro_usd": -1})}

                window.core_post = forged
            before = core.store.rows("SELECT * FROM model_usage")
            admissions = core.store.rows("SELECT * FROM model_admissions")
            journal = Path(core.settings.paid_model.journal_path)
            independent_budget = await asyncio.to_thread(journal.read_bytes)
            head = window.row()["journal_head"]
            if mutation:
                with pytest.raises((ValueError, httpx.HTTPStatusError)):
                    await window.reconcile_cancelled_inputs(client)
                assert window.row()["journal_head"] == head
                assert window.db.execute("SELECT state FROM window_inputs").fetchone()[0] == (
                    "APPROVED"
                )
            else:
                result = await window.reconcile_cancelled_inputs(client)
                assert result["cancelled_inputs"] == 1 and result["new_inputs"] == 1
                assert result["state"] == "STOPPED"
                records, _ = window.read_journal()
                receipt = next(row["data"]["core_receipt"] for row in records
                               if row["kind"] == "INPUT_CANCELLED")
                assert receipt["model_attempts"] == 1 and receipt["send_attempts"] == 0
                assert receipt["billed_micro_usd"] == usage["billed_micro_usd"]
                assert len(receipt["settled_model_usage_sha256"]) == 64
                assert not any(row["kind"] == "ACK" for row in records)
                assert window.verify(active=False)["state"] == "STOPPED"
                assert (await window.reconcile_cancelled_inputs(client))["cancelled_inputs"] == 0
            assert core.store.rows("SELECT * FROM model_usage") == before
            assert core.store.rows("SELECT * FROM model_admissions") == admissions
            assert await asyncio.to_thread(journal.read_bytes) == independent_budget
            assert not core.store.rows("SELECT * FROM outbox")
            assert not await core.run_once()
            assert len(calls) == 1 and sends == []

    run(scenario())
