"""Independent low-frequency worker for v0.5 background coordination."""

from functools import partial

import httpx

from companion import budget, live_text, private_life
from companion.contracts import CoreError
from companion.core import Core
from companion.model import ModelClient
from companion.store import uid
from companion.worker_schedule import UnitResult, run_native_unit
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
                    scan = wake.before_scan()
                    if private_life.enabled(settings):
                        try:
                            live_text.guard(core.store, settings, core.clock)
                        except CoreError:
                            await wake.wait(scan, UnitResult(False))
                            continue
                        if private_life.busy(core.store, core.clock()):
                            await wake.wait(scan, UnitResult(False))
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
                        core.life_topics.schedule()
                        maintenance.completed()
                    force_maintenance = False
                    result = UnitResult(False)
                    if not budget.chat_pending(core.store, core.clock()):
                        pending = core.store.one(
                            "SELECT j.job_id FROM jobs j JOIN life_activity_results r USING(job_id) "
                            "WHERE j.state IN ('QUEUED','RETRY_WAIT','RUNNING') AND r.state='PENDING' "
                            "ORDER BY j.created_at LIMIT 1",
                        )
                        if pending:
                            result = await run_native_unit(
                                partial(core.run_once, worker_id, job_id=pending["job_id"]),
                            )
                            if result.progressed and settings.life_loop.read_goals_enabled:
                                core.exploration.schedule_goal()
                        if not result.progressed:
                            result = await run_native_unit(core.consolidation.run_once, worker_id)
                        if (not result.progressed and due
                                and not budget.chat_pending(core.store, core.clock())):
                            result = await run_native_unit(core.extract_once)
                    if not result.progressed:
                        force_maintenance = (
                            await wake.wait(scan, result, maintenance.next_at) == "deadline"
                        )
        finally:
            core.store.close()
