"""Explicit stopped-custody handoff; old sources and attempts keep their original refs."""

import json
from dataclasses import replace
from types import SimpleNamespace

import companion_delivery_protocol as wire
from companion_delivery_protocol import model_carry

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


def carried(value, capture):
    fields = {"version", "authority", "event_id", "input_state", "carry_receipt"}
    if (not isinstance(value, dict) or set(value) != fields
        or type(value["version"]) is not int or value["version"] != 1
        or any(value[key] != capture[key] for key in ("authority", "event_id"))):
        raise ValueError("OWNER_MODEL_CARRY_INPUT_CHANGED")
    model_carry.carried(value["input_state"], capture, value["carry_receipt"])


def closed(runtime, records, references, *, carry_receipt=None):
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
    carried_inputs = {record["data"]["event_id"]: record for record in records
                      if record["kind"] == "OWNER_INPUT_CARRIED"}
    current_carried = set()
    results, inbox = [], []
    for source in inputs:
        capture = json.loads(source["capture_json"])
        retained = carried_inputs.get(source["event_id"])
        if retained is None:
            terminal(terminals.get(source["event_id"]), capture)
        else:
            carried(retained["data"], capture)
            if source["event_id"] in terminals:
                raise ValueError("OWNER_MODEL_CARRY_INPUT_CHANGED")
            if capture["authority"] == references[-1]:
                if carry_receipt is None or retained["data"]["carry_receipt"] != carry_receipt:
                    raise ValueError("OWNER_MODEL_CARRY_REQUIRED")
                current_carried.add(source["event_id"])
        inbox.extend(_rows(runtime.db, "SELECT * FROM inbox WHERE key=?", (source["event_id"],)))
    if carry_receipt is not None:
        model_carry.receipt(carry_receipt)
        if (carry_receipt["authority"] != references[-1]
            or current_carried != {item["event_id"] for item in carry_receipt["inputs"]}):
            raise ValueError("OWNER_MODEL_CARRY_INPUT_CHANGED")
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
    custody = {"inputs": inputs, "attempts": attempts, "results": results,
               "inbox": inbox, "queues": queues}
    retained = [carried_inputs[source["event_id"]] for source in inputs
                if source["event_id"] in carried_inputs]
    if retained:
        custody["carried"] = retained
    if carry_receipt is not None:
        custody["carry_receipt"] = carry_receipt
    return wire.digest(custody)


def _carry_prepared(value, authority):
    if (not isinstance(value, dict) or set(value) != {
            "version", "authority", "core_stop", "custody_sha256", "carry_receipt"}
        or type(value["version"]) is not int or value["version"] != 1
        or value["authority"] != authority or not wire.hash_value(value["custody_sha256"])):
        raise ValueError("OWNER_HANDOFF_CUSTODY_CHANGED")
    owner_native_resolution._confirmation(SimpleNamespace(authority=authority), value["core_stop"])
    proof = model_carry.receipt(value["carry_receipt"])
    if (proof["authority"] != authority or any(proof[key] != value["core_stop"][key]
            for key in ("core_stop_sha256", "budget_journal_id"))):
        raise ValueError("OWNER_MODEL_CARRY_INPUT_CHANGED")
    return proof


def _snapshot(runtime):
    """Pin native custody across every await, including non-journal inbox mutations."""
    records, head = owner_native_resolution._stopped(runtime)
    material = {}
    for table in ("identity", "owner_runtime", "window_inputs", "window_attempts", "inbox",
                  "results", "automatic_recalls", "automatic_media"):
        if runtime.db.execute("SELECT 1 FROM sqlite_master WHERE name=?", (table,)).fetchone():
            material[table] = sorted(_rows(runtime.db, f"SELECT * FROM {table}"),
                                     key=wire.canonical)
    return records, wire.digest({"head": head, "tables": material})


def _unchanged(runtime, before):
    if _snapshot(runtime)[1] != before:
        raise ValueError("OWNER_HANDOFF_CUSTODY_CHANGED")


async def _carry_lookup(runtime, client, carry_sha256, successor_policy_sha256):
    if not wire.hash_value(carry_sha256) or not wire.hash_value(successor_policy_sha256):
        raise ValueError("OWNER_MODEL_CARRY_RECEIPT_INVALID")
    before = _snapshot(runtime)[1]
    proof = model_carry.receipt(await bounded_post(
        client, "/internal/v1/owner-authority/model-carry-receipt", {
            "authority": runtime.authority, "carry_sha256": carry_sha256,
            "successor_policy_sha256": successor_policy_sha256}))
    _unchanged(runtime, before)
    if (proof["authority"] != runtime.authority or proof["carry_sha256"] != carry_sha256
        or proof["successor_policy_sha256"] != successor_policy_sha256):
        raise ValueError("OWNER_MODEL_CARRY_INPUT_CHANGED")
    return proof


