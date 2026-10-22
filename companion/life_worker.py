"""Independent low-frequency worker for v0.5 background coordination."""

import asyncio

import httpx

from companion import budget
from companion.core import Core
from companion.model import ModelClient
from companion.store import uid


async def life_worker(settings):
    async with httpx.AsyncClient(timeout=settings.timeout_seconds, trust_env=False) as client:
        core = Core(settings, ModelClient(settings, client))
        worker_id = uid("life_worker")
        try:
            ticks = 0
            while True:
                if ticks % 60 == 0:
                    core.expire_raw_events()
                    core.consolidation.schedule_due()
                    core.exploration.schedule_wake(core.wake.evaluate())
                    with core.store.transaction():
                        core.goals.expire_commitments()
                    core.exploration.schedule_goal()
                    core.social.flush()
                worked = False
                if not budget.chat_pending(core.store, core.clock()):
                    worked = await core.consolidation.run_once(worker_id)
                    if (not worked and ticks % 60 == 0
                        and not budget.chat_pending(core.store, core.clock())):
                        worked = await core.extract_once()
                ticks += 1
                if not worked:
                    await asyncio.sleep(1)
        finally:
            core.store.close()
