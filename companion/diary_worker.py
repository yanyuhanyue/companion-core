import asyncio

import httpx

from companion import live_text, private_life
from companion.contracts import CoreError
from companion.diary import DiaryService
from companion.model import ModelClient
from companion.runtime import Runtime
from companion.store import uid


async def diary_worker(settings):
    async with httpx.AsyncClient(timeout=settings.timeout_seconds, trust_env=False) as client:
        with Runtime(settings) as runtime:
            diary = DiaryService(
                runtime.store,
                settings,
                ModelClient(settings, client),
                runtime.ledger,
                runtime.clock,
            )
            runtime.store.execute(
                "UPDATE model_usage SET state='UNKNOWN' WHERE scenario='diary' AND state='RESERVED'"
                + (" AND request_id IN (SELECT request_id FROM model_admissions "
                   "WHERE lease_until<=?)"
                   if private_life.enabled(settings) else ""),
                (runtime.clock(),) if private_life.enabled(settings) else (),
            )
            worker_id = uid("diary_worker")
            ticks = 0
            while True:
                if private_life.enabled(settings):
                    try:
                        live_text.guard(runtime.store, settings, runtime.clock)
                    except CoreError:
                        await asyncio.sleep(1)
                        continue
                    if private_life.busy(runtime.store, runtime.clock()):
                        await asyncio.sleep(1)
                        continue
                if ticks % 60 == 0:
                    runtime.expire_raw_events()
                    diary.schedule_due()
                worked = await diary.generate_once(worker_id)
                ticks += 1
                if not worked:
                    await asyncio.sleep(1)
