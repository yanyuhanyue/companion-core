"""Read-only diagnostics for retiring the historical Bridge meme business store.

This is a report, never a cutover permission, cleanup operation or settlement writer.
"""

import argparse
import hashlib
import json
import math
import re
import sqlite3
from collections import Counter
from contextlib import closing, nullcontext
from datetime import UTC, datetime
from pathlib import Path

from companion_meme_protocol import (
    CaptionOutcome,
    UsageReceipt,
    digest,
    parse,
    source_evidence,
    strict_json,
)

from .config import BridgeConfig
from .instance_lock import acquire
from .meme_catalog import MemeCatalog
from .meme_export import MAX_MANIFEST, _file, _ReadonlyRuntime
from .meme_semantic import terminal_without_usage
from .owner_handoff import closed

# Only the existing native table shapes are recognized. Absence is not evidence
# that a historical worker never incurred a liability.
SCHEMA = {
    "meme_assets": "sha:TEXT data:BLOB metadata:TEXT active:INTEGER",
    "meme_offers": "key:TEXT intent:TEXT category:TEXT ids:TEXT",
    "meme_pins": "key:TEXT sha:TEXT state:TEXT sent_at:REAL",
    "meme_runtime_offers": "event_id:TEXT event_sha256:TEXT window_sha256:TEXT offer_json:TEXT",
    "meme_runtime_bindings": ("delivery_key:TEXT offer_key:TEXT event_id:TEXT "
                              "candidate_id:TEXT category:TEXT window_sha256:TEXT"),
    "meme_runtime_results": "attempt_key:TEXT job_id:TEXT body_json:TEXT state:TEXT",
    "meme_runtime_sources": "sha:TEXT attempt_key:TEXT claim_json:TEXT state:TEXT",
    "meme_runtime_semantic_offers": ("event_id:TEXT window_sha256:TEXT reply_revision:INTEGER "
                                     "query_binding:TEXT offer_json:TEXT state:TEXT"),
    "meme_semantic_config": "singleton:INTEGER value:TEXT",
    "meme_semantic_assets": ("sha:TEXT source_sha:TEXT cache_key:TEXT "
                              "binding_request:TEXT state:TEXT"),
    "meme_caption_cache": ("cache_key:TEXT sha:TEXT identity_json:TEXT "
                           "result_json:TEXT usage_json:TEXT"),
    "meme_caption_bindings": ("request_sha:TEXT sha:TEXT source_sha:TEXT cache_key:TEXT "
                              "context_json:TEXT job_id:TEXT state:TEXT "
                              "outcome_json:TEXT usage_json:TEXT"),
    "meme_semantic_vectors": ("sha:TEXT source_sha:TEXT cache_key:TEXT caption_sha:TEXT "
                              "model_version:TEXT dimensions:INTEGER vector_json:TEXT"),
    "meme_semantic_offers": "key:TEXT query_sha:TEXT profile_sha:TEXT proof_json:TEXT",
}
# Native PRAGMA table_info snapshots from the unchanged catalog/runtime/semantic
# constructors at 492d745. Freeze nullability/defaults/primary keys as well as names.
SCHEMA_SHA256 = dict(zip(SCHEMA, (
    "0ae01ef0a595e129a176a94ba9645242bf92268bc1aded0bc3e7c915c3703689",
    "3265ca8c3d679a1b61b6c2da3a1d99ecf3764f246b1e6983509e1400c2162cdf",
    "9a24671d6b191d242ff53e3a10766c1c8f25c146d331a01223e9b3240a04d772",
    "7cee6ca9f0f405d0e526367a092bcd2ca876887e2fa7b98bbbbb7e4cfeeb9da2",
    "74248b2a3869ca195a47b8847f0bff0c721fcecefd5d347b200d4e1cab06a780",
    "97036dbf6f0e153bf625fb0b7a177bc1835789bfb503c7488b5f83c930591ea0",
    "4c8fac3ee6352dcf4d5ae0d5ed46120e3acdf235705820a8563fcd3c6a3f3b6c",
    "f50cd2b842b0fc554432f654cee5434f1c6b8a71ae755356d6bc2feb95829cfb",
    "eee2f519f349f79ec28a18248f6eff07ddab9c63587ae7196eb1c02a5309d907",
    "8302cf3f8a20de786c6bc0d3a6124aaf1faad951d3acd6f8abd863763be08a2b",
    "b732d9fe151ba2d862cbeb20279d970cb4722880b4233cc72a36784516f4ec11",
    "e8a3a011bb01afb1c3025f30a876fa27c99b685f15568d73a4dc203b81595aad",
    "0ee519e79252c70079cb3594650beedc85055f85a5211231d44ac4c1c90c5ae4",
    "66c4bf91268ec15bcbdbfc8ecd383bbe544b922a45a433940ba750a505b0f777",
), strict=True))
CONTENT = {
    "meme_assets": ("data", "metadata"),
    "meme_offers": ("intent", "category", "ids"),
    "meme_runtime_offers": ("offer_json",),
    "meme_runtime_sources": ("claim_json",),
    "meme_runtime_semantic_offers": ("query_binding", "offer_json"),
    "meme_caption_cache": ("identity_json", "result_json"),
    "meme_caption_bindings": ("context_json", "outcome_json"),
    "meme_semantic_vectors": ("vector_json",),
    "meme_semantic_offers": ("proof_json",),
}
EXTERNAL = (
    "LATEST_INDEPENDENT_CORE_LEDGER_AND_LIVE_RECONCILE_REQUIRED",
    "EXPLICIT_MANAGED_COPY_INVENTORY_AND_RETIREMENT_REQUIRED",
    "RESTORED_BRIDGE_BACKUPS_REQUIRE_CURRENT_STOP_AUTHORITY_AND_REVALIDATION",
)
SHA = re.compile(r"[a-f0-9]{64}")
MAX_ROWS = 10000


