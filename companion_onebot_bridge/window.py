"""One-shot test windows. Receipts extend Spool; there is no second delivery queue.

The separate fsynced journal detects missing/older Spool state. Neither artifact is
an authorization signature: live operation trusts independently approved private
files and deployment attestation. Losing/rolling back both is a recovery blocker.
"""

import hashlib
import json
import os
import re
import sqlite3
import stat
from dataclasses import asdict, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import urlsplit

import httpx
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from . import cumulative
from .event_normalizer import decode, normalize
from .network import checked_http_client, private_http_client, private_request_matches
from .onebot_client import OneBotClient, bounded_post
from .outbound import deliver_tick
from .spool import Spool, encode

SHA = re.compile(r"[a-f0-9]{64}")
ACTIVE = {"PENDING", "LEASED", "SENDING", "RETRY_WAIT", "DELIVERY_UNCERTAIN"}
HANDOFF_DOMAIN = b"companion-core/window-handoff/v1\x00"


def digest(value):
    return hashlib.sha256(encode(value).encode()).hexdigest()


def bridge_code_sha256():
    package = Path(__file__).parent
    return digest([[p.name, hashlib.sha256(p.read_bytes()).hexdigest()]
                   for p in sorted(package.glob("*.py"))])


def private_json(path, max_bytes=65536):
    target = Path(path)
    info = target.lstat()
    if (
        not stat.S_ISREG(info.st_mode)
        or target.is_symlink()
        or (os.name != "nt" and info.st_mode & 0o077)
        or info.st_size > max_bytes
    ):
        raise ValueError("PRIVATE_WINDOW_FILE_REQUIRED")
    return decode(target.read_bytes(), max_bytes)


def stop_path(directory, value, plan_hash):
    return Path(directory) / (
        f"window-{plan_hash}.stop" if value.get("live_text", {}).get("handoff") else "window.stop"
    )


def request_stop(directory, *, plan=None):
    directory = Path(directory)
    if directory.is_symlink() or not directory.is_dir() or (
        os.name != "nt" and directory.stat().st_mode & 0o077
    ):
        raise ValueError("PRIVATE_DATA_DIRECTORY_REQUIRED")
    if plan is None:
        # A WAL reader does not need the receiver's OS-held Spool lock.
        database = directory / "spool.sqlite3"
        if database.is_symlink() or not database.is_file():
            raise ValueError("WINDOW_NOT_FOUND")
        with sqlite3.connect(database.resolve().as_uri() + "?mode=ro", uri=True, timeout=1) as db:
            row = db.execute(
                "SELECT plan_hash,plan_json FROM test_window WHERE singleton=1"
            ).fetchone()
        if not row or digest(value := json.loads(row[1])) != row[0]:
            raise ValueError("WINDOW_PLAN_MISMATCH")
        plan_hash = row[0]
    else:
        value, plan_hash = plan.value, plan.sha256
    path = stop_path(directory, value, plan_hash)
    try:
        descriptor = os.open(
            path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600
        )
    except FileExistsError:
        if path.is_symlink() or path.read_bytes() != b"OWNER_STOP\n":
            raise ValueError("INVALID_WINDOW_STOP_REQUEST") from None
        return
    with os.fdopen(descriptor, "wb") as handle:
        handle.write(b"OWNER_STOP\n")
        handle.flush()
        os.fsync(handle.fileno())
    if os.name != "nt":
        descriptor = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)


def instant(value):
    if not isinstance(value, str):
        raise ValueError("ABSOLUTE_SHANGHAI_TIME_REQUIRED")
    parsed = datetime.fromisoformat(value)
    if parsed.utcoffset() != timedelta(hours=8):
        raise ValueError("ABSOLUTE_SHANGHAI_TIME_REQUIRED")
    return parsed


def counts(rows):
    if not isinstance(rows, list):
        raise ValueError("INVALID_CORE_COUNTS")
    result = {}
    for row in rows:
        if (
            not isinstance(row, dict)
            or set(row) != {"state", "count"}
            or not isinstance(row["state"], str)
            or row["state"] in result
            or type(row["count"]) is not int
            or row["count"] < 0
        ):
            raise ValueError("INVALID_CORE_COUNTS")
        result[row["state"]] = row["count"]
    return result


def stored_plan(encoded, config):
    """Read retired authority without re-authorizing its expired configuration."""
    plan = object.__new__(WindowPlan)
    plan.value, plan.config = json.loads(encoded), config
    plan.sha256 = digest(plan.value)
    plan.mode = plan.value.get("mode", "demo")
    plan.journal = Path(plan.value["journal_path"])
    plan.start, plan.end = instant(plan.value["starts_at"]), instant(plan.value["ends_at"])
    return plan


