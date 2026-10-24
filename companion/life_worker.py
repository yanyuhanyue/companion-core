"""Independent low-frequency worker for v0.5 background coordination."""

import httpx

from companion import budget, live_text, private_life
from companion.contracts import CoreError
from companion.core import Core
from companion.model import ModelClient
from companion.store import uid
from companion.worker_wakeup import Maintenance, Wakeup


async def life_worker(settings):
    async with httpx.AsyncClient(timeout=settings.timeout_seconds, trust_env=False) as client:
        core = Core(settings, ModelClient(settings, client))
        worker_id = uid("life_worker")
        try:
            maintenance = Maintenance()
            force_maintenance = False
            with Wakeup(core.store, settings, "life-worker", core.clock) as wake:
                while True:
                    wake.before_scan()
                    if private_life.enabled(settings):
                        try:
                            live_text.guard(core.store, settings, core.clock)
                        except CoreError:
                            await wake.wait()
                            continue
                        if private_life.busy(core.store, core.clock()):
                            await wake.wait()
                            continue
                    due = maintenance.due() or force_maintenance
                    if due:
                        core.expire_raw_events()
                        core.consolidation.schedule_due()
                        core.exploration.schedule_wake(core.wake.evaluate())
                        with core.store.transaction():
                            core.goals.expire_commitments()
                        core.exploration.schedule_goal()
                        core.social.flush()
                        maintenance.completed()
                    force_maintenance = False
                    worked = False
                    if not budget.chat_pending(core.store, core.clock()):
                        worked = await core.consolidation.run_once(worker_id)
                        if not worked and due and not budget.chat_pending(core.store, core.clock()):
                            worked = await core.extract_once()
                    if not worked:
                        force_maintenance = await wake.wait(maintenance.next_at) == "deadline"
        finally:
            core.store.close()