class Refused(ValueError):
    """A fixed, content-free local refusal reason."""


def _fingerprints(directory, names, lock_handle):
    result = {}
    for name in names:
        path = directory / name
        if not path.exists() and not path.is_symlink():
            result[name] = None
            continue
        path = _file(path)
        before = path.stat()
        if before.st_size > 512 * 1024 * 1024:
            raise Refused("FILE_LIMIT")
        hasher = hashlib.sha256()
        # Windows byte-range locks also cover reads through another descriptor.
        # Hash the held lock through its original handle, retaining that OS lock.
        source = nullcontext(lock_handle) if path.name == "instance.lock" else path.open("rb")
        with source as stream:
            stream.seek(0)
            for chunk in iter(lambda: stream.read(65536), b""):
                hasher.update(chunk)
        after = path.stat()
        previous = (before.st_size, before.st_mtime_ns, before.st_ctime_ns)
        current = (after.st_size, after.st_mtime_ns, after.st_ctime_ns)
        if previous != current:
            raise Refused("FILES_CHANGED")
        result[name] = (hasher.hexdigest(), *current)
    return result


def _timestamp(value, now):
    if isinstance(value, str):
        moment = datetime.fromisoformat(value)
        if moment.tzinfo is None:
            raise ValueError("TIME_UNVERIFIED")
        result = moment.timestamp()
    elif type(value) in {int, float} and math.isfinite(value):
        result = value
    else:
        raise ValueError("TIME_UNVERIFIED")
    if not 0 <= result <= now.timestamp():
        raise ValueError("TIME_UNVERIFIED")
    return result


