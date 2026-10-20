import asyncio

import httpx

from companion.diary import DiaryService
from companion.model import ModelClient
from companion.runtime import Runtime
from companion.store import uid


async def diary_worker(settings):
    async with httpx.AsyncClient(timeout=settings.timeout_seconds, trust_env=False) as client:
        with Runtime(settings) as runtime:
            diary = DiaryService(
                runtime.store, settings, ModelClient(settings, client),
                runtime.ledger, runtime.clock,
            )
            runtime.store.execute(
                "UPDATE model_usage SET state='UNKNOWN' WHERE scenario='diary' AND state='RESERVED'"
            )
            worker_id = uid("diary_worker")
            ticks = 0
            while True:
                if ticks % 60 == 0:
                    runtime.expire_raw_events()
                    diary.schedule_due()
                worked = await diary.generate_once(worker_id)
                ticks += 1
                if not worked:
                    await asyncio.sleep(1)
