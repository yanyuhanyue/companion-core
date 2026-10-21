"""Content-free diagnostics. Never bootstrap, migrate, replay, or repair runtime state."""

import json
import shutil
import sqlite3
from contextlib import closing
from datetime import UTC, datetime
from functools import lru_cache
from pathlib import Path
from types import SimpleNamespace

from alembic.script import ScriptDirectory

from companion import __version__, budget
from companion.contracts import CoreError
from companion.privacy import ErasureLedger
from companion.store import Store


class _ReadView:
    def __init__(self, db):
        self.db = db

    def rows(self, sql, params=()):
        return [dict(row) for row in self.db.execute(sql, params)]

    def one(self, sql, params=()):
        row = self.db.execute(sql, params).fetchone()
        return dict(row) if row else None


@lru_cache(maxsize=1)
def schema_head():
    root = Path(__file__).resolve().parent
    migrations = root.parent / "migrations"
    if not migrations.is_dir():
        migrations = root / "migrations"
    return ScriptDirectory(str(migrations)).get_current_head()


def _resource(path):
    try:
        stat = Path(path).stat()
        return {"exists": True, "bytes": stat.st_size}
    except OSError:
        return {"exists": False, "bytes": None}


def _queue(view, table, active, now):
    states = {r["state"]: r["n"] for r in view.rows(
        f"SELECT state,COUNT(*) n FROM {table} GROUP BY state"
    )}
    marks = ",".join("?" for _ in active)
    oldest = view.one(f"SELECT MIN(created_at) at FROM {table} WHERE state IN ({marks})", active)
    age = None
    if oldest["at"]:
        age = max(0, (now - datetime.fromisoformat(oldest["at"])).total_seconds())
    expired = view.one(
        f"SELECT COUNT(*) n FROM {table} WHERE state IN ({marks}) AND lease_until IS NOT NULL "
        "AND julianday(lease_until)<=julianday(?)", (*active, now.isoformat()),
    )["n"]
    return {"states": states, "active": sum(states.get(s, 0) for s in active),
            "oldest_age_seconds": age, "expired_leases": expired}


def _ledger(view, settings, result, integrity):
    resource = _resource(settings.erasure_ledger)
    result["resources"]["erasure_ledger"] = resource
    identity = view.one("SELECT value FROM runtime_meta WHERE key='ledger_id'")
    if not identity:
        result["blockers"].append("ERASURE_IDENTITY_UNINITIALIZED")
        return
    try:
        with Path(settings.erasure_ledger).open("rb") as stream:
            header = json.loads(stream.readline(4096))
        if not isinstance(header, dict) or header.get("ledger_id") != identity["value"]:
            raise ValueError("identity")
        resource["checked"] = "HEADER_ONLY"
    except (OSError, ValueError, TypeError):
        result["blockers"].append("ERASURE_LEDGER_RECONCILIATION_REQUIRED")
        return
    if view.one("SELECT COUNT(*) n FROM privacy_erasure_ledger WHERE state!='COMPLETE'")["n"]:
        result["blockers"].append("ERASURE_REPLAY_REQUIRED")
    if not integrity:
        return
    if not resource["exists"] or resource["bytes"] > 4 * 1024 * 1024:
        result["blockers"].append("ERASURE_LEDGER_UNAVAILABLE_OR_TOO_LARGE")
        return
    try:
        records = ErasureLedger(settings.erasure_ledger).read()
        if records[0]["ledger_id"] != identity["value"]:
            raise CoreError("ERASURE_LEDGER_MISMATCH", 503)
        applied = {r["erasure_id"]: r for r in view.rows(
            "SELECT erasure_id,restore_min_replay_version,state FROM privacy_erasure_ledger"
        )}
        if len(applied) != len(records) - 1 or any(
            not (row := applied.get(record["erasure_id"])) or row["state"] != "COMPLETE"
            or row["restore_min_replay_version"] != record["seq"]
            for record in records[1:]
        ):
            raise CoreError("ERASURE_REPLAY_REQUIRED", 503)
        resource["records"] = len(records) - 1
        resource["checked"] = "FULL_LEDGER"
    except (CoreError, KeyError, TypeError):
        result["blockers"].append("ERASURE_LEDGER_RECONCILIATION_REQUIRED")


def _budget_journal(view, settings, result, integrity):
    identity = view.one("SELECT value FROM runtime_meta WHERE key='model_budget_journal_id'")
    paths = {r["journal_path"] for r in view.rows(
        "SELECT DISTINCT journal_path FROM model_admissions"
    )}
    if settings.paid_model:
        paths.add(settings.paid_model.journal_path)
    if not paths and not identity:
        result["resources"]["budget_journal"] = {"required": False, "checked": False}
        return
    resource = {"required": True, "checked": False}
    result["resources"]["budget_journal"] = resource
    if not identity or len(paths) != 1:
        result["blockers"].append("BUDGET_JOURNAL_RECONCILIATION_REQUIRED")
        return
    path = paths.pop()
    resource.update(_resource(path))
    if not resource["exists"]:
        result["blockers"].append("BUDGET_JOURNAL_UNAVAILABLE_OR_TOO_LARGE")
        return
    try:
        with Path(path).open("rb") as stream:
            header = json.loads(stream.readline(4096))
        if not isinstance(header, dict) or header.get("journal_id") != identity["value"]:
            raise ValueError("identity")
        resource["checked"] = "HEADER_ONLY"
    except (OSError, ValueError, TypeError):
        result["blockers"].append("BUDGET_JOURNAL_RECONCILIATION_REQUIRED")
        return
    if not integrity:
        return
    if resource["bytes"] > 4 * 1024 * 1024:
        result["blockers"].append("BUDGET_JOURNAL_UNAVAILABLE_OR_TOO_LARGE")
        return
    try:
        # Identity is already present in this read transaction. journal(record=None)
        # only checks; it cannot take its absent-identity initialization branch.
        from companion.paid_budget import journal

        journal(view, SimpleNamespace(journal_path=path))
        resource["checked"] = "FULL_JOURNAL"
    except CoreError:
        result["blockers"].append("BUDGET_JOURNAL_RECONCILIATION_REQUIRED")