def _inventory(db, issues):
    found = dict(db.execute("SELECT name,type FROM sqlite_master WHERE name LIKE 'meme_%'"))
    tables = {}
    if set(found) - set(SCHEMA) - {"meme_pins_sha"}:
        issues["UNKNOWN_MEME_SCHEMA"] += 1
    for table, shape in SCHEMA.items():
        columns = db.execute(f"PRAGMA table_info({table})").fetchall()
        expected = [tuple(field.split(":")) for field in shape.split()]
        if (found.get(table) != "table" or [(r[1], r[2].upper()) for r in columns] != expected
            or digest(columns) != SCHEMA_SHA256[table]):
            issues["MISSING_OR_UNKNOWN_SCHEMA"] += 1
            continue
        if table == "meme_runtime_bindings":
            unique = {tuple(r[0] for r in db.execute(
                "SELECT name FROM pragma_index_info(?) ORDER BY seqno", (index[1],)))
                for index in db.execute("PRAGMA index_list(meme_runtime_bindings)") if index[2]}
            if unique != {("delivery_key",), ("offer_key",)}:
                issues["MISSING_OR_UNKNOWN_SCHEMA"] += 1
                continue
        count = db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        tables[table] = {"rows": count, "schema_sha256": digest(columns)}
        if count > MAX_ROWS:
            issues["ROW_LIMIT"] += 1
        tables[table]["content_cells"] = {
            column: db.execute(f"SELECT COUNT(*) FROM {table} WHERE {column} IS NOT NULL "
                               f"AND length({column})>0").fetchone()[0]
            for column in CONTENT.get(table, ())
        }
    return tables


def _accounting(db, issues):
    for request, sha, source, state, job, outcome_raw, usage_raw in db.execute(
        "SELECT request_sha,sha,source_sha,state,job_id,outcome_json,usage_json "
        "FROM meme_caption_bindings"
    ):
        try:
            outcome = parse(CaptionOutcome, outcome_raw)
            if (outcome.job_id != job or outcome.request_sha256 != request
                or outcome.asset_sha256 != sha or outcome.source_evidence_sha256 != source):
                raise ValueError("CAPTION_RECEIPT_UNVERIFIED")
            if terminal_without_usage((state, outcome_raw, usage_raw)):
                continue
            usage = parse(UsageReceipt, usage_raw)
            if outcome.usage != usage:
                raise ValueError("CAPTION_RECEIPT_UNVERIFIED")
            if (outcome.job_id != job or outcome.state not in {"SUCCEEDED", "FAILED", "CANCELLED"}
                or state not in {"SUCCEEDED", "FAILED", "CANCELLED"}
                or usage.state != "SETTLED"):
                issues["CAPTION_ACCOUNTING_UNSETTLED"] += 1
        except (ValueError, TypeError, KeyError):
            issues["CAPTION_RECEIPT_UNVERIFIED"] += 1
    for state, raw, attempt, job in db.execute(
        "SELECT state,body_json,attempt_key,job_id FROM meme_runtime_results"
    ):
        try:
            body = strict_json(raw)
            usage = body["usage"]
            if (not isinstance(body, dict)
                or set(body) != {"lease_token", "attempt_key", "outcome", "usage"}
                or body["attempt_key"] != attempt
                or not isinstance(attempt, str) or not 1 <= len(attempt) <= 256
                or not re.fullmatch(r"job_[a-f0-9]{32}", job)
                or not re.fullmatch(r"lease_[a-f0-9]{32}", body["lease_token"])
                or not isinstance(usage, dict)
                or set(usage) != {"queries", "requests", "download_bytes",
                                  "storage_bytes", "model_cost"}
                or any(type(n) is not int or n < 0 for n in usage.values())
                or usage["model_cost"] != 0):
                raise ValueError("DISCOVERY_RECEIPT_UNVERIFIED")
            if state != "SETTLED" or body["outcome"] != "SETTLED":
                issues["DISCOVERY_ACCOUNTING_UNSETTLED"] += 1
        except (ValueError, TypeError, KeyError):
            issues["DISCOVERY_RECEIPT_UNVERIFIED"] += 1
    for sha, attempt, raw, state in db.execute(
        "SELECT sha,attempt_key,claim_json,state FROM meme_runtime_sources"
    ):
        try:
            claim = strict_json(raw)
            result = db.execute("SELECT job_id,state FROM meme_runtime_results WHERE attempt_key=?",
                                (attempt,)).fetchone()
            if (not isinstance(claim, dict) or set(claim) != {
                "job_id", "event_id", "reply_revision", "intent", "provider",
                "lease_token", "lease_until", "attempt_key"}
                or not re.fullmatch(r"evt_[a-f0-9]{64}", claim["event_id"])
                or type(claim["reply_revision"]) is not int or claim["reply_revision"] < 1
                or claim["intent"] not in {"celebrate", "comfort", "like", "surprised",
                                          "morning", "sleep", "playful"}
                or claim["provider"] not in {"community", "brave"}
                or not re.fullmatch(r"lease_[a-f0-9]{32}", claim["lease_token"])
                or datetime.fromisoformat(claim["lease_until"]).tzinfo is None
                or not SHA.fullmatch(sha) or claim["attempt_key"] != attempt or result is None
                or claim["job_id"] != result[0] or result[1] != "SETTLED"
                or state not in {"INDEXED", "QUARANTINED", "CANCELLED"}):
                raise ValueError("SOURCE_OR_ACCOUNTING_UNVERIFIED")
        except (ValueError, TypeError, KeyError):
            issues["SOURCE_OR_ACCOUNTING_UNVERIFIED"] += 1
    orphan = db.execute("SELECT COUNT(*) FROM meme_assets a WHERE NOT EXISTS "
                        "(SELECT 1 FROM meme_runtime_sources s WHERE s.sha=a.sha)").fetchone()[0]
    if orphan:
        issues["ASSET_SOURCE_UNVERIFIED"] += orphan
    for sha, raw, active, size in db.execute(
        "SELECT sha,metadata,active,length(data) FROM meme_assets"
    ):
        try:
            meta = MemeCatalog.metadata(raw)
            evidence = source_evidence(meta)
            original = db.execute("SELECT source_sha FROM meme_semantic_assets WHERE sha=?",
                                  (sha,)).fetchone()
            if (not SHA.fullmatch(sha) or active not in (0, 1) or not evidence["acquisition"]
                or original is None or original[0] != digest(evidence)
                or active == 0 and size is not None
                or active == 1 and (size is None or not 0 < size <= 4 * 1024 * 1024)):
                raise ValueError("ASSET_SOURCE_UNVERIFIED")
            if active:
                data = db.execute("SELECT data FROM meme_assets WHERE sha=?", (sha,)).fetchone()[0]
                if hashlib.sha256(data).hexdigest() != sha:
                    raise ValueError("ASSET_BYTES_UNVERIFIED")
        except (ValueError, TypeError, KeyError):
            issues["ASSET_SOURCE_OR_BYTES_UNVERIFIED"] += 1
    for cache, raw in db.execute("SELECT cache_key,usage_json FROM meme_caption_cache"):
        try:
            usage = parse(UsageReceipt, raw)
            original = db.execute("SELECT 1 FROM meme_caption_bindings WHERE cache_key=? "
                                  "AND usage_json=?", (cache, raw)).fetchone()
            if usage.state != "SETTLED" or original is None:
                raise ValueError("CACHE_ACCOUNTING_UNVERIFIED")
        except (ValueError, TypeError):
            issues["CACHE_ACCOUNTING_UNVERIFIED"] += 1


