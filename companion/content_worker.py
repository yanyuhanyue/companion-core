from companion.content import ContentService
from companion.observation import ObservationService
from companion.runtime import Runtime
from companion.worker_wakeup import Wakeup


async def content_worker(settings):
    with Runtime(settings) as runtime:
        observations = ObservationService(runtime.store, settings, runtime.clock)
        content = ContentService(runtime.store, settings, observations, clock=runtime.clock)
        with Wakeup(runtime.store, settings, "content-worker", runtime.clock) as wake:
            while True:
                wake.before_scan()
                if not await content.run_batch():
                    await wake.wait()