class WindowPlan:
    def __init__(self, value, config, inventory, authorization=None):
        fields = {
            "window_id",
            "timezone",
            "starts_at",
            "ends_at",
            "protocol_epoch",
            "identity_sha256",
            "config_sha256",
            "config_version",
            "image_ids",
            "runtime_attestation_sha256",
            "authorization_sha256",
            "journal_path",
            "max_new_inputs",
            "max_attempts",
            "max_visible_replies",
            "concurrency",
            "max_input_chars",
            "max_reply_chars",
            "execution",
        }
        if not isinstance(value, dict):
            raise ValueError("INVALID_WINDOW_PLAN")
        self.mode = value.get("mode", "demo")
        if self.mode == "live_text":
            fields |= {"mode", "live_text"}
        elif self.mode != "demo":
            raise ValueError("INVALID_WINDOW_MODE")
        if set(value) != fields:
            raise ValueError("INVALID_WINDOW_PLAN")
        self.value, self.config = value, config
        self.sha256 = digest(value)
        if not re.fullmatch(r"win_[a-f0-9]{32}", value["window_id"]):
            raise ValueError("INVALID_WINDOW_ID")
        if value["timezone"] != "Asia/Shanghai":
            raise ValueError("ABSOLUTE_SHANGHAI_TIME_REQUIRED")
        self.start, self.end = instant(value["starts_at"]), instant(value["ends_at"])
        if not self.start < self.end:
            raise ValueError("INVALID_WINDOW_INTERVAL")
        if self.mode == "live_text":
            scope = value["live_text"]
            expected_scope = {
                "live_approved",
                "execution",
                "window_id",
                "starts_at",
                "ends_at",
                "core_config_sha256",
                "core_code_sha256",
                "bridge_code_sha256",
                "candidate_sha",
                "paid_contract_sha256",
                "budget_journal_id",
                "custodian_sha256",
                "approval_sha256",
                "max_inputs",
                "max_model_attempts",
                "max_input_chars",
                "max_reply_chars",
                "capture_public_key",
                "history_scope",
                "max_history_turns",
                "max_history_utf8_bytes",
                "memory_scope",
                "memory_facts",
                "memory_approval_sha256",
                "handoff",
            }
            if not isinstance(scope, dict) or scope.get("live_approved") is not True:
                raise ValueError("LIVE_TEXT_APPROVAL_REQUIRED")
            if "cumulative" in scope:
                expected_scope.add("cumulative")
                cumulative.validate(scope["cumulative"], config, self.start, self.end)
            # Retired plain scopes retain their original bytes/hash; new scopes may opt in.
            if "rich_input_scope" in scope:
                expected_scope.add("rich_input_scope")
            if scope.get("rich_input_scope", "none") not in ("none", "references_v1"):
                raise ValueError("INVALID_RICH_INPUT_SCOPE")
            for field in ("image_scope", "image_approval_sha256"):
                if field in scope:
                    expected_scope.add(field)
            if (
                scope.get("image_scope", "none") not in ("none", "owner_image_v1")
                or (
                    scope.get("image_scope", "none") == "none"
                    and scope.get("image_approval_sha256") is not None
                )
                or (
                    scope.get("image_scope") == "owner_image_v1"
                    and (
                        scope.get("rich_input_scope") != "references_v1"
                        or scope["history_scope"] != "none"
                        or scope["memory_scope"] != "none"
                        or not isinstance(scope.get("image_approval_sha256"), str)
                        or not SHA.fullmatch(scope["image_approval_sha256"])
                        or not scope["capture_public_key"]
                    )
                )
            ):
                raise ValueError("INVALID_IMAGE_SCOPE")
            # Old history-only authority remains byte exact; no new grant is inferred.
            if set(scope) - {"history_approval_sha256"} != expected_scope or any(
                not isinstance(scope[k], str) or not SHA.fullmatch(scope[k])
                for k in expected_scope
                if k.endswith("_sha256")
                and k not in {"memory_approval_sha256", "image_approval_sha256"}
            ):
                raise ValueError("INVALID_LIVE_TEXT_SCOPE")
            facts = scope["memory_facts"]
            if (
                scope["memory_scope"] not in {"none", "approved_facts"}
                or not isinstance(facts, list)
                or len(facts) > 6
                or (
                    scope["memory_scope"] == "none"
                    and (facts or scope["memory_approval_sha256"] is not None)
                )
                or (
                    scope["memory_scope"] == "approved_facts"
                    and (
                        not facts
                        or not isinstance(scope["memory_approval_sha256"], str)
                        or not SHA.fullmatch(scope["memory_approval_sha256"])
                        or not scope["capture_public_key"]
                    )
                )
                or any(
                    not isinstance(fact, dict)
                    or set(fact)
                    != {
                        "memory_id",
                        "subject",
                        "status",
                        "content_sha256",
                        "evidence_sha256",
                        "memory_sha256",
                    }
                    or not isinstance(fact["memory_id"], str)
                    or not re.fullmatch(r"mem_[a-f0-9]{32}", fact["memory_id"])
                    or fact["subject"] not in {"user", "project"}
                    or fact["status"] != "CONFIRMED"
                    or any(
                        not isinstance(fact[k], str) or not SHA.fullmatch(fact[k])
                        for k in ("content_sha256", "evidence_sha256", "memory_sha256")
                    )
                    for fact in facts
                )
                or len({fact["memory_id"] for fact in facts}) != len(facts)
            ):
                raise ValueError("INVALID_READONLY_MEMORY_SCOPE")
            history_approval = scope.get("history_approval_sha256")
            if (
                history_approval is not None
                and (
                    not isinstance(history_approval, str)
                    or not SHA.fullmatch(history_approval)
                    or scope["history_scope"] == "none"
                )
            ) or (
                scope["history_scope"] == "approved_window"
                and scope["memory_scope"] == "approved_facts"
                and not history_approval
            ):
                raise ValueError("SEPARATE_HISTORY_APPROVAL_REQUIRED")
            handoff = scope["handoff"]
            if handoff is not None and (
                not isinstance(handoff, dict)
                or set(handoff)
                != {
                    "predecessor_plan_sha256",
                    "predecessor_window_id",
                    "budget_journal_head_sha256",
                    "bridge_journal_head_sha256",
                    "identity_sha256",
                    "protocol_epoch",
                    "privacy_epoch",
                    "erasure_ledger_sha256",
                    "evidence_sha256",
                }
                or any(
                    not isinstance(handoff[key], str) or not SHA.fullmatch(handoff[key])
                    for key in handoff
                    if key.endswith("_sha256")
                )
                or not re.fullmatch(r"win_[a-f0-9]{32}", handoff["predecessor_window_id"])
                or type(handoff["privacy_epoch"]) is not int
                or handoff["privacy_epoch"] < 0
                or handoff["identity_sha256"] != value["identity_sha256"]
                or handoff["protocol_epoch"] != value["protocol_epoch"]
                or not config.capture_signing_key
            ):
                raise ValueError("INVALID_WINDOW_HANDOFF")
            if (
                scope["history_scope"] not in ("none", "approved_window")
                or type(scope["max_history_turns"]) is not int
                or type(scope["max_history_utf8_bytes"]) is not int
                or not 0 <= scope["max_history_turns"] <= 4
                or not 0 <= scope["max_history_utf8_bytes"] <= 16000
                or (
                    scope["history_scope"] == "none"
                    and (scope["max_history_turns"] or scope["max_history_utf8_bytes"])
                )
                or (
                    scope["history_scope"] == "approved_window"
                    and not (
                        scope["max_history_turns"]
                        and scope["max_history_utf8_bytes"]
                        and scope["capture_public_key"]
                    )
                )
                or scope["bridge_code_sha256"] != bridge_code_sha256()
                or scope["capture_public_key"] != config.capture_public_key
                or scope["execution"] != value["execution"]
                or scope["window_id"] != value["window_id"]
                or instant(scope["starts_at"]) != self.start
                or instant(scope["ends_at"]) != self.end
                or not re.fullmatch(r"[a-f0-9]{40}", scope["candidate_sha"])
                or not re.fullmatch(r"budget_[a-f0-9]{32}", scope["budget_journal_id"])
                or type(scope["max_model_attempts"]) is not int
                or not 1 <= scope["max_model_attempts"] <= 5
                or any(
                    type(scope[key]) is not int or scope[key] != value[other]
                    for key, other in (
                        ("max_inputs", "max_new_inputs"),
                        ("max_input_chars", "max_input_chars"),
                        ("max_reply_chars", "max_reply_chars"),
                    )
                )
            ):
                raise ValueError("LIVE_TEXT_SCOPE_MISMATCH")
        for field in ("max_new_inputs", "max_attempts", "max_visible_replies"):
            if type(value[field]) is not int or not 1 <= value[field] <= 5:
                raise ValueError("INVALID_WINDOW_LIMIT")
        if type(value["concurrency"]) is not int or value["concurrency"] != 1:
            raise ValueError("INVALID_WINDOW_LIMIT")
        for field in ("max_input_chars", "max_reply_chars"):
            if type(value[field]) is not int or not 1 <= value[field] <= 500:
                raise ValueError("INVALID_WINDOW_TEXT_LIMIT")
        for field in (
            "identity_sha256",
            "config_sha256",
            "runtime_attestation_sha256",
            "authorization_sha256",
        ):
            if not isinstance(value[field], str) or not SHA.fullmatch(value[field]):
                raise ValueError("INVALID_WINDOW_DIGEST")
        images = value["image_ids"]
        if (
            not isinstance(images, dict)
            or set(images) != {"core", "bridge", "protocol"}
            or any(
                not isinstance(image, str) or not re.fullmatch(r"sha256:[a-f0-9]{64}", image)
                for image in images.values()
            )
        ):
            raise ValueError("PINNED_RUNTIME_IMAGES_REQUIRED")
        if not isinstance(value["config_version"], str) or not re.fullmatch(
            r"[A-Za-z0-9_-]{1,64}", value["config_version"]
        ):
            raise ValueError("INVALID_CONFIG_VERSION")
        if (
            config.delivery_enabled
            or value["protocol_epoch"] != config.protocol_epoch
            or value["identity_sha256"] != digest([config.owner_qq_id, config.bot_qq_id])
            or value["config_sha256"] != digest(asdict(config))
        ):
            raise ValueError("WINDOW_CONFIG_MISMATCH")
        expected = {
            key: value[key]
            for key in (
                "identity_sha256",
                "protocol_epoch",
                "config_sha256",
                "config_version",
                "image_ids",
            )
        }
        if inventory != expected or digest(inventory) != value["runtime_attestation_sha256"]:
            raise ValueError("RUNTIME_ATTESTATION_MISMATCH")
        journal = Path(value["journal_path"])
        if (
            not journal.is_absolute()
            or journal.resolve().is_relative_to(Path(config.data_dir).resolve())
            or journal.resolve() != journal.absolute()
            or not journal.parent.is_dir()
            or journal.parent.is_symlink()
            or (os.name != "nt" and journal.parent.stat().st_mode & 0o077)
        ):
            raise ValueError("INDEPENDENT_WINDOW_JOURNAL_REQUIRED")
        self.journal = journal
        if value["execution"] == "local_synthetic":
            if any(
                urlsplit(url).hostname not in {"127.0.0.1", "::1", "localhost"}
                for url in (config.core_url, config.onebot_http_url, config.onebot_ws_url)
            ):
                raise ValueError("SYNTHETIC_LOOPBACK_REQUIRED")
        elif value["execution"] == "approved_live":
            if config.capture_signing_key is None:
                raise ValueError("INDEPENDENT_CAPTURE_SIGNING_KEY_REQUIRED")
            scope = {key: val for key, val in value.items() if key != "authorization_sha256"}
            if (
                not isinstance(authorization, dict)
                or set(authorization) != {"kind", "approved", "scope_sha256", "evidence_sha256"}
                or authorization["kind"] != "owner_qq_window"
                or authorization["approved"] is not True
                or authorization["scope_sha256"] != digest(scope)
                or not isinstance(authorization["evidence_sha256"], str)
                or not SHA.fullmatch(authorization["evidence_sha256"])
                or digest(authorization) != value["authorization_sha256"]
            ):
                raise ValueError("NEW_WINDOW_AUTHORIZATION_REQUIRED")
        else:
            raise ValueError("INVALID_WINDOW_EXECUTION_MODE")


