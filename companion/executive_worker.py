import asyncio

from companion.executive import Executive
from companion.runtime import Runtime


async def executive_worker(settings):
    with Runtime(settings) as runtime:
        executive = Executive(runtime.store, settings, runtime.clock)
        while True:
            if not await executive.run_one():
                await asyncio.sleep(1)
