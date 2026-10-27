"""Explicit stopped accounting sweep; never enter the native content host."""

import asyncio
import hashlib
import os
import sqlite3
from collections import Counter
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import httpx

from companion_meme_protocol import UsageReceipt, digest, parse, strict_json

from .instance_lock import acquire
from .meme_export import _file, _ReadonlyRuntime
from .meme_responsibility import get_responsibility
from .meme_retirement import MAX_ROWS, SCHEMA
from .meme_semantic import retain_usage_projection
from .network import private_http_client
from .owner_native_resolution import _stopped

COLUMNS = "request_sha,sha,source_sha,cache_key,job_id,state,usage_json"
TERMINAL = {"SUCCEEDED", "FAILED", "CANCELLED"}


class AccountingContext:
    def __init__(self, db, runtime, held, directory):
        self.db, self.runtime, self.held, self.directory = db, runtime, held, directory
        self.config, self.authority = runtime.config, dict(runtime.authority)
        self.writing = False
        self.db.set_authorizer(self.authorize)
        self.inventory = self.rows()
        if len(self.inventory) > MAX_ROWS:
            raise ValueError("ACCOUNTING_ROW_LIMIT")
        self.native = self.custody()
        self.index = 0
        self.counts = Counter(inventory=len(self.inventory))
        self.reasons = Counter()
        self.duplicates = Counter(row[4] for row in self.inventory if row[4] is not None)
        self.result_pending = db.execute(
            "SELECT COUNT(*) FROM meme_runtime_results WHERE state='PENDING'"
        ).fetchone()[0]
        if self.result_pending:
            self.reasons["RESULT_PENDING"] = self.result_pending

    def authorize(self, action, table, column, database, source):
        if (
            action == sqlite3.SQLITE_UPDATE
            and self.writing
            and table == "meme_caption_bindings"
            and column == "usage_json"
        ):
            return sqlite3.SQLITE_OK
        if action == sqlite3.SQLITE_PRAGMA and table == "table_info":
            return sqlite3.SQLITE_OK
        if action in {
            sqlite3.SQLITE_SELECT,
            sqlite3.SQLITE_READ,
            sqlite3.SQLITE_FUNCTION,
            sqlite3.SQLITE_TRANSACTION,
            sqlite3.SQLITE_RECURSIVE,
        }:
            return sqlite3.SQLITE_OK
        return sqlite3.SQLITE_DENY

    def rows(self):
        return self.db.execute(
            "SELECT " + COLUMNS + " FROM meme_caption_bindings ORDER BY request_sha LIMIT ?",
            (MAX_ROWS + 1,),
        ).fetchall()

    def custody(self):
        runtime = self.runtime
        if (
            runtime.db is not self.db
            or runtime.config != self.config
            or runtime.authority != self.authority
            or runtime.path != self.directory / "owner-journal.jsonl"
        ):
            raise ValueError("ACCOUNTING_CUSTODY_CHANGED")
        lock = _file(self.directory / "instance.lock").stat()
        opened = os.fstat(self.held.fileno())
        if (lock.st_dev, lock.st_ino) != (opened.st_dev, opened.st_ino):
            raise ValueError("ACCOUNTING_CUSTODY_CHANGED")
        database = _file(self.directory / "spool.sqlite3").stat()
        _file(runtime.path)
        _stopped(runtime)
        captures = self.db.execute(
            "SELECT event_id,input_hash,approval_hash,state,"
            "capture_json,plan_hash FROM window_inputs ORDER BY event_id"
        ).fetchall()
        return (
            runtime.row(),
            hashlib.sha256(runtime.path.read_bytes()).hexdigest(),
            captures,
            (database.st_dev, database.st_ino),
        )

    def verify(self, inventory=None):
        if self.custody() != self.native or self.rows() != (
            self.inventory if inventory is None else inventory
        ):
            raise ValueError("ACCOUNTING_CUSTODY_CHANGED")

    def retain(self, index, value):
        receipt = parse(UsageReceipt, value["usage"]) if value["usage"] is not None else None
        self.db.execute("BEGIN IMMEDIATE")
        try:
            self.verify()
            row = self.inventory[index]
            # The approved getter has bound all five original request/cache identities.
            if row[4] != value["job_id"]:
                raise ValueError("MEME_CAPTION_JOB_MISMATCH")
            expected = list(self.inventory)
            if receipt is not None and receipt.model_dump_json() != row[6]:
                self.writing = True
                try:
                    retain_usage_projection(self.db, row[0], row[6], receipt)
                finally:
                    self.writing = False
                expected[index] = (*row[:6], receipt.model_dump_json())
            self.verify(expected)
            self.db.commit()
            self.inventory = expected
            return receipt is not None and row[6] != expected[index][6]
        except BaseException:
            self.db.rollback()
            raise

    def report(self):
        remaining = len(self.inventory) - self.index
        counts = {
            key: self.counts[key]
            for key in (
                "inventory",
                "attempted",
                "updated",
                "settled",
                "unknown",
                "reserved",
                "no_admission",
                "cancelled",
                "unverified",
            )
        }
        state = (
            "UNVERIFIED"
            if counts["unverified"] or remaining
            else ("PENDING" if self.reasons else "COMPLETE")
        )
        return {
            "version": 1,
            "state": state,
            **counts,
            "remaining": remaining,
            "result_pending": self.result_pending,
            "reasons": dict(sorted(self.reasons.items())),
            "custody_sha256": digest(self.native),
            "cutover_authorized": False,
        }


