import asyncio

from companion.content import ContentService
from companion.observation import ObservationService
from companion.runtime import Runtime


async def content_worker(settings):
    with Runtime(settings) as runtime:
        observations = ObservationService(runtime.store, settings, runtime.clock)
        content = ContentService(runtime.store, settings, observations, clock=runtime.clock)
        while True:
            if not await content.run_batch():
                await asyncio.sleep(1)