def verify(runtime, records):
    references = []
    prepared = None
    stopped = False
    terminal_events, carried_events = set(), set()
    for index, record in enumerate(records):
        value = record["data"]
        if record["kind"] == "OWNER_BOUND":
            references.append(value["authority"])
        elif record["kind"] == "OWNER_STOPPED":
            stopped = True
        elif record["kind"] == "OWNER_INPUT_TERMINAL":
            source = runtime.db.execute("SELECT capture_json FROM window_inputs WHERE event_id=?",
                                         (value["event_id"],)).fetchone()
            if (not stopped or source is None or value["authority"] != references[-1]
                or value["event_id"] in carried_events):
                raise ValueError("OWNER_INPUT_TERMINAL_REQUIRED")
            terminal(value, json.loads(source[0]))
            terminal_events.add(value["event_id"])
        elif record["kind"] == "OWNER_INPUT_CARRIED":
            source = runtime.db.execute("SELECT capture_json FROM window_inputs WHERE event_id=?",
                                         (value.get("event_id"),)).fetchone()
            if (not stopped or source is None or value.get("authority") != references[-1]
                or value.get("event_id") in terminal_events | carried_events):
                raise ValueError("OWNER_MODEL_CARRY_INPUT_CHANGED")
            carried(value, json.loads(source[0]))
            carried_events.add(value["event_id"])
        elif record["kind"] == "OWNER_HANDOFF_PREPARED":
            context = SimpleNamespace(authority=references[-1])
            if (not stopped or set(value) != {"authority", "core_stop", "custody_sha256"}
                or value["authority"] != references[-1]
                or value["custody_sha256"] != closed(runtime, records[:index], references)):
                raise ValueError("OWNER_HANDOFF_CUSTODY_CHANGED")
            owner_native_resolution._confirmation(context, value["core_stop"])
            prepared = record
        elif record["kind"] == "OWNER_CARRY_HANDOFF_PREPARED":
            proof = _carry_prepared(value, references[-1])
            if (not stopped or value["custody_sha256"] != closed(
                    runtime, records[:index], references, carry_receipt=proof)):
                raise ValueError("OWNER_HANDOFF_CUSTODY_CHANGED")
            prepared = record
        elif record["kind"] == "OWNER_REBOUND":
            proof = (prepared["data"]["carry_receipt"] if prepared
                     and prepared["kind"] == "OWNER_CARRY_HANDOFF_PREPARED" else None)
            if (not prepared or set(value) != {
                "authority", "previous_authority", "prepared_sha256", "custody_sha256"}
                or value["prepared_sha256"] != wire.digest(prepared)
                 or proof is not None and value["authority"]["policy_sha256"] != (
                     proof["successor_policy_sha256"])
                 or value["custody_sha256"] != closed(
                     runtime, records[:index], references, carry_receipt=proof)):
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


