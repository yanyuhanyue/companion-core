import asyncio

from companion.core import Core


async def executive_worker(settings):
    core = Core(settings)
    try:
        while True:
            if not await core.executive.run_one():
                await asyncio.sleep(1)
    finally:
        core.store.close()