def snapshot(settings, *, integrity=False):
    """One bounded SQLite read transaction, including committed WAL; no Core/Store init.

    mode=ro/query_only prohibit logical writes. SQLite may need existing writable
    WAL shared-memory coordination; if unavailable, report failure, never immutable.
    """
    now = datetime.now(UTC)
    path = Path(settings.database).resolve()
    result = {"schema_version": 1, "version": __version__, "mode": settings.mode,
              "observed_at": now.isoformat(), "read_only": True, "blockers": [],
              "worker_progress": {"state": "UNOBSERVED", "reason": "NO_WORKER_HEARTBEAT"},
              "database": _resource(path), "resources": {}}
    result["database"]["wal_bytes"] = _resource(str(path) + "-wal")["bytes"]
    try:
        result["resources"]["disk_free_bytes"] = shutil.disk_usage(path.parent).free
    except OSError:
        result["resources"]["disk_free_bytes"] = None
        result["blockers"].append("DISK_RESOURCE_UNAVAILABLE")
    if not path.is_file():
        result["blockers"].append("DATABASE_MISSING")
        return result
    try:
        with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=1)) as db:
            db.row_factory = sqlite3.Row
            db.execute("PRAGMA query_only=ON")
            # Do not turn an unexpectedly huge/corrupt DB into unbounded health work.
            remaining = 2_000

            def progress():
                nonlocal remaining
                remaining -= 1
                return remaining <= 0

            db.set_progress_handler(progress, 10_000)
            db.execute("BEGIN")
            view = _ReadView(db)
            if not view.one("SELECT 1 FROM sqlite_master WHERE name='alembic_version'"):
                result["blockers"].append("MIGRATION_REQUIRED")
                return result
            version = view.one("SELECT version_num FROM alembic_version")
            result["database"]["schema"] = version["version_num"] if version else None
            result["database"]["expected_schema"] = schema_head()
            # Reuse the existing pure capability query without invoking Store.__init__.
            capable = Store.schema_capabilities(view)
            result["database"]["capabilities_present"] = capable
            if not version or not capable:
                result["blockers"].append("MIGRATION_REQUIRED")
                return result
            result["database"]["integrity"] = "NOT_CHECKED"
            if integrity:
                ok = db.execute("PRAGMA quick_check(1)").fetchone()[0] == "ok"
                result["database"]["integrity"] = "OK" if ok else "FAILED"
                if not ok:
                    result["blockers"].append("DATABASE_INTEGRITY_FAILED")
            owner = view.one("SELECT value FROM runtime_meta WHERE key='owner_qq_id'")
            if not owner or owner["value"] != settings.owner_qq_id:
                result["blockers"].append("OWNER_IDENTITY_UNINITIALIZED_OR_MISMATCH")
            _ledger(view, settings, result, integrity)
            _budget_journal(view, settings, result, integrity)
            result["jobs"] = _queue(view, "jobs", ("QUEUED", "RUNNING", "RETRY_WAIT"), now)
            result["deliveries"] = _queue(view, "outbox", (
                "PENDING", "LEASED", "SENDING", "RETRY_WAIT", "DELIVERY_UNCERTAIN",
            ), now)
            result["budget"] = view.one(
                "SELECT COUNT(*) unsettled_calls,COALESCE(SUM(billed_micro_usd),0) "
                "unsettled_micro_usd,COALESCE(SUM(state='UNKNOWN'),0) unknown_calls "
                "FROM model_usage WHERE state IN ('UNKNOWN','RESERVED')"
            )
            result["budget"]["pricing_configured"] = settings.pricing_known
            result["budget"]["paid_approval_configured"] = settings.paid_model is not None
            result["budget"]["calibration"] = budget.usage_diagnostics(db)
            if settings.mode == "live" and settings.paid_model is None:
                result["blockers"].append("PAID_APPROVAL_REQUIRED")
            for key in (
                "model_budget_blocked", "model_budget_restore_required", "live_text_stopped",
            ):
                if view.one("SELECT 1 FROM runtime_meta WHERE key=?", (key,)):
                    result["blockers"].append(key.upper())
            if result["budget"]["unsettled_calls"]:
                result["blockers"].append("MODEL_USAGE_UNSETTLED")
            if result["deliveries"]["states"].get("DELIVERY_UNCERTAIN"):
                result["blockers"].append("DELIVERY_UNCERTAIN")
            if result["jobs"]["expired_leases"] or result["deliveries"]["expired_leases"]:
                result["blockers"].append("EXPIRED_LEASES")
            result["resources"]["embedding_cache"] = {
                "required": settings.memory_enabled,
                "exists": Path(settings.embedding_cache_dir).is_dir(),
                "model_loaded": False,
            }
            if settings.memory_enabled and not Path(settings.embedding_cache_dir).is_dir():
                result["blockers"].append("EMBEDDING_CACHE_MISSING")
            db.rollback()
    except (sqlite3.Error, OSError, ValueError, TypeError):
        result["blockers"].append("DATABASE_SNAPSHOT_UNAVAILABLE")
    return result
