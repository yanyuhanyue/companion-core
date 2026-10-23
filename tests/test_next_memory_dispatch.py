"""Memory validity fences the provider transport after asynchronous request hooks."""

import asyncio
import json
from datetime import UTC, datetime

import httpx
import pytest

from companion.core import Core
from companion.model import RECALL_RULES, ModelClient
from companion.store import after
from tests.conftest import ingest, run
from tests.test_m1 import remember
from tests.test_next_memory_integration import FACT, response
from tests.test_paid_model_gate import approved_live


@pytest.mark.parametrize("phase", ["review", "chat"])
@pytest.mark.parametrize("expiry", ["fact", "source"])
def test_expiry_during_request_hook_never_reaches_provider(core, tmp_path, phase, expiry):
    core.clock.now = datetime.now(UTC)
    memory_id = remember(core, 1, FACT, confirmed=True)
    core.store.execute("UPDATE jobs SET state='SUCCEEDED'")
    core.store.execute("UPDATE events SET memory_processed=1")
    if expiry == "fact":
        core.store.execute("UPDATE memories SET valid_to=? WHERE memory_id=?",
                           (after(core.clock(), 1), memory_id))
    else:
        retained_at = after(core.clock(), -86400 * core.settings.raw_event_retention_days + 1)
        core.store.execute("UPDATE events SET ingested_at=? WHERE event_id='evt_test_1'",
                           (retained_at,))
    approved_live(core, tmp_path)
    accepted = ingest(core, 2, "青禾项目下一步做什么？")
    calls, hooks = [], []

    async def hook(request):
        messages = json.loads(request.content)["messages"]
        stage = "review" if messages[0]["content"] == RECALL_RULES else "chat"
        hooks.append(stage)
        if stage == phase:
            await asyncio.sleep(0)
            core.clock.advance(2)

    def provider(request):
        messages = json.loads(request.content)["messages"]
        stage = "review" if messages[0]["content"] == RECALL_RULES else "chat"
        calls.append(stage)
        return response('{"relevant":["m0"]}' if stage == "review" else "下一步检查配色。")

    async def scenario():
        async with httpx.AsyncClient(transport=httpx.MockTransport(provider),
                                     event_hooks={"request": [hook]}) as client:
            with Core(core.settings, ModelClient(core.settings, client), core.clock) as worker:
                assert await worker.run_once()
    run(scenario())
    assert phase in hooks
    assert calls == ([] if phase == "review" else ["review"])
    assert core.store.one("SELECT state FROM jobs WHERE job_id=?", (accepted["job_id"],)) == {
        "state": "CANCELLED",
    }
    assert not core.store.rows("SELECT * FROM outbox")
    # The transport proves no handoff for this attempt; earlier received usage is retained.
    rows = core.store.rows("SELECT state,billed_micro_usd FROM model_usage ORDER BY rowid")
    assert all(row["state"] == "SETTLED" for row in rows)
    assert rows[-1]["billed_micro_usd"] == 0
    if phase == "chat":
        assert rows[0]["billed_micro_usd"] > 0
