"""Explicit stopped-custody handoff; old sources and attempts keep their original refs."""

import json
from dataclasses import replace
from types import SimpleNamespace

import companion_delivery_protocol as wire

from . import owner_native_resolution
from .onebot_client import bounded_post


def _rows(db, query, values=()):
    cursor = db.execute(query, values)
    names = [column[0] for column in cursor.description]
    return [dict(zip(names, row, strict=True)) for row in cursor]


def terminal(receipt, capture):
    fields = {"authority", "event_id", "input_sha256", "terminal", "state", "unsettled_work",
              "unsettled_model", "unsettled_delivery", "work_sha256"}
    if (not isinstance(receipt, dict) or set(receipt) not in (fields, fields | {"reply_revision"})
        or "reply_revision" in receipt and (type(receipt["reply_revision"]) is not int
                                             or receipt["reply_revision"] < 0)
        or any(receipt[key] != capture[key] for key in ("authority", "event_id"))
        or receipt["input_sha256"] != (wire.digest(capture["notice"])
            if capture["kind"] == "authenticated_onebot_poke" else capture["input_sha256"])
        or receipt["terminal"] is not True or receipt["state"] not in {
            "COMPLETE", "RECALLED", "CANCELLED"}
        or any(receipt[key] is not False for key in (
            "unsettled_work", "unsettled_model", "unsettled_delivery"))
        or not wire.hash_value(receipt["work_sha256"])):
        raise ValueError("OWNER_INPUT_TERMINAL_REQUIRED")


def closed(runtime, records, references):
    hashes = [wire.digest(ref) for ref in references]
    placeholders = ",".join("?" for _ in hashes)
    inputs = _rows(runtime.db, f"SELECT * FROM window_inputs WHERE plan_hash IN ({placeholders}) "
                   "ORDER BY event_id", hashes)
    attempts = _rows(runtime.db, "SELECT * FROM window_attempts "
                     f"WHERE plan_hash IN ({placeholders}) ORDER BY message_id", hashes)
    receipts = {record["data"]["delivery"]["message_id"]: record["data"] for record in records
                if record["kind"] == "OWNER_RECONCILED"}
    terminals = {record["data"]["event_id"]: record["data"] for record in records
                 if record["kind"] == "OWNER_INPUT_TERMINAL"}
    results, inbox = [], []
    for source in inputs:
        capture = json.loads(source["capture_json"])
        terminal(terminals.get(source["event_id"]), capture)
        inbox.extend(_rows(runtime.db, "SELECT * FROM inbox WHERE key=?", (source["event_id"],)))
    for attempt in attempts:
        result = _rows(runtime.db, "SELECT * FROM results WHERE message_id=?",
                       (attempt["message_id"],))
        receipt = receipts.get(attempt["message_id"])
        if attempt["state"] in owner_native_resolution.UNCERTAIN:
            if receipt is None:
                raise ValueError("OWNER_HANDOFF_RECONCILIATION_REQUIRED")
        elif attempt["state"] == "ACK":
            body = json.loads(result[0]["body"]) if result else {}
            ack = (body.get("account_action_receipt") or body.get("native_ack")
                   or body.get("provider_message_id"))
            if (not result or result[0]["state"] != "accepted" or body.get("result") != "SENT"
                or not ack or wire.digest(ack) != attempt["provider_ack_hash"]):
                raise ValueError("OWNER_HANDOFF_RECONCILIATION_REQUIRED")
        elif attempt["state"] == "NOT_SENT":
            body = json.loads(result[0]["body"]) if result else {}
            if not result or result[0]["state"] != "accepted" or body.get("result") != "NOT_SENT":
                raise ValueError("OWNER_HANDOFF_RECONCILIATION_REQUIRED")
        else:
            raise ValueError("OWNER_HANDOFF_RECONCILIATION_REQUIRED")
        results.extend(result)
    queues = {}
    for table, done in (("automatic_recalls", {"ACCEPTED"}),
                        ("automatic_media", {"DONE", "CANCELLED"})):
        if not runtime.db.execute("SELECT 1 FROM sqlite_master WHERE name=?", (table,)).fetchone():
            continue
        columns = [row[1] for row in runtime.db.execute(f"PRAGMA table_info({table})")]
        if "authority_json" in columns:
            entries = [row for row in _rows(runtime.db, f"SELECT * FROM {table}")
                       if row["authority_json"] and json.loads(row["authority_json"]) in references]
        else:
            entries = _rows(runtime.db,
                f"SELECT * FROM {table} WHERE plan_hash IN ({placeholders})", hashes)
        if any(row["state"] not in done for row in entries):
            raise ValueError("OWNER_HANDOFF_QUEUE_RECONCILIATION_REQUIRED")
        queues[table] = sorted(entries, key=wire.canonical)
    return wire.digest({"inputs": inputs, "attempts": attempts, "results": results,
                        "inbox": inbox, "queues": queues})


