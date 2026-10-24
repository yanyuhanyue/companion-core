"""One in-process turn at a time over native queues; no scheduler persistence."""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from time import monotonic

from companion import budget, private_life, social_autonomy_runtime
from companion.contracts import CoreError
from companion.worker_schedule import UnitResult, run_native_unit
from companion.worker_wakeup import MAINTENANCE_SECONDS


@dataclass
class Lane:
    name: str
    run: Callable[[], Awaitable[UnitResult]]
    interval: float | None = None
    next_at: float = 0


@dataclass(frozen=True)
class Selection:
    lane: str | None
    result: UnitResult
    more: bool
    blocked: bool = False


class RoundRobin:
    """A finite idle sweep; every invoked native operation consumes a turn.

    A failed/fenced operation can have already used its one model attempt, so
    never call another operation within that turn. Only genuine progress starts
    a fresh sweep. Eligibility is checked again before every invocation.
    """

    def __init__(self, lanes: tuple[Lane, ...], can_start: Callable[[], bool]):
        self.lanes, self.can_start = lanes, can_start
        self.cursor = 0
        self.remaining = len(lanes)

    def resume(self):
        self.remaining = len(self.lanes)

    @property
    def next_maintenance(self):
        return min((lane.next_at for lane in self.lanes if lane.interval is not None),
                   default=None)

    async def step(self) -> Selection:
        if not self.can_start():
            self.remaining = 0
            return Selection(None, UnitResult(False), False, blocked=True)
        while self.remaining:
            lane = self.lanes[self.cursor]
            self.cursor = (self.cursor + 1) % len(self.lanes)
            self.remaining -= 1
            if lane.interval is not None and monotonic() < lane.next_at:
                continue
            result = await lane.run()
            if lane.interval is not None:
                lane.next_at = monotonic() + lane.interval
            if result.progressed:
                self.resume()
            return Selection(lane.name, result, bool(self.remaining))
        return Selection(None, UnitResult(False), False)


def foreground_clear(core):
    if budget.chat_pending(core.store, core.clock()):
        return False
    return private_life.ready(core.store, core.settings, core.clock)


def native_selector(core, worker_id) -> RoundRobin:
    """Bind only existing native operations; required single-step APIs fail closed.

    Core and domain owners provide the paid-admission/final-dispatch fences. This
    foreground check is a scheduling hint and grants no model or tool authority.
    """
    if core.settings.consolidation_enabled and not callable(
        getattr(core.consolidation, "run_step", None),
    ):
        raise CoreError("NATIVE_CONSOLIDATION_STEP_REQUIRED", 503)
    if core.settings.memory_enabled and not callable(getattr(core, "extract_step", None)):
        raise CoreError("NATIVE_MEMORY_STEP_REQUIRED", 503)

    def maintenance(name, operation):
        async def run():
            operation()
            # Completed a scheduled maintenance unit. Its next monotonic deadline
            # advances once; rescan to observe any work enqueued on our own Store.
            return UnitResult(True)

        return Lane(name, run, interval=MAINTENANCE_SECONDS)

    async def wake_goal():
        decisions = core.wake.evaluate()
        core.exploration.schedule_wake(decisions)
        return UnitResult(bool(decisions))

    async def schedule_goal():
        return UnitResult(bool(core.exploration.schedule_goal()))

    async def life_topic():
        return UnitResult(bool(core.life_topics.schedule()))

    async def private_share():
        return UnitResult(bool(core.social.flush()))

    async def activity():
        row = core.store.one(
            "SELECT j.job_id FROM jobs j JOIN events e USING(event_id) "
            "WHERE j.kind='activity' AND e.redacted=0 "
            "AND json_extract(e.body_json,'$.notice.kind') IN ('life','share','commitment') "
            "AND (j.state IN ('QUEUED','RETRY_WAIT') "
            "OR (j.state='RUNNING' AND j.lease_until<=?)) "
            "AND (j.next_attempt_at IS NULL OR j.next_attempt_at<=?) "
            "ORDER BY j.rowid LIMIT 1", (core.clock(), core.clock()),
        )
        if row is None:
            return UnitResult(False)

        async def execute():
            return await core.run_once(worker_id, job_id=row["job_id"])

        return await run_native_unit(execute)

    async def diary():
        return await run_native_unit(core.diary.generate_once, worker_id)

    async def consolidation():
        if not core.settings.consolidation_enabled:
            return UnitResult(False)
        return await core.consolidation.run_step(worker_id)

    async def memory():
        if not core.settings.memory_enabled:
            return UnitResult(False)
        return await core.extract_step()

    async def social():
        result = await social_autonomy_runtime.run_once(
            core.store, core.settings, core.model, core.clock, worker_id=worker_id,
        )
        return UnitResult(result)

    return RoundRobin((
        maintenance("retention", core.expire_raw_events),
        maintenance("diary_schedule", core.diary.schedule_due),
        maintenance("consolidation_schedule", core.consolidation.schedule_due),
        maintenance("commitment_expiry", core.goals.expire_commitments),
        Lane("wake", wake_goal, interval=MAINTENANCE_SECONDS),
        Lane("goal", schedule_goal),
        Lane("life_topic", life_topic),
        Lane("private_share", private_share),
        Lane("activity", activity),
        Lane("diary", diary),
        Lane("consolidation", consolidation),
        Lane("memory", memory),
        Lane("social", social),
    ), lambda: foreground_clear(core))
