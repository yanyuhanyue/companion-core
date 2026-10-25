"""Finished tasks release ownership; idle discovery remembers only the latest input."""

import asyncio
import sqlite3
from contextlib import closing
from types import SimpleNamespace

import pytest

from companion_onebot_bridge.continuous import MemePump
from companion_onebot_bridge.pending_media import MediaPump
from tests.conftest import run


@pytest.mark.parametrize("cancelled", [False, True])
def test_completed_media_failure_is_raised_once_and_releases_task(tmp_path, cancelled):
    async def scenario(db):
        pump = MediaPump(SimpleNamespace(db=db), None)

        async def fail():
            if cancelled:
                raise asyncio.CancelledError()
            raise ValueError("SOURCE_CHECK_FAILED")

        task = pump.task = asyncio.create_task(fail())
        await asyncio.gather(task, return_exceptions=True)
        with pytest.raises(asyncio.CancelledError if cancelled else ValueError):
            pump.tick()
        assert pump.task is None
        pump.tick()  # The completed exception cannot monopolize subsequent reconciliation.

    with closing(sqlite3.connect(tmp_path / "pump.db")) as db:
        run(scenario(db))


def test_idle_meme_submission_is_once_for_latest_input_without_retaining_history():
    submitted = []

    class Runtime:
        def missing_offer(self, event_id):
            return {"offer_key": event_id}

        async def submit_missing(self, client, event_id, offer, *, reply_revision):
            submitted.append(event_id)
            return {"status": "ACCEPTED"}

        async def run_once(self, client):
            return {"status": "IDLE"}

    async def state(client, row):
        return {"reply_revision": 1}

    async def scenario():
        window = SimpleNamespace(plan=SimpleNamespace(config=SimpleNamespace(meme_enabled=True)),
                                 memes=Runtime(), input_state=state)
        pump = MemePump(window, None)
        for number in range(1000):
            row = {"event_id": "evt_" + str(number)}
            await pump.execute(row)
            await pump.execute(row)
        assert len(submitted) == len(set(submitted)) == 1000
        # This loop only visits the latest immutable window_inputs row. A growing
        # process cache must not become an alternative history or replay authority.
        assert not any(isinstance(value, set) and len(value) > 1
                       for value in vars(pump).values())
    run(scenario())
