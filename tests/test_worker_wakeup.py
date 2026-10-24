"""Cross-connection wake hints, native deadline clocks and cancellation ownership."""

import asyncio
from contextlib import closing
from datetime import datetime
from time import monotonic

import pytest

from companion import worker_wakeup
from companion.store import Store, after, utcnow
from companion.worker_loop import Recorder, project_public, snapshot
from companion.worker_wakeup import Maintenance, Wakeup, next_deadline
from tests.conftest import ingest, run


@pytest.mark.parametrize("kind", list(worker_wakeup.DEADLINES))
def test_each_worker_can_subscribe_scan_and_wake_on_foreign_commit(core, kind):
    async def scenario():
        with Wakeup(core.store, core.settings, kind, utcnow) as wake:
            wake.before_scan()
            pending = asyncio.create_task(wake.wait())
            await asyncio.sleep(0.04)
            with closing(Store(core.settings.database)) as other:
                other.execute("INSERT INTO runtime_meta VALUES ('foreign-write','1')")
            assert await asyncio.wait_for(pending, 2) == "commit"
    run(scenario())


@pytest.mark.parametrize("timing", ["after_subscribe", "after_snapshot", "after_scan"])
def test_commit_around_subscription_and_scan_is_not_lost(core, timing):
    async def scenario():
        with Wakeup(core.store, core.settings, "worker", utcnow) as wake:
            with closing(Store(core.settings.database)) as other:
                if timing == "after_subscribe":
                    other.execute("INSERT INTO runtime_meta VALUES ('race','1')")
                wake.before_scan()
                if timing == "after_subscribe":
                    assert core.store.one("SELECT value FROM runtime_meta WHERE key='race'")
                    return
                if timing == "after_snapshot":
                    other.execute("INSERT INTO runtime_meta VALUES ('race','1')")
                core.store.one("SELECT COUNT(*) FROM jobs")
                if timing == "after_scan":
                    other.execute("INSERT INTO runtime_meta VALUES ('race','1')")
            assert await asyncio.wait_for(wake.wait(), 1) == "commit"
    run(scenario())


def test_recorder_self_writes_do_not_wake_business_scan(core):
    async def scenario():
        with Wakeup(core.store, core.settings, "worker", utcnow) as wake:
            wake.before_scan()
            Recorder(core.store).completed(waiting=True)
            started = monotonic()
            assert await wake.wait(started + 0.15) == "maintenance"
            assert monotonic() - started >= 0.14
    run(scenario())


def test_due_retry_wakes_without_a_writer_and_native_claim_succeeds(core):
    core.clock = utcnow
    job = ingest(core)
    due = after(utcnow(), 0.15)
    core.store.execute("UPDATE jobs SET state='RETRY_WAIT',next_attempt_at=? WHERE job_id=?",
                       (due, job["job_id"]))

    async def scenario():
        with Wakeup(core.store, core.settings, "worker", utcnow) as wake:
            wake.before_scan()
            assert core.claim_job("too-early") is None
            assert await asyncio.wait_for(wake.wait(), 1) == "deadline"
            assert core.claim_job("now-due")["job_id"] == job["job_id"]
    run(scenario())


def test_missed_notification_is_reconciled_without_repeated_business_scans(core, monkeypatch):
    monkeypatch.setattr(worker_wakeup, "MAINTENANCE_SECONDS", 0.12)

    async def scenario():
        with Wakeup(core.store, core.settings, "executive-worker", utcnow) as wake:
            real = wake.watcher

            class Missed:
                def watch(self, *_):
                    # Simulate a backend losing hints while keeping native timeout semantics.
                    wake.stop.wait(0.12)
                    return "timeout"

                def close(self):
                    real.close()

            wake.watcher = Missed()
            wake.before_scan()
            pending = asyncio.create_task(wake.wait())
            await asyncio.sleep(0.02)
            with closing(Store(core.settings.database)) as other:
                other.execute("INSERT INTO runtime_meta VALUES ('missed','1')")
            assert await asyncio.wait_for(pending, 1) == "commit"
            assert wake.version_checks <= 4
    run(scenario())


