"""One recoverable coordinator per local day, reusing Memory and Diary state machines."""

import json
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from companion.contracts import CoreError
from companion.diary import day_bounds, source_hash
from companion.privacy import epoch
from companion.store import after, encode, uid

CONFIG_VERSION = "v05-1"


def erase_consolidation(store, event_ids):
    if not store.one(
        "SELECT 1 FROM sqlite_master WHERE name='consolidation_runs' AND type='table'"
    ):
        return
    for event_id in event_ids:
        store.execute(
            "UPDATE consolidation_runs SET state='CANCELLED',lease_token=NULL,steps_json='{}',"
            "source_set_sha256='',last_error_code='PRIVACY_CHANGED' WHERE run_id IN (SELECT "
            "run_id FROM consolidation_evidence WHERE event_id=?)",
            (event_id,),
        )
        store.execute("DELETE FROM consolidation_evidence WHERE event_id=?", (event_id,))
    store.execute(
        "UPDATE consolidation_runs SET state='CANCELLED',lease_token=NULL,"
        "last_error_code='PRIVACY_CHANGED' WHERE state IN ('LEASED','PARTIAL')"
    )


class NightlyConsolidator:
    def __init__(self, core):
        self.core, self.store, self.settings, self.clock = (
            core,
            core.store,
            core.settings,
            core.clock,
        )

    def enabled(self):
        paused = self.store.one("SELECT value FROM runtime_meta WHERE key='life_paused'")
        return self.settings.consolidation_enabled and not (paused and paused["value"] == "1")

    def schedule_day(self, day):
        if not self.enabled():
            return None
        self.core.reflections.validate_day(day)
        with self.store.transaction():
            existing = self.status(day)
            if existing:
                return existing["run_id"]
            run_id, now = uid("nightly"), self.clock()
            self.store.insert(
                "consolidation_runs",
                run_id=run_id,
                identity_id="companion",
                local_date=day.isoformat(),
                timezone=self.settings.timezone,
                config_version=CONFIG_VERSION,
                state="SCHEDULED",
                steps_json="{}",
                captured_privacy_epoch=epoch(self.store),
                created_at=now,
                updated_at=now,
            )
            return run_id

    def schedule_due(self):
        if not self.enabled():
            return []
        local = datetime.fromisoformat(self.clock()).astimezone(ZoneInfo(self.settings.timezone))
        latest = local.date() - timedelta(
            days=1 if local.hour >= self.settings.consolidation_local_hour else 2
        )
        return [
            self.schedule_day(local.date() - timedelta(days=offset))
            for offset in reversed(range(1, self.settings.consolidation_catchup_days + 1))
            if local.date() - timedelta(days=offset) <= latest
        ]

    def status(self, day):
        return self.store.one(
            "SELECT * FROM consolidation_runs WHERE local_date=? AND timezone=? AND "
            "config_version=?",
            (day.isoformat(), self.settings.timezone, CONFIG_VERSION),
        )

    def recent(self):
        return self.store.rows(
            "SELECT local_date,state,steps_json,last_error_code FROM consolidation_runs ORDER "
            "BY local_date DESC LIMIT 7"
        )

    def claim(self):
        s, now = self.store, self.clock()
        with s.transaction():
            if not self.enabled():
                s.execute(
                    "UPDATE consolidation_runs SET state='CANCELLED',lease_token=NULL,"
                    "last_error_code='CONSOLIDATION_PAUSED' WHERE state IN ('SCHEDULED',"
                    "'LEASED','PARTIAL')"
                )
                return None
            row = s.one(
                "SELECT * FROM consolidation_runs WHERE timezone=? AND (state='SCHEDULED' OR "
                "(state='PARTIAL' AND next_attempt_at<=?) OR (state='LEASED' AND "
                "lease_until<=?)) ORDER BY local_date LIMIT 1",
                (self.settings.timezone, now, now),
            )
            if not row:
                return None
            sources = self.core.diary.repo.sources(date.fromisoformat(row["local_date"]))
            # Erasure cancels leased/partial coordinators in the same DB transaction.
            # Benign Memory writes may advance the epoch before a crashed coordinator
            # checkpoints. Resume against fresh state only when its sources are unchanged;
            # each generative subtask separately rejects its own old model result.
            if row["state"] != "SCHEDULED" and row["source_set_sha256"] != source_hash(sources):
                s.execute(
                    "UPDATE consolidation_runs SET state='CANCELLED',lease_token=NULL,"
                    "last_error_code='CONSOLIDATION_SOURCES_CHANGED' WHERE run_id=?",
                    (row["run_id"],),
                )
                return None
            token = uid("nightly_lease")
            s.execute(
                "UPDATE consolidation_runs SET state='LEASED',lease_token=?,lease_until=?,"
                "source_set_sha256=?,captured_privacy_epoch=?,updated_at=? WHERE run_id=?",
                (
                    token,
                    after(now, self.settings.lease_seconds),
                    source_hash(sources),
                    epoch(s),
                    now,
                    row["run_id"],
                ),
            )
            for source_id in {source["event_id"] for source in sources}:
                s.execute(
                    "INSERT OR IGNORE INTO consolidation_evidence VALUES (?,?)",
                    (row["run_id"], source_id),
                )
            return {
                **row,
                "lease_token": token,
                "captured_privacy_epoch": epoch(s),
                "sources": sources,
                "steps": json.loads(row["steps_json"]),
                "erasure_version": s.one(
                    "SELECT COALESCE(MAX(restore_min_replay_version),0) AS n "
                    "FROM privacy_erasure_ledger"
                )["n"],
            }

    def after_memory_commit(self, run):
        # Memory.add advances the context epoch. Accept our own successful write only
        # if no erasure intervened and the exact collected source set is still intact.
        with self.store.transaction():
            current = self.store.one(
                "SELECT * FROM consolidation_runs WHERE run_id=?", (run["run_id"],)
            )
            erasure_version = self.store.one(
                "SELECT COALESCE(MAX(restore_min_replay_version),0) AS n "
                "FROM privacy_erasure_ledger"
            )["n"]
            if (
                not current
                or current["state"] != "LEASED"
                or current["lease_token"] != run["lease_token"]
                or current["lease_until"] <= self.clock()
            ):
                raise CoreError("CONSOLIDATION_STALE_LEASE", 409)
            if erasure_version != run["erasure_version"] or source_hash(
                run["sources"]
            ) != source_hash(self.core.diary.repo.sources(date.fromisoformat(run["local_date"]))):
                raise CoreError("PRIVACY_CHANGED", 409)
            run["captured_privacy_epoch"] = epoch(self.store)
            self.store.execute(
                "UPDATE consolidation_runs SET captured_privacy_epoch=? WHERE run_id=?",
                (run["captured_privacy_epoch"], run["run_id"]),
            )

    def checkpoint(self, run, state="LEASED", code=None, waiting=False):
        s, now = self.store, self.clock()
        with s.transaction():
            current = s.one("SELECT * FROM consolidation_runs WHERE run_id=?", (run["run_id"],))
            if (
                not current
                or current["state"] != "LEASED"
                or current["lease_token"] != run["lease_token"]
                or current["lease_until"] <= now
            ):
                raise CoreError("CONSOLIDATION_STALE_LEASE", 409)
            if not self.enabled() or epoch(s) != run["captured_privacy_epoch"]:
                raise CoreError("PRIVACY_CHANGED", 409)
            s.execute(
                "UPDATE consolidation_runs SET state=?,steps_json=?,lease_token=?,"
                "lease_until=?,next_attempt_at=?,last_error_code=?,updated_at=? WHERE run_id=?",
                (
                    state,
                    encode(run["steps"]),
                    run["lease_token"] if state == "LEASED" else None,
                    after(now, self.settings.lease_seconds),
                    after(now, 60) if waiting else None,
                    code,
                    now,
                    run["run_id"],
                ),
            )

    async def run_once(self, worker_id="life-worker"):
        run = self.claim()
        if not run:
            return False
        day, steps = date.fromisoformat(run["local_date"]), run["steps"]
        try:
            if not run["sources"]:
                self.checkpoint(run, "SKIPPED", "NO_SOURCES")
                return True
            if steps.get("memory") not in {"COMPLETE", "SKIPPED"}:
                steps["memory"] = "RUNNING"
                self.checkpoint(run)
                worked = (
                    await self.core.extract_once(day) if self.settings.memory_enabled else False
                )
                if worked:
                    self.after_memory_commit(run)
                start, end = day_bounds(day, self.settings.timezone)
                remaining = self.store.one(
                    "SELECT COUNT(*) AS n FROM events e JOIN jobs j USING(event_id) WHERE "
                    "e.actor_id='owner' AND e.source='qq.onebot' AND e.redacted=0 AND "
                    "e.memory_processed=0 AND j.state='SUCCEEDED' AND e.occurred_at>=? AND "
                    "e.occurred_at<?",
                    (start, end),
                )["n"]
                steps["memory"] = (
                    "WAITING"
                    if self.settings.memory_enabled
                    and remaining >= self.settings.extraction_min_events
                    else "COMPLETE"
                    if worked
                    else "SKIPPED"
                )
                if steps["memory"] == "WAITING":
                    self.checkpoint(run, "PARTIAL", "MEMORY_PENDING", waiting=True)
                    return True
                self.checkpoint(run)
            if steps.get("diary") not in {"COMMITTED", "SKIPPED", "FAILED", "CANCELLED"}:
                steps["diary"] = "RUNNING"
                self.checkpoint(run)
                diary_id = self.core.diary.schedule_day(day)
                if not diary_id:
                    steps["diary"] = "SKIPPED"
                else:
                    row = self.store.one("SELECT * FROM diary_runs WHERE run_id=?", (diary_id,))
                    if row["state"] in {"PENDING", "RETRY_WAIT"} or (
                        row["state"] == "RUNNING" and row["lease_until"] <= self.clock()
                    ):
                        await self.core.diary.generate_once(worker_id)
                        row = self.store.one("SELECT * FROM diary_runs WHERE run_id=?", (diary_id,))
                    steps["diary"] = row["state"]
                    if row["state"] in {"PENDING", "RUNNING", "RETRY_WAIT"}:
                        self.checkpoint(run, "PARTIAL", "DIARY_PENDING", waiting=True)
                        return True
                self.checkpoint(run)
            if steps.get("reflection") not in {"COMMITTED", "SKIPPED", "FAILED", "CANCELLED"}:
                self.checkpoint(run)
                result = (
                    await self.core.reflections.run_day(day)
                    if self.settings.reflection_enabled
                    else None
                )
                steps["reflection"] = result["state"] if result else "SKIPPED"
                if steps["reflection"] == "RUNNING":
                    self.checkpoint(run, "PARTIAL", "REFLECTION_PENDING", waiting=True)
                    return True
            failed = any(value in {"FAILED", "CANCELLED"} for value in steps.values())
            state = (
                "PARTIAL"
                if failed
                else "SKIPPED"
                if all(value == "SKIPPED" for value in steps.values())
                else "COMPLETE"
            )
            self.checkpoint(run, state, "STEP_FAILED" if failed else None)
        except Exception as error:
            code = error.code if isinstance(error, CoreError) else "CONSOLIDATION_INTERNAL_ERROR"
            self.store.execute(
                "UPDATE consolidation_runs SET state=?,lease_token=NULL,last_error_code=?,"
                "updated_at=? WHERE run_id=? AND state='LEASED' AND lease_token=?",
                (
                    "CANCELLED" if code == "PRIVACY_CHANGED" else "FAILED",
                    code,
                    self.clock(),
                    run["run_id"],
                    run["lease_token"],
                ),
            )
        return True
