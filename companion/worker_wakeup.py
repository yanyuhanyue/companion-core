"""Filesystem hints wake native SQLite workers; persisted queues remain authoritative."""

import asyncio
import math
import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path
from time import monotonic
from zoneinfo import ZoneInfo

from watchfiles._rust_notify import RustNotify
from watchfiles.main import _default_force_polling, _default_poll_delay_ms

# Existing once-per-minute scheduling/retention cadence, measured in elapsed time.
MAINTENANCE_SECONDS = 60

# Only future eligibility/expiry boundaries: already-due but fenced work must not spin.
DEADLINES = {
    "worker": [
        ("jobs", "next_attempt_at", "state IN ('QUEUED','RETRY_WAIT')"),
        ("jobs", "lease_until", "state='RUNNING'"),
        ("outbox", "lease_until", "state IN ('LEASED','SENDING')"),
        ("social_autonomy_plans", "expires_at", "state IN ('READY','PLANNING')"),
    ],
    "content-worker": [
        ("content_items", "lease_until", "state IN ('FETCHING','RUNNING')"),
        ("content_items", "deadline", "state IN ('WAITING','QUEUED','FETCHING','RUNNING')"),
    ],
    "executive-worker": [("tool_tasks", "lease_until", "state IN ('LEASED','RUNNING')")],
    "diary-worker": [
        ("diary_runs", "next_attempt_at", "state IN ('PENDING','RETRY_WAIT')"),
        ("diary_runs", "lease_until", "state='RUNNING'"),
    ],
    "life-worker": [
        ("consolidation_runs", "next_attempt_at", "state='PARTIAL'"),
        ("consolidation_runs", "lease_until", "state='LEASED'"),
        ("reflection_runs", "lease_until", "state='RUNNING'"),
        ("goal_steps", "lease_until", "state='RUNNING'"),
        ("goals", "next_due_at", "state IN ('ACCEPTED','QUEUED','RETRY_WAIT')"),
        ("commitments", "deadline", "state IN ('ACKNOWLEDGED','IN_PROGRESS')"),
    ],
}


class Maintenance:
    def __init__(self):
        self.next_at = monotonic()

    def due(self):
        return monotonic() >= self.next_at

    def completed(self):
        self.next_at = monotonic() + MAINTENANCE_SECONDS


def next_deadline(store, settings, kind, now):
    parts = [f"SELECT julianday({field}) AS at FROM {table} WHERE {condition}"
             for table, field, condition in DEADLINES[kind]]
    parts.append("SELECT julianday(a.lease_until) AS at FROM model_admissions a "
                 "JOIN model_usage m USING(request_id) WHERE m.state='RESERVED'")
    row = store.one("SELECT MIN(at) AS at FROM (" + " UNION ALL ".join(parts)
                    + ") WHERE at>=julianday(?)", (now.isoformat(),))
    dates = ([datetime.fromtimestamp((row["at"] - 2440587.5) * 86400 + 0.002, UTC)]
             if row["at"] else [])
    # Daily native schedulers and guard expiration do not require another writer.
    local = now.astimezone(ZoneInfo(settings.timezone))
    dates.append((local + timedelta(days=1)).replace(
        hour=0, minute=0, second=0, microsecond=0).astimezone(UTC))
    hour = (settings.diary_generate_hour if kind == "diary-worker" else
            settings.consolidation_local_hour if kind == "life-worker" else None)
    if hour is not None:
        local = now.astimezone(ZoneInfo(settings.timezone))
        due = local.replace(hour=hour, minute=0, second=0, microsecond=0)
        if due <= local:
            due += timedelta(days=1)
        dates.append(due.astimezone(UTC))
    if settings.live_text_window:
        end = settings.live_text_window.ends_at
        if end > now:
            dates.append(end)
    return min(dates) if dates else None


class Wakeup:
    """Subscribe synchronously, snapshot on the SAME Store, scan, then check before waiting.

    The documented Rust backend provides a subscription barrier and explicit close. Its
    version-pinned defaults retain watchfiles' environment/WSL polling fallback behavior.
    Cancellation joins the outstanding watch call before releasing the native watcher.
    """

    def __init__(self, store, settings, kind, clock):
        if kind not in DEADLINES:
            raise ValueError("UNKNOWN_WORKER_KIND")
        self.store, self.settings, self.kind, self.clock = store, settings, kind, clock
        self.stop = threading.Event()
        self.watcher = None
        self.version = None
        self.notifications = self.version_checks = 0

    def __enter__(self):
        # Watch the parent: SQLite may create/delete/recreate its WAL beside the database.
        self.watcher = RustNotify([str(Path(self.store.path).resolve().parent)], False,
                                  _default_force_polling(None), _default_poll_delay_ms(300),
                                  False, False)
        return self

    def __exit__(self, *_):
        self.stop.set()
        if self.watcher is not None:
            self.watcher.close()

    def data_version(self):
        self.version_checks += 1
        return self.store.one("PRAGMA data_version")["data_version"]

    def before_scan(self):
        self.version = self.data_version()

    async def wait(self, maintenance_at=None):
        wall = datetime.fromisoformat(self.clock())
        deadline = next_deadline(self.store, self.settings, self.kind, wall)
        started = monotonic()
        # Reconcile potentially missed hints using the existing maintenance horizon;
        # a timeout with no commit/deadline does not run the business scan or Recorder.
        reconcile_at = started + MAINTENANCE_SECONDS
        while True:
            if self.data_version() != self.version:
                return "commit"
            now, mono = datetime.fromisoformat(self.clock()), monotonic()
            if deadline and now >= deadline:
                return "deadline"
            if maintenance_at is not None and mono >= maintenance_at:
                return "maintenance"
            if abs((now - wall).total_seconds() - (mono - started)) > 2:
                return "clock"
            if mono >= reconcile_at:
                reconcile_at = mono + MAINTENANCE_SECONDS
            delay = reconcile_at - mono
            if maintenance_at is not None:
                delay = min(delay, maintenance_at - mono)
            if deadline:
                delay = min(delay, (deadline - now).total_seconds())
            pending = asyncio.create_task(asyncio.to_thread(
                self.watcher.watch, 50, 50, max(1, math.ceil(delay * 1000)), self.stop,
            ))
            try:
                changes = await asyncio.shield(pending)
            except asyncio.CancelledError:
                self.stop.set()
                await pending
                raise
            if changes in ("stop", "signal"):
                raise asyncio.CancelledError
            if isinstance(changes, set):
                self.notifications += 1
            # Coalesced/self/unrelated filesystem events are only hints. Our own Store
            # commits do not change this connection's data_version; never compare peers.
