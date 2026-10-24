"""Explicit migration of verified stopped legacy custody into the native Bridge."""

from dataclasses import replace

from . import journal

TABLES = {"identity", "inbox", "results", "test_window", "window_history", "window_inputs",
          "window_attempts", "window_delivery_resolutions", "automatic_received",
          "automatic_recalls", "automatic_media", "native_handoff"}


def _tables(db, names, references):
    hashes = [journal.digest(ref) for ref in references]
    placeholders = ",".join("?" for _ in hashes) or "NULL"
    result = {}
    for table in sorted(names):
        if table not in TABLES:
            raise ValueError("OWNER_LEGACY_CUSTODY_CHANGED")
        columns = [row[1] for row in db.execute(f"PRAGMA table_info({table})")]
        if not columns:
            raise ValueError("OWNER_LEGACY_CUSTODY_CHANGED")
        condition, values = "1=1", []
        if "plan_hash" in columns and hashes:
            condition, values = f"plan_hash NOT IN ({placeholders})", hashes
        elif table == "automatic_recalls":
            condition = "authority_json IS NULL"
        elif table in {"inbox", "results"}:
            source, key, identity = ("window_inputs", "event_id", "key") if table == "inbox" else (
                "window_attempts", "message_id", "message_id")
            condition = (f"{identity} NOT IN (SELECT {key} FROM {source} "
                         f"WHERE plan_hash IN ({placeholders}))")
            values = hashes
        rows = [list(row) for row in db.execute(f"SELECT * FROM {table} WHERE {condition}", values)]
        rows.sort(key=journal.digest)
        result[table] = {"columns": columns, "count": len(rows), "sha256": journal.digest(rows)}
    return result


def recall_schema(db):
    if not db.execute("SELECT 1 FROM sqlite_master WHERE name='automatic_recalls'").fetchone():
        return
    columns = [row[1] for row in db.execute("PRAGMA table_info(automatic_recalls)")]
    if "authority_json" in columns:
        return
    if columns != ["recall_id", "window_sha256", "target_event_id", "record_json", "state",
                   "response_json"]:
        raise ValueError("OWNER_LEGACY_CUSTODY_CHANGED")
    with db:
        db.execute("CREATE TABLE automatic_recalls_next (recall_id TEXT PRIMARY KEY,"
            "window_sha256 TEXT,target_event_id TEXT NOT NULL,record_json TEXT NOT NULL,"
            "state TEXT NOT NULL,response_json TEXT,authority_json TEXT)")
        db.execute("INSERT INTO automatic_recalls_next(rowid,recall_id,window_sha256,"
            "target_event_id,record_json,state,response_json) "
            "SELECT rowid,* FROM automatic_recalls")
        db.execute("DROP TABLE automatic_recalls")
        db.execute("ALTER TABLE automatic_recalls_next RENAME TO automatic_recalls")


def inspect(spool):
    from .owner_resolution import unsettled_attempts, unsettled_inputs, unsettled_results
    from .window import TestWindow, stored_plan

    if not spool.reconciliation_only or spool.lock is None:
        raise ValueError("DELIVERY_QUIESCENT_PROCESS_REQUIRED")
    saved = spool.db.execute("SELECT plan_json FROM test_window WHERE singleton=1").fetchone()
    if saved is None:
        raise ValueError("OWNER_LEGACY_CUSTODY_REQUIRED")
    old = TestWindow(spool, stored_plan(saved[0], replace(spool.config, owner_authority=None)))
    row = old.verify(active=False)
    records, head = old.read_journal()
    if (row["state"] != "STOPPED" or old.stop_path.is_symlink() or not old.stop_path.is_file()
        or old.stop_path.read_bytes() != b"OWNER_STOP\n"
        or not any(record["kind"] == "CORE_STOP_CONFIRMED" for record in records)):
        raise ValueError("OWNER_LEGACY_STOP_REQUIRED")
    old.check_spool()
    if unsettled_inputs(old) or unsettled_attempts(old) or unsettled_results(old):
        raise ValueError("OWNER_HANDOFF_RECONCILIATION_REQUIRED")
    for table, condition in (("automatic_recalls", "state!='ACCEPTED'"),
                             ("automatic_media", "state NOT IN ('DONE','CANCELLED')"),
                             ("automatic_received", "1=1"), ("inbox", "state='pending'")):
        if spool.db.execute("SELECT 1 FROM sqlite_master WHERE name=?", (table,)).fetchone():
            if spool.db.execute(f"SELECT 1 FROM {table} WHERE {condition} LIMIT 1").fetchone():
                raise ValueError("OWNER_HANDOFF_QUEUE_RECONCILIATION_REQUIRED")
    history = old.verify_history(records)
    evidence = []
    for plan, entries in history:
        path = plan.journal.resolve()
        evidence.append({"path": str(path),
                         "head": journal.digest(entries[-1])})
    old.prepare_schema()
    recall_schema(spool.db)
    names = {row[0] for row in spool.db.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    tables = _tables(spool.db, names & TABLES, [])
    spool.db.row_factory = None
    return {"tables": tables, "journals": evidence, "head": head,
            "budget_journal_id": old.plan.value["live_text"]["budget_journal_id"]}


def verify(runtime, value, references):
    if not isinstance(value, dict) or set(value) != {
        "tables", "journals", "head", "budget_journal_id",
    }:
        raise ValueError("OWNER_LEGACY_CUSTODY_CHANGED")
    if _tables(runtime.db, value["tables"], references) != value["tables"]:
        raise ValueError("OWNER_LEGACY_CUSTODY_CHANGED")
    for evidence in value["journals"]:
        from pathlib import Path

        path = Path(evidence["path"])
        if not path.is_absolute():
            raise ValueError("OWNER_LEGACY_JOURNAL_LOCATION_CHANGED")
        if journal.read(path)[1] != evidence["head"]:
            raise ValueError("OWNER_LEGACY_CUSTODY_CHANGED")


async def command(config, args):
    from .network import private_http_client
    from .onebot_client import bounded_post
    from .owner_runtime import OwnerRuntime, check_receipt
    from .spool import Spool

    with Spool(config, reconciliation_only=True) as spool:
        if spool.db.execute("SELECT 1 FROM sqlite_master WHERE name='owner_runtime'").fetchone():
            raise ValueError("OWNER_CUTOVER_ALREADY_RECORDED")
        legacy = inspect(spool)
        async with private_http_client(config.core_url, config.bridge_token) as client:
            response = await bounded_post(client, "/internal/v1/owner-authority/check",
                {"authority": config.owner_authority, "purpose": "chat"})
            from datetime import UTC, datetime

            check_receipt(config, config.owner_authority, "chat", response, datetime.now(UTC))
            if response["budget_journal_id"] != legacy["budget_journal_id"]:
                raise ValueError("OWNER_LEGACY_BUDGET_CUSTODY_CHANGED")
            runtime = OwnerRuntime(spool, legacy=legacy)
            runtime.verify(active=False)
            return {"state": "ACTIVE", "authority": runtime.authority,
                    "journal_head": runtime.row()["journal_head"]}