@contextmanager
def open_existing_accounting_store(config):
    directory = Path(config.data_dir).absolute()
    database, lock = (_file(directory / name) for name in ("spool.sqlite3", "instance.lock"))
    with acquire(lock, existing_only=True) as held:
        for suffix in ("-wal", "-journal", "-shm"):
            sidecar = Path(str(database) + suffix)
            if sidecar.exists() and _file(sidecar).stat().st_size:
                raise ValueError("ACCOUNTING_RECOVERY_REQUIRED")
        db = sqlite3.connect(
            database.as_uri() + "?mode=rw", uri=True, isolation_level=None, cached_statements=0
        )
        try:
            identity = [config.owner_qq_id, config.bot_qq_id, config.origin, config.protocol_epoch]
            if config.astrbot_handoff_path is not None:
                identity.append(config.astrbot_handoff_sha256)
            saved = db.execute("SELECT value FROM identity").fetchall()
            if len(saved) != 1 or strict_json(saved[0][0]) != identity:
                raise ValueError("BRIDGE_IDENTITY_CHANGED")
            for name in ("meme_caption_bindings", "meme_runtime_results"):
                kind = db.execute("SELECT type FROM sqlite_master WHERE name=?", (name,)).fetchone()
                columns = db.execute("PRAGMA table_info(" + name + ")").fetchall()
                shape = " ".join(r[1] + ":" + r[2] for r in columns)
                if kind != ("table",) or shape != SCHEMA[name]:
                    raise ValueError("ACCOUNTING_SCHEMA_UNVERIFIED")
            runtime = _ReadonlyRuntime(db, config, directory)
            runtime.spool.lock = held
            runtime.spool.quiesced_at = datetime.now(UTC).isoformat()
            yield AccountingContext(db, runtime, held, directory)
        finally:
            db.close()


async def drain_pass(context, client, *, max_jobs=8, max_seconds=2.0, request_seconds=0.5):
    if (
        type(max_jobs) is not int
        or not 1 <= max_jobs <= 8
        or not 0 < max_seconds <= 2
        or not 0 < request_seconds <= 0.5
    ):
        raise ValueError("ACCOUNTING_LIMIT_INVALID")
    deadline = asyncio.get_running_loop().time() + max_seconds
    start = context.index
    http = SimpleNamespace(window=context.runtime, client=client)
    catalog = SimpleNamespace(db=context.db)
    context.verify()
    while context.index < len(context.inventory) and context.index - start < max_jobs:
        remaining = deadline - asyncio.get_running_loop().time()
        if remaining <= 0:
            break
        index, row = context.index, context.inventory[context.index]
        context.index += 1
        context.counts["attempted"] += 1
        if row[4] is None or context.duplicates[row[4]] != 1:
            context.counts["unverified"] += 1
            context.reasons["ACCOUNTING_BINDING_UNVERIFIED"] += 1
            continue
        try:
            async with asyncio.timeout(min(request_seconds, remaining)):
                value = await get_responsibility(http, catalog, row[4])
            context.verify()
            if asyncio.current_task().cancelling():
                raise asyncio.CancelledError
            context.counts["updated"] += context.retain(index, value)
            if value["job_state"] == "CANCELLED":
                context.counts["cancelled"] += 1
            if value["liability_kind"] == "NO_ADMISSION":
                context.counts["no_admission"] += 1
            else:
                state = value["usage"]["state"]
                context.counts[state.lower()] += 1
                if state != "SETTLED":
                    context.reasons["CAPTION_USAGE_" + state] += 1
            if value["job_state"] not in TERMINAL:
                context.reasons["CAPTION_WORK_PENDING"] += 1
        except (ValueError, httpx.HTTPError, TimeoutError):
            context.verify()  # Any changed native or whole-inventory custody aborts the sweep.
            context.counts["unverified"] += 1
            context.reasons["CAPTION_ACCOUNTING_UNVERIFIED"] += 1
    return context.index - start


async def command(config, args):
    context = None
    try:
        with open_existing_accounting_store(config) as context:
            async with private_http_client(config.core_url, config.bridge_token) as client:
                while context.index < len(context.inventory):
                    if not await drain_pass(context, client):
                        context.reasons["ACCOUNTING_PASS_BUDGET_EXHAUSTED"] += 1
                        break
                    await asyncio.sleep(0)
            context.verify()
            return context.report()
    except Exception:
        # Fixed codes only; native parser/transport exceptions may contain private data.
        if context is not None:
            context.counts["unverified"] += 1
            context.reasons["ACCOUNTING_CUSTODY_UNVERIFIED"] += 1
            return context.report()
        return {
            "version": 1,
            "state": "UNVERIFIED",
            "inventory": None,
            "attempted": 0,
            "updated": 0,
            "settled": 0,
            "unknown": 0,
            "reserved": 0,
            "no_admission": 0,
            "cancelled": 0,
            "unverified": 1,
            "remaining": None,
            "result_pending": None,
            "custody_sha256": None,
            "reasons": {"ACCOUNTING_CUSTODY_UNVERIFIED": 1},
            "cutover_authorized": False,
        }