def test_cancel_joins_watch_before_releasing_native_watcher(core):
    async def scenario():
        with Wakeup(core.store, core.settings, "worker", utcnow) as wake:
            watcher = wake.watcher
            wake.before_scan()
            pending = asyncio.create_task(wake.wait())
            await asyncio.sleep(0.03)
            pending.cancel()
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(pending, 1)
        with pytest.raises(RuntimeError):
            watcher.watch(50, 10, 1, None)
    run(scenario())


def test_wall_clock_rollback_rechecks_without_stale_deadline(core, monkeypatch):
    monkeypatch.setattr(worker_wakeup, "MAINTENANCE_SECONDS", 0.1)
    now = [utcnow()]

    async def scenario():
        with Wakeup(core.store, core.settings, "worker", lambda: now[0]) as wake:
            wake.before_scan()
            pending = asyncio.create_task(wake.wait())
            await asyncio.sleep(0.03)
            now[0] = after(now[0], -100)
            assert await asyncio.wait_for(pending, 1) == "clock"
    run(scenario())


def test_maintenance_is_elapsed_time_not_notification_count(monkeypatch):
    ticks = [10.0]
    monkeypatch.setattr(worker_wakeup, "monotonic", lambda: ticks[0])
    timer = Maintenance()
    assert timer.due()
    timer.completed()
    for _ in range(1000):
        assert not timer.due()
    ticks[0] += 60
    assert timer.due()


def test_wait_record_is_not_health_and_expires_under_strict_public_projection(core):
    recorder = Recorder(core.store)
    recorder.completed(waiting=True)
    now = datetime.fromisoformat(utcnow())
    fresh = snapshot(core.store, now)
    assert fresh["state"] == "WAITING" and fresh["stage"] == "WAITING_AFTER_POLL"
    assert project_public(fresh, now) == fresh
    stale = snapshot(core.store, datetime.fromisoformat(after(now.isoformat(), 11)))
    assert stale["state"] == "STALE"
    with pytest.raises(ValueError):
        project_public({**fresh, "state": "HEALTHY"}, now)


def test_all_deadline_queries_use_existing_schema(core):
    for kind in worker_wakeup.DEADLINES:
        assert next_deadline(core.store, core.settings, kind, datetime.fromisoformat(utcnow()))


@pytest.mark.parametrize("force_polling", ["true", "false"])
def test_watchfiles_supported_backend_selection_still_wakes_on_commit(
    core, monkeypatch, force_polling,
):
    if force_polling == "true":
        # Polling metadata can miss open-WAL writes on Windows. Exercise the bounded
        # authoritative reconciliation, using a shortened maintenance horizon.
        monkeypatch.setattr(worker_wakeup, "MAINTENANCE_SECONDS", 0.1)
    monkeypatch.setenv("WATCHFILES_FORCE_POLLING", force_polling)
    monkeypatch.setenv("WATCHFILES_POLL_DELAY_MS", "50")
    run_backend = test_each_worker_can_subscribe_scan_and_wake_on_foreign_commit
    run_backend(core, "content-worker")



def test_waiting_record_cannot_prove_activity_or_console_health(core):
    from companion import console_read, observe

    core.clock = utcnow
    Recorder(core.store).completed(waiting=True)
    value = console_read.overview(core)
    assert value["runtime"]["worker"]["state"] == "WAITING"
    assert value["runtime"]["state"] == "ATTENTION"
    activity = observe._LoopActivity().sample({
        "worker_loop": value["runtime"]["worker"], "observed_at": value["observed_at"],
    }, 0, [])
    assert activity["state"] == "UNPROVEN"
    assert activity["reasons"] == ["LOOP_WAITING_UNVERIFIED"]
