"""One in-process turn at a time over native queues; no scheduler persistence."""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from time import monotonic

from companion import budget, owner_authority, private_life, social_autonomy_runtime
from companion.contracts import CoreError
from companion.store import encode
from companion.wake import WAKE_PURPOSES
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
    if not owner_authority.required(core.store, core.settings):
        return not budget.chat_pending(core.store, core.clock())
    try:
        view = owner_authority.current(core.store, core.settings, core.clock)
    except CoreError:
        return False
    # This is a scheduling hint over the native signed input receipt, not a new
    # permission or input queue. Final admission remains owned by the domain.
    pending = owner_authority.owner_waiting(
        core.store, core.settings, core.clock(), authority=view.authority.as_dict(),
    )
    return not pending and not private_life.busy(core.store, core.clock())


def _allowed(core, purposes):
    if not owner_authority.required(core.store, core.settings):
        return True
    try:
        view = owner_authority.current(core.store, core.settings, core.clock)
    except CoreError:
        return False
    return set(purposes) <= set(view.policy.purposes)


def native_selector(core, worker_id) -> RoundRobin:
    """Bind only existing native operations; required single-step APIs fail closed.

    Core and domain owners provide the paid-admission/final-dispatch fences. This
    foreground check is a scheduling hint and grants no model or tool authority.
    """
    if core.settings.consolidation_enabled and _allowed(core, ("consolidation",)) and not callable(
        getattr(core.consolidation, "run_step", None),
    ):
        raise CoreError("NATIVE_CONSOLIDATION_STEP_REQUIRED", 503)
    if (core.settings.memory_enabled and _allowed(core, ("memory",))
        and not callable(getattr(core, "extract_step", None))):
        raise CoreError("NATIVE_MEMORY_STEP_REQUIRED", 503)

    def lane(name, operation, purposes=(), *, interval=None):
        async def run():
            if not _allowed(core, purposes):
                return UnitResult(False)
            return await operation()

        return Lane(name, run, interval=interval)

    def maintenance(name, operation, purposes=()):
        async def run():
            operation()
            # Completed a scheduled maintenance unit. Its next monotonic deadline
            # advances once; rescan to observe any work enqueued on our own Store.
            return UnitResult(True)

        return lane(name, run, purposes, interval=MAINTENANCE_SECONDS)

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
        kinds, authority = ["life", "share", "commitment"], None
        if owner_authority.required(core.store, core.settings):
            try:
                view = owner_authority.current(core.store, core.settings, core.clock)
            except CoreError:
                return UnitResult(False)
            authority = encode(view.authority.as_dict())
            kinds = [kind for kind, purpose in (
                ("life", "life"), ("share", "proactive"), ("commitment", "goals"),
            ) if purpose in view.policy.purposes]
        row = core.store.one(
            "SELECT j.* FROM jobs j JOIN events e USING(event_id) "
            "WHERE j.kind='activity' AND e.redacted=0 "
            "AND json_extract(e.body_json,'$.notice.kind') IN (SELECT value FROM json_each(?)) "
            "AND (? IS NULL OR json(j.authority_json)=json(?)) "
            "AND (j.state IN ('QUEUED','RETRY_WAIT') "
            "OR (j.state='RUNNING' AND j.lease_until<=?)) "
            "AND (j.next_attempt_at IS NULL OR j.next_attempt_at<=?) "
            "ORDER BY j.rowid LIMIT 1",
            (encode(kinds), authority, authority, core.clock(), core.clock()),
        )
        if row is None:
            return UnitResult(False)
        if authority is not None:
            try:
                owner_authority.check_job(core.store, core.settings, core.clock, row,
                    purpose=owner_authority.model_purpose(core.store, "activity", row["job_id"]))
            except CoreError:
                return UnitResult(False)

        async def execute():
            return await core.run_once(worker_id, job_id=row["job_id"])

        return await run_native_unit(execute)

    async def diary():
        return await run_native_unit(core.diary.generate_once, worker_id)

    async def consolidation():
        if not core.settings.consolidation_enabled:
            return UnitResult(False)
        operation = getattr(core.consolidation, "run_step", None)
        if not callable(operation):
            raise CoreError("NATIVE_CONSOLIDATION_STEP_REQUIRED", 503)
        return await operation(worker_id)

    async def memory():
        if not core.settings.memory_enabled:
            return UnitResult(False)
        operation = getattr(core, "extract_step", None)
        if not callable(operation):
            raise CoreError("NATIVE_MEMORY_STEP_REQUIRED", 503)
        return await operation()

    async def social():
        result = await social_autonomy_runtime.run_once(
            core.store, core.settings, core.model, core.clock, worker_id=worker_id,
        )
        return UnitResult(result)

    return RoundRobin((
        maintenance("retention", core.expire_raw_events),
        maintenance("diary_schedule", core.diary.schedule_due, ("diary",)),
        maintenance("consolidation_schedule", core.consolidation.schedule_due, ("consolidation",)),
        maintenance("commitment_expiry", core.goals.expire_commitments, ("goals",)),
        lane("wake", wake_goal, WAKE_PURPOSES, interval=MAINTENANCE_SECONDS),
        lane("goal", schedule_goal, ("goals", "exploration")),
        lane("life_topic", life_topic, ("life",)),
        lane("private_share", private_share, ("proactive",)),
        lane("activity", activity),
        lane("diary", diary, ("diary",)),
        lane("consolidation", consolidation, ("consolidation",)),
        lane("memory", memory, ("memory",)),
        lane("social", social, ("social",)),
    ), lambda: foreground_clear(core))