def verify(runtime, records):
    references = []
    prepared = None
    stopped = False
    for index, record in enumerate(records):
        value = record["data"]
        if record["kind"] == "OWNER_BOUND":
            references.append(value["authority"])
        elif record["kind"] == "OWNER_STOPPED":
            stopped = True
        elif record["kind"] == "OWNER_INPUT_TERMINAL":
            source = runtime.db.execute("SELECT capture_json FROM window_inputs WHERE event_id=?",
                                         (value["event_id"],)).fetchone()
            if not stopped or source is None or value["authority"] != references[-1]:
                raise ValueError("OWNER_INPUT_TERMINAL_REQUIRED")
            terminal(value, json.loads(source[0]))
        elif record["kind"] == "OWNER_HANDOFF_PREPARED":
            context = SimpleNamespace(authority=references[-1])
            if (not stopped or set(value) != {"authority", "core_stop", "custody_sha256"}
                or value["authority"] != references[-1]
                or value["custody_sha256"] != closed(runtime, records[:index], references)):
                raise ValueError("OWNER_HANDOFF_CUSTODY_CHANGED")
            owner_native_resolution._confirmation(context, value["core_stop"])
            prepared = record
        elif record["kind"] == "OWNER_REBOUND":
            if (not prepared or set(value) != {
                "authority", "previous_authority", "prepared_sha256", "custody_sha256"}
                or value["prepared_sha256"] != wire.digest(prepared)
                or value["custody_sha256"] != closed(runtime, records[:index], references)):
                raise ValueError("OWNER_HANDOFF_CUSTODY_CHANGED")
            references.append(value["authority"])
            stopped, prepared = False, None


async def prepare(runtime, client):
    records, _ = owner_native_resolution._stopped(runtime)
    confirmation = await bounded_post(client, "/internal/v1/owner-authority/stop-receipt",
                                       {"authority": runtime.authority})
    owner_native_resolution._confirmation(runtime, confirmation)
    existing = {record["data"]["event_id"] for record in records
                if record["kind"] == "OWNER_INPUT_TERMINAL"}
    for source in _rows(runtime.db, "SELECT event_id,capture_json FROM window_inputs "
                        "WHERE plan_hash=? ORDER BY event_id", (wire.digest(runtime.authority),)):
        if source["event_id"] in existing:
            continue
        capture = json.loads(source["capture_json"])
        receipt = await bounded_post(client, "/internal/v1/owner-authority/inputs/state", capture)
        terminal(receipt, capture)
        owner_native_resolution._stopped(runtime)
        with runtime.db:
            runtime.append("OWNER_INPUT_TERMINAL", receipt)
            runtime.db.execute("UPDATE inbox SET state='closed',body=NULL "
                               "WHERE key=? AND state='pending'",
                               (source["event_id"],))
    records, before = owner_native_resolution._stopped(runtime)
    references = [record["data"]["authority"] for record in records
                  if record["kind"] in {"OWNER_BOUND", "OWNER_REBOUND"}]
    custody_hash = closed(runtime, records, references)
    latest = await bounded_post(client, "/internal/v1/owner-authority/stop-receipt",
                                 {"authority": runtime.authority})
    if latest != confirmation or owner_native_resolution._stopped(runtime)[1] != before:
        raise ValueError("OWNER_HANDOFF_CUSTODY_CHANGED")
    value = {"authority": runtime.authority, "core_stop": confirmation,
             "custody_sha256": custody_hash}
    if records[-1]["kind"] != "OWNER_HANDOFF_PREPARED" or records[-1]["data"] != value:
        with runtime.db:
            runtime.append("OWNER_HANDOFF_PREPARED", value)
    return {"state": "HANDOFF_PREPARED", "authority": runtime.authority,
            "journal_head": runtime.row()["journal_head"]}


async def handoff(runtime, client, successor):
    from .owner_runtime import check_receipt

    records, before = owner_native_resolution._stopped(runtime)
    if records[-1]["kind"] != "OWNER_HANDOFF_PREPARED":
        raise ValueError("OWNER_HANDOFF_PREPARATION_REQUIRED")
    if (successor["grant_id"] != runtime.authority["grant_id"]
        or successor["revision"] != runtime.authority["revision"] + 2
        or successor["restore_generation"] != runtime.authority["restore_generation"]):
        raise ValueError("OWNER_HANDOFF_BINDING_CHANGED")
    response = await bounded_post(client, "/internal/v1/owner-authority/check",
                                   {"authority": successor, "purpose": "chat"})
    check_receipt(runtime.config, successor, "chat", response, runtime.clock())
    if (response["budget_journal_id"] != records[-1]["data"]["core_stop"]["budget_journal_id"]
        or owner_native_resolution._stopped(runtime)[1] != before):
        raise ValueError("OWNER_HANDOFF_AUTHORITY_REQUIRED")
    marker = runtime.spool.directory / ("owner-" + wire.digest(successor) + ".stop")
    if marker.exists() or marker.is_symlink():
        raise ValueError("OWNER_HANDOFF_BINDING_CHANGED")
    with runtime.db:
        runtime.append("OWNER_REBOUND", {"authority": successor,
            "previous_authority": runtime.authority, "prepared_sha256": wire.digest(records[-1]),
            "custody_sha256": records[-1]["data"]["custody_sha256"]})
        runtime.db.execute("UPDATE owner_runtime SET authority_json=?,state='ACTIVE' WHERE id=1",
                           (wire.canonical(successor).decode(),))
    return {"state": "ACTIVE", "authority": successor,
            "journal_head": runtime.row()["journal_head"]}


async def command(config, args):
    from .network import private_http_client
    from .owner_runtime import OwnerRuntime
    from .spool import Spool

    with Spool(config, reconciliation_only=True) as spool:
        saved = spool.db.execute("SELECT authority_json FROM owner_runtime WHERE id=1").fetchone()
        if saved is None:
            raise ValueError("OWNER_RUNTIME_NOT_FOUND")
        original = json.loads(saved[0])
        spool.config = replace(config, owner_authority=original)
        runtime = OwnerRuntime(spool)
        async with private_http_client(config.core_url, config.bridge_token) as client:
            if args.command == "owner-handoff-prepare":
                if config.owner_authority != original:
                    raise ValueError("OWNER_AUTHORITY_BINDING_CHANGED")
                return await prepare(runtime, client)
            return await handoff(runtime, client, config.owner_authority)
