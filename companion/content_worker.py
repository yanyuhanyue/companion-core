from companion.content import ContentService, cleanup_images
from companion.observation import ObservationService
from companion.runtime import Runtime
from companion.worker_schedule import run_native_unit
from companion.worker_wakeup import Wakeup


async def content_worker(settings):
    with Runtime(settings) as runtime:
        cleanup_images(runtime.store, settings, runtime.clock)
        observations = ObservationService(runtime.store, settings, runtime.clock)
        content = ContentService(runtime.store, settings, observations, clock=runtime.clock)
        with Wakeup(runtime.store, settings, "content-worker", runtime.clock) as wake:
            while True:
                scan = wake.before_scan()
                result = await run_native_unit(content.run_batch)
                if not result.progressed:
                    await wake.wait(scan, result)
