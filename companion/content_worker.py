import asyncio

from companion.core import Core


async def content_worker(settings):
    core = Core(settings)
    try:
        while True:
            if not await core.content.run_once():
                await asyncio.sleep(1)
    finally:
        core.store.close()
