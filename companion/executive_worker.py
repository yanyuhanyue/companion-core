from companion.executive import Executive
from companion.runtime import Runtime
from companion.worker_wakeup import Wakeup


async def executive_worker(settings):
    with Runtime(settings) as runtime:
        executive = Executive(runtime.store, settings, runtime.clock)
        with Wakeup(runtime.store, settings, "executive-worker", runtime.clock) as wake:
            while True:
                wake.before_scan()
                if not await executive.run_one():
                    await wake.wait()