async def prepare_carry(runtime, client, carry_sha256, successor_policy_sha256):
    if not wire.hash_value(carry_sha256) or not wire.hash_value(successor_policy_sha256):
        raise ValueError("OWNER_MODEL_CARRY_RECEIPT_INVALID")
    records, before = _snapshot(runtime)
    confirmation = await bounded_post(client, "/internal/v1/owner-authority/stop-receipt",
                                       {"authority": runtime.authority})
    _unchanged(runtime, before)
    owner_native_resolution._confirmation(runtime, confirmation)
    proof = await _carry_lookup(runtime, client, carry_sha256, successor_policy_sha256)
    if any(proof[key] != confirmation[key] for key in ("core_stop_sha256", "budget_journal_id")):
        raise ValueError("OWNER_MODEL_CARRY_INPUT_CHANGED")
    inputs = _rows(runtime.db, "SELECT event_id,capture_json FROM window_inputs "
                   "WHERE plan_hash=? ORDER BY event_id", (wire.digest(runtime.authority),))
    selected = {item["event_id"] for item in proof["inputs"]}
    if not selected <= {source["event_id"] for source in inputs}:
        raise ValueError("OWNER_MODEL_CARRY_INPUT_CHANGED")
    existing = {record["data"]["event_id"]: record for record in records
                if record["kind"] in {"OWNER_INPUT_TERMINAL", "OWNER_INPUT_CARRIED"}}
    for source in inputs:
        capture = json.loads(source["capture_json"])
        if prior := existing.get(source["event_id"]):
            if source["event_id"] in selected:
                carried(prior["data"], capture)
                if prior["data"]["carry_receipt"] != proof:
                    raise ValueError("OWNER_MODEL_CARRY_INPUT_CHANGED")
            else:
                terminal(prior["data"], capture)
            continue
        before = _snapshot(runtime)[1]
        state = await bounded_post(client, "/internal/v1/owner-authority/inputs/state", capture)
        _unchanged(runtime, before)
        if source["event_id"] in selected:
            model_carry.carried(state, capture, proof)
            kind, value = "OWNER_INPUT_CARRIED", {"version": 1,
                "authority": runtime.authority, "event_id": source["event_id"],
                "input_state": state, "carry_receipt": proof}
        else:
            terminal(state, capture)
            kind, value = "OWNER_INPUT_TERMINAL", state
        runtime.spool.capacity(len(wire.canonical(value)) * 3)
        with runtime.db:
            runtime.append(kind, value)
            runtime.db.execute("UPDATE inbox SET state='closed',body=NULL "
                               "WHERE key=? AND state='pending'", (source["event_id"],))
    records, before = _snapshot(runtime)
    references = [record["data"]["authority"] for record in records
                  if record["kind"] in {"OWNER_BOUND", "OWNER_REBOUND"}]
    custody_hash = closed(runtime, records, references, carry_receipt=proof)
    latest = await bounded_post(client, "/internal/v1/owner-authority/stop-receipt",
                                 {"authority": runtime.authority})
    _unchanged(runtime, before)
    if latest != confirmation or await _carry_lookup(
            runtime, client, carry_sha256, successor_policy_sha256) != proof:
        raise ValueError("OWNER_HANDOFF_CUSTODY_CHANGED")
    _unchanged(runtime, before)
    value = {"version": 1, "authority": runtime.authority, "core_stop": confirmation,
             "custody_sha256": custody_hash, "carry_receipt": proof}
    if records[-1]["kind"] != "OWNER_CARRY_HANDOFF_PREPARED" or records[-1]["data"] != value:
        runtime.spool.capacity(len(wire.canonical(value)) * 3)
        with runtime.db:
            runtime.append("OWNER_CARRY_HANDOFF_PREPARED", value)
    return {"state": "CARRY_HANDOFF_PREPARED", "authority": runtime.authority,
             "carry_sha256": proof["carry_sha256"],
             "successor_policy_sha256": proof["successor_policy_sha256"],
             "journal_head": runtime.row()["journal_head"]}


async def handoff(runtime, client, successor):
    from .owner_runtime import check_receipt

    records, before = owner_native_resolution._stopped(runtime)
    if records[-1]["kind"] not in {"OWNER_HANDOFF_PREPARED", "OWNER_CARRY_HANDOFF_PREPARED"}:
        raise ValueError("OWNER_HANDOFF_PREPARATION_REQUIRED")
    proof = (_carry_prepared(records[-1]["data"], runtime.authority)
             if records[-1]["kind"] == "OWNER_CARRY_HANDOFF_PREPARED" else None)
    if (successor["grant_id"] != runtime.authority["grant_id"]
        or successor["revision"] != runtime.authority["revision"] + 2
         or successor["restore_generation"] != runtime.authority["restore_generation"]
         or proof is not None and successor["policy_sha256"] != proof["successor_policy_sha256"]):
        raise ValueError("OWNER_HANDOFF_BINDING_CHANGED")
    custody_before = _snapshot(runtime)[1] if proof is not None else None
    response = await bounded_post(client, "/internal/v1/owner-authority/check",
                                   {"authority": successor, "purpose": "chat"})
    check_receipt(runtime.config, successor, "chat", response, runtime.clock())
    if (response["budget_journal_id"] != records[-1]["data"]["core_stop"]["budget_journal_id"]
        or owner_native_resolution._stopped(runtime)[1] != before):
        raise ValueError("OWNER_HANDOFF_AUTHORITY_REQUIRED")
    if proof is not None:
        _unchanged(runtime, custody_before)
        latest = await _carry_lookup(runtime, client, proof["carry_sha256"],
                                     proof["successor_policy_sha256"])
        _unchanged(runtime, custody_before)
        if latest != proof:
            raise ValueError("OWNER_MODEL_CARRY_INPUT_CHANGED")
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
            if args.command in {"owner-handoff-prepare", "owner-carry-handoff-prepare"}:
                if config.owner_authority != original:
                    raise ValueError("OWNER_AUTHORITY_BINDING_CHANGED")
                if args.command == "owner-carry-handoff-prepare":
                    return await prepare_carry(runtime, client, args.carry_sha256,
                                               args.successor_policy_sha256)
                return await prepare(runtime, client)
            return await handoff(runtime, client, config.owner_authority)