def _pins(db, records, now, issues):
    counts = Counter()
    times = []
    for key, sha, state, sent_at in db.execute("SELECT * FROM meme_pins"):
        counts[state if state in {"PREPARED", "SENDING", "UNKNOWN", "SENT", "CANCELLED"}
               else "UNRECOGNIZED"] += 1
        if not SHA.fullmatch(key) or sha is not None and not SHA.fullmatch(sha):
            issues["PIN_IDENTITY_UNVERIFIED"] += 1
        if state in {"PREPARED", "SENDING", "UNKNOWN"}:
            issues["PIN_UNPROJECTED_OR_UNSETTLED"] += 1
        elif state == "SENT":
            try:
                at = _timestamp(sent_at, now)
                matches = [row for row in db.execute("SELECT offer_key,event_id,candidate_id,"
                           "window_sha256 FROM meme_runtime_bindings")
                           if hashlib.sha256(row[0].encode()).hexdigest() == key]
                if len(matches) != 1 or matches[0][2] != sha:
                    raise ValueError("PIN_ORIGINAL_RECEIPT_UNVERIFIED")
                binding = matches[0]
                attempts = db.execute("SELECT message_id,state FROM window_attempts "
                                      "WHERE event_id=? AND plan_hash=?",
                                      (binding[1], binding[3])).fetchall()
                if len(attempts) != 1 or attempts[0][1] != "ACK":
                    raise ValueError("PIN_ORIGINAL_RECEIPT_UNVERIFIED")
                acks = [r for r in records if r["kind"] == "ACK"
                        and r["data"]["message_id"] == attempts[0][0]]
                if len(acks) != 1:
                    raise ValueError("PIN_ORIGINAL_RECEIPT_UNVERIFIED")
                ack_at = _timestamp(acks[0]["at"], now)
                # Local pin commit can follow its original ACK; conflicting order
                # cannot be treated as an earlier expiry.
                if at < ack_at:
                    raise ValueError("PIN_ORIGINAL_RECEIPT_UNVERIFIED")
                times.append({"pin_sha256": key,
                              "sent_at": datetime.fromtimestamp(at, UTC).isoformat(),
                              "ack_at": datetime.fromtimestamp(ack_at, UTC).isoformat(),
                              "cooldown_policy": "UNVERIFIED", "wait_until": None})
            except (ValueError, TypeError, OverflowError):
                issues["SENT_TIME_UNVERIFIED"] += 1
            # Current mutable BridgeConfig cannot prove the old cooldown policy.
            issues["ORIGINAL_COOLDOWN_UNVERIFIED"] += 1
        elif state != "CANCELLED":
            issues["PIN_STATE_UNVERIFIED"] += 1
    # Native verification checks the original receipt itself. This extra summary
    # never projects it onto a pin or equates OWNER_RECONCILED with pin release.
    counts["original_owner_resolutions"] = sum(r["kind"] == "OWNER_RECONCILED" for r in records)
    for record in records:
        if record["kind"] != "OWNER_RECONCILED" or record["data"].get("outcome") != "SENT":
            continue
        issues["ORIGINAL_COOLDOWN_UNVERIFIED"] += 1
        try:
            resolved = _timestamp(record["data"]["at"], now)
            recorded = _timestamp(record["at"], now)
            if resolved > recorded:
                raise ValueError("RESOLUTION_TIME_UNVERIFIED")
            times.append({"resolution_sha256": digest(record["data"]),
                          "resolved_sent_at": datetime.fromtimestamp(resolved, UTC).isoformat(),
                          "recorded_at": datetime.fromtimestamp(recorded, UTC).isoformat(),
                          "cooldown_policy": "UNVERIFIED", "wait_until": None})
        except (ValueError, TypeError, KeyError, OverflowError):
            issues["RESOLUTION_TIME_UNVERIFIED"] += 1
    return {"states": dict(counts), "verified_times": times}


