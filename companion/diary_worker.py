import asyncio

import httpx

from companion.core import Core
from companion.model import ModelClient
from companion.store import uid


async def diary_worker(settings):
    async with httpx.AsyncClient(timeout=settings.timeout_seconds, trust_env=False) as client:
        core = Core(settings, ModelClient(settings, client))
        core.store.execute(
            "UPDATE model_usage SET state='UNKNOWN' WHERE scenario='diary' AND state='RESERVED'"
        )
        worker_id = uid("diary_worker")
        try:
            ticks = 0
            while True:
                if ticks % 60 == 0:
                    core.expire_raw_events()
                    core.diary.schedule_due()
                worked = await core.diary.generate_once(worker_id)
                ticks += 1
                if not worked:
                    await asyncio.sleep(1)
        finally:
            core.store.close()