class TestWindow:
    """Exclusive Spool owner. Every irreversible allowance is journaled before I/O."""

    __test__ = False

    def __init__(self, spool, plan, *, clock=None):
        self.spool, self.db, self.plan = spool, spool.db, plan
        self.db.row_factory = sqlite3.Row
        self.clock = clock or (lambda: datetime.now(UTC))
        self.busy = False
        self.model_attempts = None
        self.core_stop_ack = False
        self.stop_path = stop_path(spool.directory, plan.value, plan.sha256)

    def row(self):
        self.db.row_factory = sqlite3.Row
        row = self.db.execute("SELECT * FROM test_window WHERE singleton=1").fetchone()
        if not row or row["plan_hash"] != self.plan.sha256:
            raise ValueError("WINDOW_IMMUTABLE_OR_MISSING")
        return row

    def ensure_schema(self):
        # Legacy ownership is assigned only after the current plan and journal match.
        # New inserts are explicit; event/message uniqueness remains global.
        missing = []
        for table in ("window_inputs", "window_attempts"):
            columns = {row[1] for row in self.db.execute(f"PRAGMA table_info({table})")}
            if not columns:
                raise ValueError("WINDOW_SCHEMA_MISSING")
            if "plan_hash" not in columns:
                missing.append(table)
        history = self.db.execute(
            "SELECT 1 FROM sqlite_master WHERE name='window_history'"
        ).fetchone()
        if not missing and history:
            return
        if self.db.execute("SELECT COUNT(*) FROM test_window").fetchone()[0] != 1:
            raise ValueError("WINDOW_SCHEMA_AMBIGUOUS")
        self.db.execute("SAVEPOINT window_ownership")
        try:
            for table in missing:
                self.db.execute(
                    f"ALTER TABLE {table} ADD COLUMN plan_hash TEXT NOT NULL "
                    f"DEFAULT '{self.plan.sha256}'"
                )
            self.db.execute(
                "CREATE TABLE IF NOT EXISTS window_history(plan_hash TEXT PRIMARY KEY,"
                "record_json TEXT NOT NULL,retirement_head TEXT NOT NULL,"
                "successor_hash TEXT NOT NULL)"
            )
        except BaseException:
            self.db.execute("ROLLBACK TO window_ownership")
            raise
        finally:
            self.db.execute("RELEASE window_ownership")

    def count(self, table, condition="1=1"):
        if table not in {"window_inputs", "window_attempts"}:
            raise ValueError("INVALID_WINDOW_TABLE")
        return self.db.execute(
            f"SELECT COUNT(*) FROM {table} WHERE plan_hash=? AND ({condition})",
            (self.plan.sha256,),
        ).fetchone()[0]

    def verify_history(self, records):
        plan, journal = self.plan, records
        history = [(plan, journal)]
        seen = set()
        while scope := plan.value.get("live_text", {}).get("handoff"):
            predecessor = scope["predecessor_plan_sha256"]
            if predecessor in seen:
                raise ValueError("WINDOW_HISTORY_MISMATCH")
            seen.add(predecessor)
            saved = self.db.execute(
                "SELECT * FROM window_history WHERE plan_hash=?", (predecessor,)
            ).fetchone()
            if not saved or saved["successor_hash"] != plan.sha256:
                raise ValueError("WINDOW_HISTORY_MISMATCH")
            snapshot = json.loads(saved["record_json"])
            old = stored_plan(snapshot["plan_json"], self.plan.config)
            old_records, old_head = TestWindow(self.spool, old, clock=self.clock).read_journal()
            if (
                old.sha256 != predecessor or old_head != saved["retirement_head"]
                or not old_records or old_records[-1]["kind"] != "RETIRED"
                or old_records[-1]["data"].get("snapshot_sha256") != digest(snapshot)
                or old_records[-1]["data"].get("successor_window_sha256") != plan.sha256
                or not journal or journal[0]["kind"] != "HANDOFF_PREPARED"
                or journal[0]["data"].get("retirement_head") != old_head
                or journal[0]["data"].get("predecessor_window_sha256") != predecessor
                or journal[0]["data"].get("handoff_sha256") != digest(scope)
            ):
                raise ValueError("WINDOW_HISTORY_MISMATCH")
            marker = stop_path(self.spool.directory, old.value, old.sha256)
            if (marker.is_symlink() or not marker.is_file()
                    or marker.read_bytes() != b"OWNER_STOP\n"):
                raise ValueError("WINDOW_STOP_EVIDENCE_MISSING")
            plan, journal = old, old_records
            history.append((plan, journal))
        return history

    def verify(self, *, active=True, dispatch=False, capture_fence=None):
        if digest(self.plan.value) != self.plan.sha256:
            raise ValueError("WINDOW_IMMUTABLE_OR_MISSING")
        if (active or dispatch) and (
            digest(asdict(self.plan.config)) != self.plan.value["config_sha256"]
            or (self.plan.mode == "live_text" and (
                self.plan.value["live_text"]["bridge_code_sha256"] != bridge_code_sha256()
            ))
        ):
            raise ValueError("LIVE_TEXT_RUNTIME_CHANGED")
        if (active or dispatch) and self.stop_path.exists():
            raise ValueError("WINDOW_STOPPED")
        row = self.row()
        records, head = self.read_journal()
        if not records or head != row["journal_head"]:
            raise ValueError("WINDOW_JOURNAL_RECONCILIATION_REQUIRED")
        self.ensure_schema()
        quota = cumulative.fold(self, self.verify_history(records))
        if active or dispatch:
            cumulative.check(self, quota, self.clock())
        if active:
            capturing = (capture_fence is not None and row["state"] == "CAPTURING"
                         and row["fence"] == capture_fence)
            if row["state"] == "CAPTURING" and not capturing:
                raise ValueError("CAPTURE_RECONCILIATION_REQUIRED")
            if row["state"] != "OPEN" and not capturing:
                raise ValueError("WINDOW_STOPPED")
            unknown = self.count("window_attempts", "state!='ACK'")
            if unknown:
                raise ValueError("WINDOW_ATTEMPT_RECONCILIATION_REQUIRED")
            if not self.plan.start <= self.clock() < self.plan.end:
                raise ValueError("WINDOW_OUTSIDE_INTERVAL")
        return row

    def read_journal(self):
        path = self.plan.journal
        info = path.lstat()
        if (
            path.is_symlink()
            or not stat.S_ISREG(info.st_mode)
            or info.st_size > 1048576
            or (os.name != "nt" and info.st_mode & 0o077)
        ):
            raise ValueError("INVALID_WINDOW_JOURNAL")
        records, head = [], "0" * 64
        for line in path.read_text("utf-8").splitlines():
            record = decode(line, 65536)
            if record.get("previous") != head or record.get("seq") != len(records) + 1:
                raise ValueError("INVALID_WINDOW_JOURNAL")
            head = digest(record)
            records.append(record)
        return records, head

    def append(self, kind, data, *, create=False):
        if not create and kind in {"CAPTURED", "INPUT", "RESERVED"}:
            cumulative.admit(self, "send_attempts" if kind == "RESERVED" else "owner_inputs",
                             data["message_hash"] if kind == "RESERVED" else data["event_hash"])
        records, previous = ([], "0" * 64) if create else self.read_journal()
        if not create and previous != self.row()["journal_head"]:
            raise ValueError("WINDOW_JOURNAL_RECONCILIATION_REQUIRED")
        record = {
            "seq": len(records) + 1,
            "previous": previous,
            "at": self.clock().isoformat(),
            "kind": kind,
            "data": data,
        }
        flags = os.O_WRONLY | (os.O_CREAT | os.O_EXCL if create else os.O_APPEND)
        flags |= getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(self.plan.journal, flags, 0o600)
        with os.fdopen(descriptor, "ab") as handle:
            handle.write((encode(record) + "\n").encode())
            handle.flush()
            os.fsync(handle.fileno())
        # Persist the newly created directory entry on POSIX too.
        if create and os.name != "nt":
            directory = os.open(self.plan.journal.parent, os.O_RDONLY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        return digest(record)

    async def core_counts(self, client, *, initial=False):
        if self.plan.mode == "live_text":
            self.check_core_client(client)
        content = bytearray()
        async with self.core_http(client) as scoped:
            async with scoped.stream("GET", "/internal/v1/ops/status") as response:
                response.raise_for_status()
                async for chunk in response.aiter_bytes(chunk_size=8192):
                    content.extend(chunk)
                    if len(content) > 65536:
                        raise ValueError("INVALID_CORE_COUNTS")
        status = decode(bytes(content), 65536)
        deliveries, jobs = counts(status.get("deliveries")), counts(status.get("jobs"))
        readonly_memory = (
            self.plan.mode == "live_text"
            and self.plan.value["live_text"]["memory_scope"] == "approved_facts"
        )
        owner_image = (
            self.plan.mode == "live_text"
            and self.plan.value["live_text"].get("image_scope") == "owner_image_v1"
        )
        closed = (
            isinstance(status.get("features"), dict)
            and all(
                value is (readonly_memory if key == "memory" else owner_image if key == "content"
                          else False)
                for key, value in status["features"].items()
            )
            and (not readonly_memory or status["features"].get("memory") is True)
            and (not owner_image or status["features"].get("content") is True)
        )
        if self.plan.mode == "live_text":
            live = status.get("live_text_window", {})
            if (
                status.get("mode") != "live"
                or not closed
                or live.get("approved") is not True
                or live.get("plan_sha256") != self.plan.sha256
                or live.get("scope_sha256") != digest(self.plan.value["live_text"])
                or type(live.get("model_attempts")) is not int
                or not 0
                <= live["model_attempts"]
                <= self.plan.value["live_text"]["max_model_attempts"]
            ):
                raise ValueError("LIVE_TEXT_CORE_APPROVAL_REQUIRED")
            self.model_attempts = live["model_attempts"]
        elif (
            status.get("mode") != "demo"
            or not isinstance(status.get("features"), dict)
            or any(value is not False for value in status["features"].values())
        ):
            raise ValueError("DEMO_CLOSED_FEATURES_REQUIRED")
        allowed = {"SENT", "FAILED", "CANCELLED", *ACTIVE}
        if set(deliveries) - allowed:
            raise ValueError("UNKNOWN_CORE_DELIVERY_STATE")
        if any(deliveries.get(state, 0) for state in ACTIVE - {"PENDING"}):
            raise ValueError("CORE_DELIVERY_RECONCILIATION_REQUIRED")
        if initial:
            if deliveries.get("PENDING", 0) or any(
                jobs.get(state, 0) for state in {"QUEUED", "RUNNING", "RETRY_WAIT"}
            ):
                raise ValueError("INITIAL_PENDING_MUST_BE_ZERO")
        else:
            baseline = json.loads(self.row()["initial_counts"])
            acked = self.count("window_attempts", "state='ACK'")
            for state in ("SENT", "FAILED", "CANCELLED"):
                if deliveries.get(state, 0) != baseline.get(state, 0) + (
                    acked if state == "SENT" else 0
                ):
                    raise ValueError("CORE_DELIVERY_COUNTS_CHANGED")
            if deliveries.get("PENDING", 0) > 1:
                raise ValueError("UNATTENDED_CORE_DELIVERY")
        return deliveries

    def check_core_client(self, client):
        if (
            str(client.base_url).rstrip("/") != self.plan.config.core_url.rstrip("/")
            or client.headers.get("Authorization") != "Bearer " + self.plan.config.bridge_token
        ):
            raise ValueError("LIVE_TEXT_CORE_ENDPOINT_CHANGED")

    async def check_core_request(self, request):
        if (
            not (
                request.url.path.startswith("/internal/v1/")
                or (
                    self.plan.value.get("live_text", {}).get("image_scope") == "owner_image_v1"
                    and re.fullmatch(
                        r"/internal/v2/content/evt_[a-f0-9]{64}/image(?:/claim|/check)?",
                        request.url.path,
                    )
                )
            )
            or request.headers.get("Authorization") != "Bearer " + self.plan.config.bridge_token
            or not private_request_matches(
                request,
                self.plan.config.core_url,
                request.url.path,
                synthetic=self.plan.value["execution"] == "local_synthetic",
            )
        ):
            raise ValueError("LIVE_TEXT_CORE_ENDPOINT_CHANGED")

    def core_http(self, client):
        self.check_core_client(client)
        return checked_http_client(client, self.check_core_request)

    async def core_post(self, client, path, body):
        async with self.core_http(client) as scoped:
            return await bounded_post(scoped, path, body)

    async def stop_core(self, client):
        if self.plan.mode != "live_text":
            return
        try:
            self.verify(active=False)
            records, _ = self.read_journal()
            self.core_stop_ack = any(r.get("kind") == "CORE_STOP_CONFIRMED" for r in records)
            if self.core_stop_ack:
                return
            self.check_core_client(client)
            result = await self.core_post(client, "/internal/v1/live-text/stop", {
                "window_sha256": self.plan.sha256,
            })
            if result != {"stopped": True}:
                raise ValueError("LIVE_TEXT_CORE_STOP_UNCONFIRMED")
            head = self.append("CORE_STOP_CONFIRMED", {})
            with self.db:
                self.db.execute("UPDATE test_window SET journal_head=?", (head,))
            self.core_stop_ack = True
        except Exception:
            raise ValueError("LIVE_TEXT_CORE_STOP_UNCONFIRMED") from None

    async def stop_live(self, client, reason="OWNER_STOP"):
        if self.plan.mode != "live_text":
            raise ValueError("LIVE_TEXT_WINDOW_REQUIRED")
        self.stop(reason)
        await self.stop_core(client)
        return self.status()

    def check_opening(self):
        if digest(self.plan.value) != self.plan.sha256:
            raise ValueError("WINDOW_IMMUTABLE_OR_MISSING")
        if digest(asdict(self.plan.config)) != self.plan.value["config_sha256"] or (
            self.plan.mode == "live_text"
            and self.plan.value["live_text"]["bridge_code_sha256"] != bridge_code_sha256()
        ):
            raise ValueError("LIVE_TEXT_RUNTIME_CHANGED")
        if self.stop_path.exists() or self.stop_path.is_symlink():
            raise ValueError("WINDOW_STOPPED")
        # All runtime/file checks precede the final clock sample. The probe's
        # transport calls this after every request hook, including DNS pinning.
        if not self.plan.start <= self.clock() < self.plan.end:
            raise ValueError("WINDOW_OUTSIDE_INTERVAL")

    async def probe(self, protocol):
        if (
            str(protocol.base_url).rstrip("/") != self.plan.config.onebot_http_url.rstrip("/")
            or protocol.headers.get("Authorization") != "Bearer " + self.plan.config.onebot_token
        ):
            raise ValueError("LIVE_TEXT_ONEBOT_ENDPOINT_CHANGED")

        async def boundary(request):
            check_protocol_request(self, request)
            content = request.content
            if request.url.path == "/send_private_msg" or decode(content, 64) != {}:
                raise ValueError("WINDOW_PROBE_CHANGED")
            self.check_opening()
            seal_protocol_content(request, content)

        async with checked_http_client(protocol, boundary) as scoped:
            if not (await OneBotClient(self.plan.config, scoped).probe())["connection_ready"]:
                raise ValueError("ONEBOT_NOT_READY")

    async def handoff(self, client):
        scope = self.plan.value.get("live_text", {}).get("handoff")
        if self.plan.mode != "live_text" or not scope:
            raise ValueError("WINDOW_HANDOFF_APPROVAL_REQUIRED")
        if digest(self.plan.value) != self.plan.sha256 or (
            digest(asdict(self.plan.config)) != self.plan.value["config_sha256"]
            or self.plan.value["live_text"]["bridge_code_sha256"] != bridge_code_sha256()
        ):
            raise ValueError("LIVE_TEXT_RUNTIME_CHANGED")
        current = self.db.execute("SELECT * FROM test_window WHERE singleton=1").fetchone()
        if not current:
            raise ValueError("WINDOW_NOT_FOUND")
        old = TestWindow(self.spool, stored_plan(current["plan_json"], self.plan.config),
                         clock=self.clock)
        source = old.verify(active=False)
        records, head = old.read_journal()
        capturing = False
        for record in records:
            if record["kind"] == "CAPTURE_STARTED":
                capturing = True
            elif record["kind"] in {"CAPTURED", "CAPTURE_DUPLICATE"}:
                capturing = False
            elif record["kind"] == "STOP" and record["data"].get("reason") == (
                "CAPTURE_FAILED_CLOSED"
            ):
                # The exclusive receiver exited its WS context before persisting
                # this terminal failure. A CAPTURED-fsync/DB crash instead leaves
                # a mismatching head (already refused), or an unresolved input.
                capturing = False
        if (
            source["state"] != "STOPPED" or old.plan.mode != "live_text"
            or scope["predecessor_plan_sha256"] != old.plan.sha256
            or scope["predecessor_window_id"] != old.plan.value["window_id"]
            or scope["bridge_journal_head_sha256"] != head
            or scope["identity_sha256"] != old.plan.value["identity_sha256"]
            or scope["protocol_epoch"] != old.plan.value["protocol_epoch"]
            or self.plan.value["live_text"]["budget_journal_id"]
            != old.plan.value["live_text"]["budget_journal_id"]
            or self.plan.value["window_id"] == old.plan.value["window_id"]
            or self.plan.journal == old.plan.journal or self.plan.journal.exists()
            or self.plan.journal.is_symlink() or self.stop_path.exists()
            or not self.plan.start <= self.clock() < self.plan.end
        ):
            raise ValueError("WINDOW_HANDOFF_SOURCE_MISMATCH")
        if capturing:
            raise ValueError("CAPTURE_RECONCILIATION_REQUIRED")
        if not any(record["kind"] == "CORE_STOP_CONFIRMED" for record in records):
            raise ValueError("LIVE_TEXT_CORE_STOP_UNCONFIRMED")
        identity = self.db.execute("SELECT value FROM identity").fetchall()
        if len(identity) != 1 or identity[0][0] != encode([
            self.plan.config.owner_qq_id, self.plan.config.bot_qq_id,
            self.plan.config.origin, self.plan.config.protocol_epoch,
        ]):
            raise ValueError("BRIDGE_IDENTITY_CHANGED")
        if self.db.execute("SELECT 1 FROM window_history WHERE plan_hash=?", (
            self.plan.sha256,
        )).fetchone() or any(
            json.loads(json.loads(row[0])["plan_json"])[
                "window_id"
            ] == self.plan.value["window_id"]
            for row in self.db.execute("SELECT record_json FROM window_history")
        ):
            raise ValueError("WINDOW_ALREADY_REGISTERED")
        old.check_spool()
        if self.db.execute("SELECT 1 FROM window_inputs WHERE state!='ACK' LIMIT 1").fetchone() or (
            self.db.execute("SELECT 1 FROM window_attempts WHERE state!='ACK' LIMIT 1").fetchone()
        ) or self.db.execute(
            "SELECT 1 FROM results WHERE state!='accepted' "
            "OR json_extract(body,'$.result')!='SENT' LIMIT 1"
        ).fetchone():
            raise ValueError("WINDOW_HANDOFF_RECONCILIATION_REQUIRED")
        if (
            old.stop_path.is_symlink() or not old.stop_path.is_file()
            or old.stop_path.read_bytes() != b"OWNER_STOP\n"
        ):
            raise ValueError("WINDOW_STOP_EVIDENCE_MISSING")
        snapshot = dict(source)
        # Close locally BEFORE crossing HTTP. A lost acknowledgment is never an
        # invitation to retry another successor or refill the old allowance.
        requested = old.append("HANDOFF_REQUESTED", {
            "successor_window_sha256": self.plan.sha256, "handoff_sha256": digest(scope),
        })
        with self.db:
            self.db.execute("UPDATE test_window SET state='HANDOFF_UNCONFIRMED',journal_head=?",
                            (requested,))
        result = await self.core_post(client, "/internal/v1/live-text/handoff", {
            "window_sha256": self.plan.sha256,
        })
        if (
            not isinstance(result, dict) or set(result) != {
                "state", "window_sha256", "predecessor_window_sha256", "handoff_sha256",
                "budget_journal_head_sha256", "privacy_epoch", "erasure_ledger_sha256",
            }
            or result["state"] != "PREPARED" or result["window_sha256"] != self.plan.sha256
            or result["predecessor_window_sha256"] != old.plan.sha256
            or result["handoff_sha256"] != digest(scope)
            or result["privacy_epoch"] != scope["privacy_epoch"]
            or result["erasure_ledger_sha256"] != scope["erasure_ledger_sha256"]
            or not isinstance(result["budget_journal_head_sha256"], str)
            or not SHA.fullmatch(result["budget_journal_head_sha256"])
        ):
            raise ValueError("WINDOW_HANDOFF_CORE_UNCONFIRMED")
        closed = old.verify(active=False)
        if closed["state"] != "HANDOFF_UNCONFIRMED" or closed["journal_head"] != requested:
            raise ValueError("WINDOW_HANDOFF_SOURCE_MISMATCH")
        old.check_spool()
        retired = old.append("RETIRED", {
            "snapshot_sha256": digest(snapshot), "successor_window_sha256": self.plan.sha256,
            "core_receipt_sha256": digest(result), "handoff_sha256": digest(scope),
        })
        prepared = self.append("HANDOFF_PREPARED", {
            "predecessor_window_sha256": old.plan.sha256, "retirement_head": retired,
            "core_receipt": result, "handoff_sha256": digest(scope),
        }, create=True)
        with self.db:
            self.db.execute("INSERT INTO window_history VALUES(?,?,?,?)", (
                old.plan.sha256, encode(snapshot), retired, self.plan.sha256,
            ))
            self.db.execute(
                "UPDATE test_window SET plan_hash=?,plan_json=?,journal_head=?,state='PREPARED',"
                "fence=0,initial_counts='{}',initial_inbox_rowid=?,reason=NULL", (
                    self.plan.sha256, encode(self.plan.value), prepared,
                    self.db.execute("SELECT COALESCE(MAX(rowid),0) FROM inbox").fetchone()[0],
                ),
            )
        return self.status()

    async def open_successor(self, client):
        row = self.verify(active=False)
        records, head = self.read_journal()
        if row["state"] != "PREPARED" or len(records) != 1:
            raise ValueError("WINDOW_HANDOFF_NOT_PREPARED")
        data = records[0]["data"]
        receipt = {
            "kind": "stopped_window_handoff", "window_sha256": self.plan.sha256,
            "predecessor_window_sha256": data["predecessor_window_sha256"],
            "handoff_sha256": data["handoff_sha256"],
            "core_receipt_sha256": digest(data["core_receipt"]),
            "bridge_journal_head_sha256": head,
        }
        receipt["signature"] = Ed25519PrivateKey.from_private_bytes(bytes.fromhex(
            self.plan.config.capture_signing_key
        )).sign(HANDOFF_DOMAIN + encode(receipt).encode()).hex()
        requested = self.append("OPEN_REQUESTED", {"receipt_sha256": digest(receipt)})
        with self.db:
            self.db.execute("UPDATE test_window SET state='OPEN_UNCONFIRMED',journal_head=?",
                            (requested,))
        await self.core_post(client, "/internal/v1/live-text/open", {
            "window_sha256": self.plan.sha256, "handoff_receipt": receipt,
        })
        baseline = await self.core_counts(client, initial=True)
        self.verify(active=False)
        if not self.plan.start <= self.clock() < self.plan.end or self.stop_path.exists():
            raise ValueError("WINDOW_STOPPED")
        head = self.append("OPEN", {"plan_sha256": self.plan.sha256})
        with self.db:
            self.db.execute("UPDATE test_window SET state='OPEN',journal_head=?,initial_counts=?",
                            (head, encode(baseline)))
        return self.status()

    async def open(self, client, protocol=None):
        self.check_opening()
        successor = bool(self.plan.value.get("live_text", {}).get("handoff"))
        if successor:
            row = self.verify(active=False)
            records, _ = self.read_journal()
            if row["state"] != "PREPARED" or len(records) != 1:
                raise ValueError("WINDOW_HANDOFF_NOT_PREPARED")
        elif self.plan.journal.exists() or self.plan.journal.is_symlink() or self.db.execute(
            "SELECT 1 FROM sqlite_master WHERE name='test_window'"
        ).fetchone():
            raise ValueError("WINDOW_ALREADY_REGISTERED")
        if self.plan.value["execution"] == "approved_live" and protocol is None:
            raise ValueError("ONEBOT_READINESS_REQUIRED")
        if (
            self.spool.pending_count
            or self.db.execute(
                "SELECT 1 FROM results WHERE state!='accepted' "
                "OR json_extract(body,'$.result')!='SENT' LIMIT 1"
            ).fetchone()
        ):
            raise ValueError("INITIAL_SPOOL_MUST_BE_CONFIRMED")
        if protocol is not None:
            await self.probe(protocol)
        self.check_opening()
        if successor:
            return await self.open_successor(client)
        if self.plan.mode == "live_text":
            self.check_core_client(client)
            await self.core_post(client, "/internal/v1/live-text/open", {
                "window_sha256": self.plan.sha256,
            })
        baseline = await self.core_counts(client, initial=True)
        if not self.plan.start <= self.clock() < self.plan.end:
            raise ValueError("WINDOW_OUTSIDE_INTERVAL")
        head = self.append("OPEN", {"plan_sha256": self.plan.sha256}, create=True)
        self.db.executescript(
            "CREATE TABLE test_window (singleton INTEGER PRIMARY KEY CHECK(singleton=1),"
            "plan_hash TEXT NOT NULL,plan_json TEXT NOT NULL,journal_head TEXT NOT NULL,"
            "state TEXT NOT NULL,fence INTEGER NOT NULL,initial_counts TEXT NOT NULL,"
            "initial_inbox_rowid INTEGER NOT NULL,reason TEXT);"
            "CREATE TABLE window_inputs(event_id TEXT PRIMARY KEY,input_hash TEXT NOT NULL,"
            "approval_hash TEXT NOT NULL,state TEXT NOT NULL,capture_json TEXT,"
            "plan_hash TEXT NOT NULL);"
            "CREATE TABLE window_attempts(message_id TEXT PRIMARY KEY,"
            "event_id TEXT NOT NULL UNIQUE,"
            "fence INTEGER NOT NULL,state TEXT NOT NULL,text_hash TEXT NOT NULL,"
            "approval_hash TEXT NOT NULL,provider_ack_hash TEXT,item_hash TEXT NOT NULL,"
            "plan_hash TEXT NOT NULL);"
            "CREATE TABLE window_history(plan_hash TEXT PRIMARY KEY,record_json TEXT NOT NULL,"
            "retirement_head TEXT NOT NULL,successor_hash TEXT NOT NULL);"
        )
        with self.db:
            self.db.execute(
                "INSERT INTO test_window VALUES(1,?,?,?,'OPEN',0,?,?,NULL)",
                (
                    self.plan.sha256,
                    encode(self.plan.value),
                    head,
                    encode(baseline),
                    self.db.execute("SELECT COALESCE(MAX(rowid),0) FROM inbox").fetchone()[0],
                ),
            )
        return self.status()

    def status(self):
        row = self.verify(active=False)
        attempts = dict(
            self.db.execute("SELECT state,COUNT(*) FROM window_attempts WHERE plan_hash=? "
                            "GROUP BY state", (self.plan.sha256,)).fetchall()
        )
        result = {
            "state": row["state"],
            "reason": row["reason"],
            "plan_sha256": row["plan_hash"],
            "fence": row["fence"],
            "new_inputs": self.count("window_inputs"),
            "attempts_reserved": sum(attempts.values()),
            "attempt_states": attempts,
            "visible_replies_upper_bound": sum(attempts.values()),
            "phone_receipts": None,
            "e2e_p50_ms": None,
            "e2e_p95_ms": None,
        }
        if row["state"] == "OPEN" and self.stop_path.exists():
            result.update(state="STOP_REQUESTED", reason="OWNER_STOP_REQUESTED")
        if row["state"] == "CAPTURING":
            result.update(state="CAPTURE_UNCONFIRMED", reason="CAPTURE_RECONCILIATION_REQUIRED")
        if self.plan.mode == "live_text":
            records, _ = self.read_journal()
            confirmed = any(record.get("kind") == "CORE_STOP_CONFIRMED" for record in records)
            result.update(mode="live_text", model_attempts=self.model_attempts,
                          local_state=result["state"], core_stop_confirmed=confirmed)
            if row["state"] == "STOPPED" and not confirmed:
                result["state"] = "CORE_STOP_UNCONFIRMED"
        return result

    def stop(self, reason="OWNER_STOP"):
        self.verify(active=False)
        request_stop(self.spool.directory, plan=self.plan)
        head = self.append("STOP", {"reason": reason})
        with self.db:
            self.db.execute(
                "UPDATE test_window SET state='STOPPED',reason=?,journal_head=? WHERE singleton=1",
                (reason, head),
            )
        return self.status()

    def check_spool(self):
        row = self.row()
        if (
            self.spool.pending_count
            or self.db.execute("SELECT 1 FROM results WHERE state='pending' LIMIT 1").fetchone()
        ):
            raise ValueError("SPOOL_RECONCILIATION_REQUIRED")
        if self.db.execute(
            "SELECT 1 FROM inbox i WHERE i.rowid>? AND NOT EXISTS "
            "(SELECT 1 FROM window_inputs w WHERE w.event_id=i.key AND w.plan_hash=?) LIMIT 1",
            (row["initial_inbox_rowid"], self.plan.sha256),
        ).fetchone():
            raise ValueError("UNAPPROVED_SPOOL_INPUT")

    async def ingest(self, client, raw, approval):
        if self.busy:
            raise ValueError("WINDOW_EXECUTOR_BUSY")
        self.busy = True
        try:
            return await self._ingest(client, raw, approval)
        except BaseException as error:
            try:
                await self.stop_core(client)
            except BaseException:
                pass
            try:
                self.stop("INPUT_FAILED_CLOSED")
            except Exception:
                pass
            if (
                self.plan.mode == "live_text" and not self.core_stop_ack
                and isinstance(error, Exception)
            ):
                raise ValueError("LIVE_TEXT_CORE_STOP_UNCONFIRMED") from None
            raise
        finally:
            self.busy = False

    async def _ingest(self, client, raw, approval):
        self.verify()
        if self.plan.value["execution"] != "local_synthetic":
            raise ValueError("AUTHENTICATED_CAPTURE_REQUIRED")
        self.check_spool()
        await self.core_counts(client)
        message = raw.get("message")
        if isinstance(message, str):
            plain = "[CQ:" not in message
        else:
            plain = (
                isinstance(message, list)
                and bool(message)
                and all(
                    isinstance(segment, dict) and segment.get("type") == "text"
                    for segment in message
                )
            )
        event = normalize(encode(raw), self.plan.config, now=self.clock().timestamp())
        if (
            not plain
            or event is None
            or not event["payload"]["text"].strip()
            or len(event["payload"]["text"]) > self.plan.value["max_input_chars"]
            or not self.plan.start <= datetime.fromisoformat(event["occurred_at"]) < self.plan.end
        ):
            raise ValueError("WINDOW_OWNER_TEXT_INPUT_REQUIRED")
        if self.plan.mode == "live_text" and (
            event["payload"]["text"].strip().startswith("/")
            or self.plan.value["execution"] != "local_synthetic"
        ):
            raise ValueError("LIVE_TEXT_ORDINARY_AUTHENTICATED_INPUT_REQUIRED")
        event_id, text_hash = event["event_id"], digest(event["payload"]["text"])
        expected = {
            "window_sha256": self.plan.sha256,
            "event_id": event_id,
            "input_sha256": text_hash,
        }
        if (
            not isinstance(approval, dict)
            or set(approval) != {*expected, "evidence_sha256"}
            or any(approval[key] != val for key, val in expected.items())
            or not isinstance(approval["evidence_sha256"], str)
            or not SHA.fullmatch(approval["evidence_sha256"])
        ):
            raise ValueError("INDIVIDUAL_INPUT_APPROVAL_REQUIRED")
        prior = self.db.execute(
            "SELECT input_hash,plan_hash FROM window_inputs WHERE event_id=?", (event_id,)
        ).fetchone()
        if prior:
            if prior["plan_hash"] != self.plan.sha256:
                raise ValueError("HISTORICAL_INPUT_REFUSED")
            # Spool also checks full source fingerprint: same ID/time/text changes are conflicts.
            if prior[0] != text_hash:
                raise ValueError("SOURCE_EVENT_CONFLICT")
            self.spool.put(event, digest({"message": raw["message"], "time": raw["time"]}))
            return self.status()
        if (
            self.count("window_inputs")
            >= self.plan.value["max_new_inputs"]
        ):
            raise ValueError("WINDOW_INPUT_CAP")
        if self.count("window_inputs", "state!='ACK'"):
            raise ValueError("ONE_INPUT_AT_A_TIME")
        if self.db.execute("SELECT 1 FROM inbox WHERE key=?", (event_id,)).fetchone():
            raise ValueError("HISTORICAL_INPUT_REFUSED")
        self.verify()
        if self.plan.mode == "live_text":
            cumulative.admit(self, "owner_inputs", digest(event_id))
            input_at = self.clock()
            await self.core_post(client, "/internal/v1/live-text/inputs/approve", approval)
            self.verify()
            quota = cumulative.scope(self.plan)
            if quota and cumulative.periods(input_at, quota["timezone"]) != cumulative.periods(
                self.clock(), quota["timezone"],
            ):
                raise ValueError("CUMULATIVE_CAPTURE_PERIOD_CHANGED")
        head = self.append(
            "INPUT",
            {
                "event_hash": digest(event_id),
                "text_sha256": text_hash,
                "approval_sha256": digest(approval),
            },
        )
        with self.db:
            self.spool.put(event, digest({"message": raw["message"], "time": raw["time"]}))
            self.db.execute(
                "INSERT INTO window_inputs VALUES(?,?,?,'APPROVED',NULL,?)",
                (event_id, text_hash, digest(approval), self.plan.sha256),
            )
            self.db.execute("UPDATE test_window SET journal_head=?", (head,))
        self.verify()
        async with self.core_http(client) as scoped:
            await self.spool.flush_one(scoped)
        return self.status()

    async def capture_once(self, client, protocol):
        from .capture import capture_once

        return await capture_once(self, client, protocol)

    async def approve_captured(self, client, approval, **image_options):
        from .capture import approve_captured

        return await approve_captured(self, client, approval, **image_options)

    async def send_once(self, client, protocol, approval):
        if self.busy:
            raise ValueError("WINDOW_EXECUTOR_BUSY")
        self.busy = True
        operation = SendOperation(self, client, approval)
        try:
            self.verify()
            self.check_spool()
            await self.core_counts(client)
            self.verify()
            sender = WindowOneBot(operation, protocol)
            async with self.core_http(client) as scoped, sender.delegate.client:
                if not await deliver_tick(scoped, sender, self.spool, guard=operation):
                    raise ValueError("NO_APPROVED_DELIVERY_READY")
            if self.row()["state"] == "STOPPED":
                await self.stop_core(client)
            return self.status()
        except BaseException as error:
            try:
                await self.stop_core(client)
            except BaseException:
                pass
            # Cancellation/crash never refunds reservations. A hard kill leaves RESERVED/
            # IO_STARTED and the next invocation blocks before any new send.
            try:
                self.stop("SEND_FAILED_CLOSED")
            except Exception:
                pass
            if (
                self.plan.mode == "live_text" and not self.core_stop_ack
                and isinstance(error, Exception)
            ):
                raise ValueError("LIVE_TEXT_CORE_STOP_UNCONFIRMED") from None
            raise
        finally:
            self.busy = False


class SendOperation:
    """One deliver_tick invocation; no send state escapes into the window object."""

    def __init__(self, window, client, approval):
        self.window, self.db, self.client, self.approval = window, window.db, client, approval
        self.clock = window.clock
        self.current = self.current_item = None
        self.lease_until = None

    def reserve(self, item):
        self.window.verify()
        approval = self.approval
        key = item.get("delivery_key", "")
        match = re.fullmatch(r"reply:(evt_[a-f0-9]{64}):v1", key)
        if not match:
            raise ValueError("WINDOW_REPLY_KEY_REQUIRED")
        event_id = match[1]
        expected = {
            "window_sha256": self.window.plan.sha256,
            "event_id": event_id,
            "message_id": item["message_id"],
            "reply_sha256": digest(item["content"]["text"]),
        }
        if (
            not isinstance(approval, dict)
            or set(approval) != {*expected, "evidence_sha256"}
            or any(approval[key] != val for key, val in expected.items())
            or not isinstance(approval["evidence_sha256"], str)
            or not SHA.fullmatch(approval["evidence_sha256"])
        ):
            raise ValueError("INDIVIDUAL_REPLY_APPROVAL_REQUIRED")
        if len(item["content"]["text"]) > self.window.plan.value["max_reply_chars"]:
            raise ValueError("WINDOW_REPLY_TEXT_LIMIT")
        if not self.db.execute(
            "SELECT 1 FROM window_inputs WHERE event_id=? AND state='APPROVED' AND plan_hash=?",
            (event_id, self.window.plan.sha256),
        ).fetchone():
            raise ValueError("UNAPPROVED_DELIVERY_INPUT")
        attempts = self.window.count("window_attempts")
        if attempts >= min(
            self.window.plan.value["max_attempts"], self.window.plan.value["max_visible_replies"]
        ):
            raise ValueError("WINDOW_ATTEMPT_CAP")
        fence = self.window.row()["fence"] + 1
        item_hash = digest(item)
        head = self.window.append(
            "RESERVED",
            {
                "message_hash": digest(item["message_id"]),
                "event_hash": digest(event_id),
                "fence": fence,
                "item_sha256": item_hash,
            },
        )
        with self.db:
            self.db.execute(
                "INSERT INTO window_attempts VALUES(?,?,?,'RESERVED',?,?,NULL,?,?)",
                (item["message_id"], event_id, fence, expected["reply_sha256"], digest(approval),
                 item_hash, self.window.plan.sha256),
            )
            self.db.execute("UPDATE test_window SET fence=?,journal_head=?", (fence, head))
        self.current = (item["message_id"], fence)
        self.current_item = item
        self.lease_until = datetime.fromisoformat(item["lease_until"])

    def before_io(self, text):
        self.window.verify(active=False, dispatch=True)
        row = self.window.row()
        if (
            row["state"] != "OPEN"
            or not self.window.plan.start <= self.clock() < self.window.plan.end
            or not self.current
            or self.current[1] != row["fence"]
        ):
            raise ValueError("WINDOW_SEND_FENCED")
        attempt = self.db.execute(
            "SELECT * FROM window_attempts WHERE message_id=? AND fence=? AND plan_hash=?",
            (*self.current, self.window.plan.sha256),
        ).fetchone()
        if not attempt or attempt["state"] != "RESERVED" or attempt["text_hash"] != digest(text):
            raise ValueError("WINDOW_SEND_FENCED")
        head = self.window.append("IO_STARTED", {"fence": row["fence"]})
        with self.db:
            self.db.execute(
                "UPDATE window_attempts SET state='IO_STARTED' WHERE message_id=?",
                (self.current[0],),
            )
            self.db.execute("UPDATE test_window SET journal_head=?", (head,))
        # No await occurs between the final clock check and the existing send adapter.
        if not self.window.plan.start <= self.clock() < self.window.plan.end:
            raise ValueError("WINDOW_SEND_FENCED")

    def before_dispatch(self, *, state="IO_STARTED", request=None):
        # HTTP request hooks can await DNS pinning after before_io(). Check again
        # after those hooks, at the transport boundary; keep the spent reservation.
        self.window.verify(active=False, dispatch=True)
        row = self.window.row()
        attempt = self.db.execute(
            "SELECT * FROM window_attempts WHERE message_id=? AND fence=? AND plan_hash=?",
            (*self.current, self.window.plan.sha256),
        ).fetchone()
        item = self.current_item
        lease_until = min(self.lease_until, datetime.fromisoformat(item["lease_until"]))
        stop_requested = self.window.stop_path.exists()
        quota = cumulative.inspect(self.window)
        # All disk/DB reads are complete. Approval/config/lease/deadline below are
        # pure checks with no lock, await, or file I/O after the last clock sample.
        if request is not None:
            check_protocol_request(self.window, request)
            expected = {"user_id": int(self.window.plan.config.owner_qq_id),
                        "message": [{"type": "text", "data": {"text": item["content"]["text"]}}],
                        "auto_escape": True}
            content = request.content
            body = decode(content, 65536)
            if (
                body != expected or not attempt
                or digest(body["message"][0]["data"]["text"]) != attempt["text_hash"]
            ):
                raise ValueError("WINDOW_SEND_FENCED")
        if (
            stop_requested or not attempt
            or digest(self.approval) != attempt["approval_hash"]
            or digest(item) != attempt["item_hash"]
            or item["message_id"] != self.current[0]
            or self.approval["message_id"] != self.current[0]
            or item["delivery_key"] != f"reply:{attempt['event_id']}:v1"
            or self.approval["event_id"] != attempt["event_id"]
            or self.approval["reply_sha256"] != attempt["text_hash"]
            or digest(self.window.plan.value) != self.window.plan.sha256
            or digest(asdict(self.window.plan.config)) != self.window.plan.value["config_sha256"]
            or row["state"] != "OPEN"
            or row["fence"] != self.current[1]
            or attempt["state"] != state
            or not self.window.plan.start <= (now := self.clock()) < self.window.plan.end
            or now >= lease_until
        ):
            raise ValueError("WINDOW_SEND_FENCED")
        if request is not None:
            cumulative.dispatch(self.window, quota, self.current[0], now)
            seal_protocol_content(request, content)

    def complete(self, item, provider_id):
        state = "ACK" if provider_id else "UNKNOWN"
        head = self.window.append(state, {"fence": self.current[1]})
        with self.db:
            self.db.execute(
                "UPDATE window_attempts SET state=?,provider_ack_hash=? WHERE message_id=?",
                (state, digest(provider_id) if provider_id else None, self.current[0]),
            )
            if provider_id:
                self.db.execute(
                    "UPDATE window_inputs SET state='ACK' WHERE event_id="
                    "(SELECT event_id FROM window_attempts WHERE message_id=?)",
                    (self.current[0],),
                )
            self.db.execute("UPDATE test_window SET journal_head=?", (head,))
        if not provider_id:
            self.window.stop("DELIVERY_UNCERTAIN")
        elif self.window.count("window_attempts") >= min(
            self.window.plan.value["max_attempts"],
            self.window.plan.value["max_visible_replies"],
            self.window.plan.value["max_new_inputs"],
        ):
            self.window.stop("WINDOW_LIMIT_REACHED")


class WindowOneBot:
    def __init__(self, operation, protocol):
        self.operation, self.window = operation, operation.window
        self.config = self.window.plan.config
        if (
            str(protocol.base_url).rstrip("/") != self.config.onebot_http_url.rstrip("/")
            or protocol.headers.get("Authorization") != "Bearer " + self.config.onebot_token
        ):
            raise ValueError("LIVE_TEXT_ONEBOT_ENDPOINT_CHANGED")
        scoped = checked_http_client(protocol, self.before_dispatch)
        # Ephemeral permission only; persistent delivery_enabled remains false.
        self.delegate = OneBotClient(replace(self.config, delivery_enabled=True), scoped)

    async def before_dispatch(self, request):
        live = self.window.plan.mode == "live_text"
        check_protocol_request(self.window, request)
        if request.url.path != "/send_private_msg":
            content = request.content
            if decode(content, 64) != {}:
                raise ValueError("WINDOW_PROBE_CHANGED")
            self.operation.before_dispatch(state="RESERVED")
            seal_protocol_content(request, content)
            return
        item, approval = self.operation.current_item, self.operation.approval
        if live:
            receipt = await self.window.core_post(
                self.operation.client, "/internal/v1/live-text/send-check",
                {key: approval[key] for key in (
                    "window_sha256", "event_id", "message_id", "reply_sha256"
                )} | {"lease_token": item["lease_token"]},
            )
            if set(receipt) != {"approved", "lease_until"} or receipt["approved"] is not True:
                raise ValueError("LIVE_TEXT_SEND_FENCED")
            self.operation.lease_until = datetime.fromisoformat(receipt["lease_until"])
            # Core's await is the final asynchronous approval boundary. Rebind
            # the actual pending request as well as the local operation after it.
        self.operation.before_dispatch(request=request)

    async def probe(self):
        return await self.delegate.probe()

    async def send_text(self, text):
        self.operation.before_io(text)
        return await self.delegate.send_text(text)


def seal_protocol_content(request, content):
    # HTTPX sends stream, not its content cache. Seal exactly the bounded
    # bytes just checked; never consume an arbitrary replacement stream.
    if "Transfer-Encoding" in request.headers:
        raise ValueError("WINDOW_REQUEST_FRAMING_CHANGED")
    request.stream = httpx.ByteStream(content)
    request.headers["Content-Length"] = str(len(content))


def check_protocol_request(window, request):
    config = window.plan.config
    if (
        request.method != "POST" or request.url.path not in {
            "/get_login_info", "/get_status", "/get_version_info", "/send_private_msg"
        }
        or not private_request_matches(
            request, config.onebot_http_url, request.url.path,
            synthetic=window.plan.value["execution"] == "local_synthetic",
        )
        or request.headers.get("Authorization") != "Bearer " + config.onebot_token
    ):
        raise ValueError("LIVE_TEXT_ONEBOT_REQUEST_CHANGED")


async def command(config, args):
    if args.command in {"window-status", "window-stop", "window-quota-inventory"}:
        database = Path(config.data_dir) / "spool.sqlite3"
        if not database.is_file() or database.is_symlink():
            raise ValueError("WINDOW_NOT_FOUND")
        if args.command == "window-stop":
            # This reaches the active receive/send owner without acquiring its lock.
            request_stop(config.data_dir)
        try:
            spool = Spool(config)
        except RuntimeError as error:
            if args.command == "window-stop" and str(error) == "BRIDGE_ALREADY_RUNNING":
                raise ValueError("WINDOW_STOP_PENDING") from None
            raise
        with spool:
            if not spool.db.execute(
                "SELECT 1 FROM sqlite_master WHERE name='test_window'"
            ).fetchone():
                raise ValueError("WINDOW_NOT_FOUND")
            value = json.loads(spool.db.execute("SELECT plan_json FROM test_window").fetchone()[0])
            # Stop/status performs no network I/O and does not require unexpired authorization.
            plan = stored_plan(encode(value), config)
            window = TestWindow(spool, plan)
            if args.command == "window-quota-inventory":
                return cumulative.legacy_inventory(window)
            if args.command == "window-status":
                return window.status()
            # Always persist the local stop; an unreachable Core is not a confirmed
            # global stop. Report that failure, never claim a paid call was revoked.
            if plan.mode == "live_text":
                async with private_http_client(config.core_url, config.bridge_token) as client:
                    return await window.stop_live(client)
            return window.stop()
    if args.window is None or args.inventory is None:
        raise ValueError("WINDOW_AND_INVENTORY_REQUIRED")
    plan = WindowPlan(
        private_json(args.window),
        config,
        private_json(args.inventory),
        private_json(args.authorization) if args.authorization else None,
    )
    with Spool(config) as spool:
        window = TestWindow(spool, plan)
        async with private_http_client(config.core_url, config.bridge_token) as client:
            if args.command == "window-open":
                async with private_http_client(
                    config.onebot_http_url, config.onebot_token
                ) as protocol:
                    return await window.open(client, protocol)
            if args.command == "window-handoff":
                return await window.handoff(client)
            if args.command == "window-ingest":
                if args.event is None or args.approval is None:
                    raise ValueError("INPUT_AND_APPROVAL_REQUIRED")
                return await window.ingest(
                    client, private_json(args.event), private_json(args.approval)
                )
            if args.command == "window-approve-input":
                if args.approval is None:
                    raise ValueError("INDIVIDUAL_CAPTURE_APPROVAL_REQUIRED")
                return await window.approve_captured(client, private_json(args.approval))
            if args.command == "window-capture-once":
                async with private_http_client(
                    config.onebot_http_url, config.onebot_token
                ) as protocol:
                    return await window.capture_once(client, protocol)
            if args.command == "window-send-once":
                if args.approval is None:
                    raise ValueError("INDIVIDUAL_REPLY_APPROVAL_REQUIRED")
                async with private_http_client(
                    config.onebot_http_url, config.onebot_token
                ) as protocol:
                    return await window.send_once(client, protocol, private_json(args.approval))
    raise ValueError("INVALID_WINDOW_COMMAND")