def _copies(paths):
    if len(paths) > 32:
        raise ValueError("COPY_LIMIT")
    result = []
    for path in paths:
        path = _file(path)
        before = path.stat()
        if before.st_size > MAX_MANIFEST:
            raise ValueError("COPY_LIMIT")
        hasher = hashlib.sha256()
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(65536), b""):
                hasher.update(chunk)
        after = path.stat()
        if (before.st_size, before.st_mtime_ns, before.st_ctime_ns) != (
            after.st_size, after.st_mtime_ns, after.st_ctime_ns
        ):
            raise Refused("FILES_CHANGED")
        result.append({"path_sha256": hashlib.sha256(str(path).encode()).hexdigest(),
                       "sha256": hasher.hexdigest(), "bytes": after.st_size})
    return result


def preflight(config, *, managed_copies=(), clock=lambda: datetime.now(UTC)):
    report = {"version": 1, "state": "BLOCKED", "cutover_authorized": False,
              "local_state": "BLOCKED", "external_gates": list(EXTERNAL), "wait_until": None}
    issues = Counter()
    try:
        directory = Path(config.data_dir).absolute()
        database, lock = (_file(directory / name) for name in ("spool.sqlite3", "instance.lock"))
        if lock.stat().st_size < 1 or config.owner_authority is None:
            raise ValueError("CUSTODY_UNVERIFIED")
        stop = _file(directory / ("owner-" + digest(config.owner_authority) + ".stop"))
        names = (database.name, lock.name, stop.name, "owner-journal.jsonl",
                 database.name + "-wal", database.name + "-shm", database.name + "-journal")
        with acquire(lock, existing_only=True) as held_lock:
            before = _fingerprints(directory, names, held_lock)
            if stop.read_bytes() != b"OWNER_STOP\n":
                raise ValueError("STOP_REQUIRED")
            if any(before[database.name + suffix] is not None
                   and before[database.name + suffix][1] > 0
                   for suffix in ("-wal", "-journal", "-shm")):
                raise Refused("WAL_RECOVERY_REQUIRED")
            # immutable disables SQLite change detection and sidecar writes. It
            # is safe here only for a clean, stopped baseline under the original
            # OS lock, with whole-file fingerprints checked before and after.
            uri = database.as_uri() + "?mode=ro&immutable=1"
            with closing(sqlite3.connect(uri, uri=True)) as db:
                db.execute("PRAGMA query_only=ON")
                db.execute("BEGIN")
                expected = [config.owner_qq_id, config.bot_qq_id,
                            config.origin, config.protocol_epoch]
                identity = db.execute("SELECT value FROM identity").fetchall()
                if len(identity) != 1 or strict_json(identity[0][0]) != expected:
                    raise ValueError("IDENTITY_UNVERIFIED")
                runtime = _ReadonlyRuntime(db, config, directory)
                records = runtime.verify(active=False)
                original = runtime.row()
                if original["state"] != "STOPPED":
                    raise ValueError("STOP_REQUIRED")
                references = [r["data"]["authority"] for r in records
                              if r["kind"] in {"OWNER_BOUND", "OWNER_REBOUND"}]
                try:
                    report["closed_custody_sha256"] = closed(runtime, records, references)
                except (ValueError, KeyError, TypeError):
                    issues["ORIGINAL_HANDOFF_NOT_CLOSED"] += 1
                tables = _inventory(db, issues)
                report["tables"] = tables
                if len(tables) == len(SCHEMA) and not issues["ROW_LIMIT"]:
                    _accounting(db, issues)
                    report["pins"] = _pins(db, records, clock(), issues)
                report["managed_copies"] = _copies(managed_copies)
                runtime.verify(active=False)
                if runtime.row() != original:
                    raise ValueError("CUSTODY_CHANGED")
                report["journal_head_sha256"] = original["journal_head"]
                report["authority_sha256"] = digest(config.owner_authority)
            if _fingerprints(directory, names, held_lock) != before:
                raise Refused("FILES_CHANGED")
    except Refused as error:
        for key in ("tables", "pins", "closed_custody_sha256", "journal_head_sha256",
                    "authority_sha256", "managed_copies"):
            report.pop(key, None)
        issues[error.args[0]] += 1
    except (ValueError, KeyError, TypeError, OSError, sqlite3.Error, OverflowError):
        # Never leak a parser exception containing metadata, paths or credentials.
        for key in ("tables", "pins", "closed_custody_sha256", "journal_head_sha256",
                    "authority_sha256", "managed_copies"):
            report.pop(key, None)
        issues["CUSTODY_PATH_SCHEMA_OR_RECEIPT_UNVERIFIED"] += 1
    report["reasons"] = dict(sorted((k, v) for k, v in issues.items() if v))
    if not report["reasons"]:
        report["local_state"] = "CLEAR"
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--managed-copy", action="append", type=Path, default=[])
    args = parser.parse_args()
    try:
        report = preflight(BridgeConfig.load(args.config), managed_copies=args.managed_copy)
    except (ValueError, KeyError, TypeError, OSError):
        report = {"version": 1, "state": "BLOCKED", "cutover_authorized": False,
                  "local_state": "BLOCKED", "reasons": {"CONFIG_UNVERIFIED": 1}}
    print(json.dumps(report, sort_keys=True))
    return 0 if report["local_state"] == "CLEAR" else 2


if __name__ == "__main__":
    raise SystemExit(main())
