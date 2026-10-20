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

from .event_normalizer import decode, normalize
from .network import private_http_client, private_request_matches
from .onebot_client import OneBotClient, bounded_post
from .outbound import deliver_tick
from .spool import Spool, encode

SHA = re.compile(r"[a-f0-9]{64}")
ACTIVE = {"PENDING", "LEASED", "SENDING", "RETRY_WAIT", "DELIVERY_UNCERTAIN"}


def digest(value):
    return hashlib.sha256(encode(value).encode()).hexdigest()


def bridge_code_sha256():
    package = Path(__file__).parent
    return digest([[p.name, hashlib.sha256(p.read_bytes()).hexdigest()]
                   for p in sorted(package.glob("*.py"))])


def private_json(path):
    target = Path(path)
    info = target.lstat()
    if (
        not stat.S_ISREG(info.st_mode)
        or target.is_symlink()
        or (os.name != "nt" and info.st_mode & 0o077)
        or info.st_size > 65536
    ):
        raise ValueError("PRIVATE_WINDOW_FILE_REQUIRED")
    return decode(target.read_bytes(), 65536)


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
            "image_digests",
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
                "live_approved", "execution", "window_id", "starts_at", "ends_at",
                "core_config_sha256",
                "core_code_sha256", "bridge_code_sha256", "candidate_sha", "paid_contract_sha256",
                "budget_journal_id", "custodian_sha256", "approval_sha256", "max_inputs",
                "max_model_attempts", "max_input_chars", "max_reply_chars",
            }
            if not isinstance(scope, dict) or scope.get("live_approved") is not True:
                raise ValueError("LIVE_TEXT_APPROVAL_REQUIRED")
            if set(scope) != expected_scope or any(
                not isinstance(scope[k], str) or not SHA.fullmatch(scope[k])
                for k in expected_scope if k.endswith("_sha256")
            ):
                raise ValueError("INVALID_LIVE_TEXT_SCOPE")
            if (
                scope["bridge_code_sha256"] != bridge_code_sha256()
                or scope["execution"] != value["execution"]
                or scope["window_id"] != value["window_id"]
                or instant(scope["starts_at"]) != self.start
                or instant(scope["ends_at"]) != self.end
                or not re.fullmatch(r"[a-f0-9]{40}", scope["candidate_sha"])
                or not re.fullmatch(r"budget_[a-f0-9]{32}", scope["budget_journal_id"])
                or type(scope["max_model_attempts"]) is not int
                or not 1 <= scope["max_model_attempts"] <= 5
                or any(type(scope[key]) is not int or scope[key] != value[other] for key, other in (
                    ("max_inputs", "max_new_inputs"), ("max_input_chars", "max_input_chars"),
                    ("max_reply_chars", "max_reply_chars"),
                ))
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
        images = value["image_digests"]
        if (
            not isinstance(images, dict)
            or set(images) != {"core", "bridge", "protocol"}
            or any(
                not isinstance(image, str)
                or not re.fullmatch(r"[A-Za-z0-9_./:-]+@sha256:[a-f0-9]{64}", image)
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
                "image_digests",
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
        self.clock = clock or (lambda: datetime.now(UTC))
        self.busy = False
        self.current = None
        self.current_item = None
        self.model_attempts = None
        self.core_stop_ack = False

    def row(self):
        self.db.row_factory = sqlite3.Row
        row = self.db.execute("SELECT * FROM test_window WHERE singleton=1").fetchone()
        if not row or row["plan_hash"] != self.plan.sha256:
            raise ValueError("WINDOW_IMMUTABLE_OR_MISSING")
        return row

    def verify(self, *, active=True, dispatch=False):
        if digest(self.plan.value) != self.plan.sha256:
            raise ValueError("WINDOW_IMMUTABLE_OR_MISSING")
        if (active or dispatch) and self.plan.mode == "live_text" and (
            self.plan.value["live_text"]["bridge_code_sha256"] != bridge_code_sha256()
            or digest(asdict(self.plan.config)) != self.plan.value["config_sha256"]
        ):
            raise ValueError("LIVE_TEXT_RUNTIME_CHANGED")
        row = self.row()
        records, head = self.read_journal()
        if not records or head != row["journal_head"]:
            raise ValueError("WINDOW_JOURNAL_RECONCILIATION_REQUIRED")
        if active:
            if row["state"] != "OPEN":
                raise ValueError("WINDOW_STOPPED")
            now = self.clock()
            if not self.plan.start <= now < self.plan.end:
                raise ValueError("WINDOW_OUTSIDE_INTERVAL")
            unknown = self.db.execute(
                "SELECT 1 FROM window_attempts WHERE state!='ACK' LIMIT 1"
            ).fetchone()
            if unknown:
                raise ValueError("WINDOW_ATTEMPT_RECONCILIATION_REQUIRED")
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
        async with client.stream("GET", "/internal/v1/ops/status") as response:
            response.raise_for_status()
            async for chunk in response.aiter_bytes(chunk_size=8192):
                content.extend(chunk)
                if len(content) > 65536:
                    raise ValueError("INVALID_CORE_COUNTS")
        status = decode(bytes(content), 65536)
        deliveries, jobs = counts(status.get("deliveries")), counts(status.get("jobs"))
        closed = (
            isinstance(status.get("features"), dict)
            and all(value is False for value in status["features"].values())
        )
        if self.plan.mode == "live_text":
            live = status.get("live_text_window", {})
            if (
                status.get("mode") != "live" or not closed
                or live.get("approved") is not True
                or live.get("plan_sha256") != self.plan.sha256
                or live.get("scope_sha256") != digest(self.plan.value["live_text"])
                or type(live.get("model_attempts")) is not int
                or not 0 <= live["model_attempts"] <= self.plan.value["live_text"][
                    "max_model_attempts"
                ]
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
            acked = self.db.execute(
                "SELECT COUNT(*) FROM window_attempts WHERE state='ACK'"
            ).fetchone()[0]
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
        hooks = client.event_hooks["request"]
        for hook in list(hooks):
            prior = getattr(hook, "live_window_scope", None)
            if prior is not None:
                if prior != self.plan.sha256:
                    raise ValueError("LIVE_TEXT_CORE_ENDPOINT_CHANGED")
                hooks.remove(hook)

        async def after_core_network_preflight(request):
            if (
                not request.url.path.startswith("/internal/v1/")
                or request.headers.get("Authorization") != "Bearer " + self.plan.config.bridge_token
                or not private_request_matches(
                    request, self.plan.config.core_url, request.url.path,
                    synthetic=self.plan.value["execution"] == "local_synthetic",
                )
            ):
                raise ValueError("LIVE_TEXT_CORE_ENDPOINT_CHANGED")

        after_core_network_preflight.live_window_scope = self.plan.sha256
        hooks.append(after_core_network_preflight)

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
            result = await bounded_post(client, "/internal/v1/live-text/stop", {
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

    async def open(self, client):
        if self.plan.journal.exists() or self.plan.journal.is_symlink():
            raise ValueError("WINDOW_ALREADY_REGISTERED")
        if self.db.execute("SELECT 1 FROM sqlite_master WHERE name='test_window'").fetchone():
            raise ValueError("WINDOW_ALREADY_REGISTERED")
        if not self.plan.start <= self.clock() < self.plan.end:
            raise ValueError("WINDOW_OUTSIDE_INTERVAL")
        if (
            self.spool.pending_count
            or self.db.execute(
                "SELECT 1 FROM results WHERE state!='accepted' "
                "OR json_extract(body,'$.result')!='SENT' LIMIT 1"
            ).fetchone()
        ):
            raise ValueError("INITIAL_SPOOL_MUST_BE_CONFIRMED")
        if self.plan.mode == "live_text":
            self.check_core_client(client)
            await bounded_post(client, "/internal/v1/live-text/open", {
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
            "approval_hash TEXT NOT NULL,state TEXT NOT NULL);"
            "CREATE TABLE window_attempts(message_id TEXT PRIMARY KEY,"
            "event_id TEXT NOT NULL UNIQUE,"
            "fence INTEGER NOT NULL,state TEXT NOT NULL,text_hash TEXT NOT NULL,"
            "approval_hash TEXT NOT NULL,provider_ack_hash TEXT);"
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
            self.db.execute("SELECT state,COUNT(*) FROM window_attempts GROUP BY state").fetchall()
        )
        result = {
            "state": row["state"],
            "reason": row["reason"],
            "plan_sha256": row["plan_hash"],
            "fence": row["fence"],
            "new_inputs": self.db.execute("SELECT COUNT(*) FROM window_inputs").fetchone()[0],
            "attempts_reserved": sum(attempts.values()),
            "attempt_states": attempts,
            "visible_replies_upper_bound": sum(attempts.values()),
            "phone_receipts": None,
            "e2e_p50_ms": None,
            "e2e_p95_ms": None,
        }
        if self.plan.mode == "live_text":
            records, _ = self.read_journal()
            confirmed = any(record.get("kind") == "CORE_STOP_CONFIRMED" for record in records)
            result.update(mode="live_text", model_attempts=self.model_attempts,
                          local_state=row["state"], core_stop_confirmed=confirmed)
            if row["state"] == "STOPPED" and not confirmed:
                result["state"] = "CORE_STOP_UNCONFIRMED"
        return result

    def stop(self, reason="OWNER_STOP"):
        self.verify(active=False)
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
            "(SELECT 1 FROM window_inputs w WHERE w.event_id=i.key) LIMIT 1",
            (row["initial_inbox_rowid"],),
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
            "SELECT input_hash FROM window_inputs WHERE event_id=?", (event_id,)
        ).fetchone()
        if prior:
            # Spool also checks full source fingerprint: same ID/time/text changes are conflicts.
            if prior[0] != text_hash:
                raise ValueError("SOURCE_EVENT_CONFLICT")
            self.spool.put(event, digest({"message": raw["message"], "time": raw["time"]}))
            return self.status()
        if (
            self.db.execute("SELECT COUNT(*) FROM window_inputs").fetchone()[0]
            >= self.plan.value["max_new_inputs"]
        ):
            raise ValueError("WINDOW_INPUT_CAP")
        if self.db.execute("SELECT 1 FROM window_inputs WHERE state!='ACK' LIMIT 1").fetchone():
            raise ValueError("ONE_INPUT_AT_A_TIME")
        if self.db.execute("SELECT 1 FROM inbox WHERE key=?", (event_id,)).fetchone():
            raise ValueError("HISTORICAL_INPUT_REFUSED")
        self.verify()
        if self.plan.mode == "live_text":
            await bounded_post(client, "/internal/v1/live-text/inputs/approve", approval)
            self.verify()
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
                "INSERT INTO window_inputs VALUES(?,?,?,'APPROVED')",
                (event_id, text_hash, digest(approval)),
            )
            self.db.execute("UPDATE test_window SET journal_head=?", (head,))
        self.verify()
        await self.spool.flush_one(client)
        return self.status()

    def reserve(self, item):
        self.verify()
        approval = self.approval
        key = item.get("delivery_key", "")
        match = re.fullmatch(r"reply:(evt_[a-f0-9]{64}):v1", key)
        if not match:
            raise ValueError("WINDOW_REPLY_KEY_REQUIRED")
        event_id = match[1]
        expected = {
            "window_sha256": self.plan.sha256,
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
        if len(item["content"]["text"]) > self.plan.value["max_reply_chars"]:
            raise ValueError("WINDOW_REPLY_TEXT_LIMIT")
        if not self.db.execute(
            "SELECT 1 FROM window_inputs WHERE event_id=? AND state='APPROVED'", (event_id,)
        ).fetchone():
            raise ValueError("UNAPPROVED_DELIVERY_INPUT")
        attempts = self.db.execute("SELECT COUNT(*) FROM window_attempts").fetchone()[0]
        if attempts >= min(self.plan.value["max_attempts"], self.plan.value["max_visible_replies"]):
            raise ValueError("WINDOW_ATTEMPT_CAP")
        fence = self.row()["fence"] + 1
        head = self.append(
            "RESERVED",
            {
                "message_hash": digest(item["message_id"]),
                "event_hash": digest(event_id),
                "fence": fence,
            },
        )
        with self.db:
            self.db.execute(
                "INSERT INTO window_attempts VALUES(?,?,?,'RESERVED',?,?,NULL)",
                (item["message_id"], event_id, fence, expected["reply_sha256"], digest(approval)),
            )
            self.db.execute("UPDATE test_window SET fence=?,journal_head=?", (fence, head))
        self.current = (item["message_id"], fence)
        self.current_item = item

    def before_io(self, text):
        self.verify(active=False, dispatch=True)
        row = self.row()
        if (
            row["state"] != "OPEN"
            or not self.plan.start <= self.clock() < self.plan.end
            or not self.current
            or self.current[1] != row["fence"]
        ):
            raise ValueError("WINDOW_SEND_FENCED")
        attempt = self.db.execute(
            "SELECT * FROM window_attempts WHERE message_id=? AND fence=?", self.current
        ).fetchone()
        if not attempt or attempt["state"] != "RESERVED" or attempt["text_hash"] != digest(text):
            raise ValueError("WINDOW_SEND_FENCED")
        head = self.append("IO_STARTED", {"fence": row["fence"]})
        with self.db:
            self.db.execute(
                "UPDATE window_attempts SET state='IO_STARTED' WHERE message_id=?",
                (self.current[0],),
            )
            self.db.execute("UPDATE test_window SET journal_head=?", (head,))
        # No await occurs between the final clock check and the existing send adapter.
        if not self.plan.start <= self.clock() < self.plan.end:
            raise ValueError("WINDOW_SEND_FENCED")

    def before_dispatch(self):
        # HTTP request hooks can await DNS pinning after before_io(). Check again
        # after those hooks, at the transport boundary; keep the spent reservation.
        self.verify(active=False, dispatch=True)
        row = self.row()
        attempt = self.db.execute(
            "SELECT state FROM window_attempts WHERE message_id=? AND fence=?", self.current
        ).fetchone()
        if (
            row["state"] != "OPEN"
            or row["fence"] != self.current[1]
            or not attempt
            or attempt["state"] != "IO_STARTED"
            or not self.plan.start <= self.clock() < self.plan.end
        ):
            raise ValueError("WINDOW_SEND_FENCED")

    def complete(self, item, provider_id):
        state = "ACK" if provider_id else "UNKNOWN"
        head = self.append(state, {"fence": self.current[1]})
        with self.db:
            self.db.execute(
                "UPDATE window_attempts SET state=?,provider_ack_hash=? WHERE message_id=?",
                (state, digest(provider_id) if provider_id else None, item["message_id"]),
            )
            if provider_id:
                self.db.execute(
                    "UPDATE window_inputs SET state='ACK' WHERE event_id="
                    "(SELECT event_id FROM window_attempts WHERE message_id=?)",
                    (item["message_id"],),
                )
            self.db.execute("UPDATE test_window SET journal_head=?", (head,))
        if not provider_id:
            self.stop("DELIVERY_UNCERTAIN")
        elif self.db.execute("SELECT COUNT(*) FROM window_attempts").fetchone()[0] >= min(
            self.plan.value["max_attempts"],
            self.plan.value["max_visible_replies"],
            self.plan.value["max_new_inputs"],
        ):
            self.stop("WINDOW_LIMIT_REACHED")

    async def send_once(self, client, protocol, approval):
        if self.busy:
            raise ValueError("WINDOW_EXECUTOR_BUSY")
        self.busy, self.approval = True, approval
        self.core_client = client
        sender = None
        try:
            self.verify()
            self.check_spool()
            await self.core_counts(client)
            self.verify()
            sender = WindowOneBot(self, protocol)
            if not await deliver_tick(client, sender, self.spool, guard=self):
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
            if sender is not None:
                sender.detach()


class WindowOneBot:
    def __init__(self, window, protocol):
        self.window, self.config = window, window.plan.config
        # Ephemeral permission only; the persistent config is required to remain false.
        self.delegate = OneBotClient(replace(self.config, delivery_enabled=True), protocol)
        if window.plan.mode == "live_text" and (
            str(protocol.base_url).rstrip("/") != self.config.onebot_http_url.rstrip("/")
            or protocol.headers.get("Authorization") != "Bearer " + self.config.onebot_token
        ):
            raise ValueError("LIVE_TEXT_ONEBOT_ENDPOINT_CHANGED")
        self.endpoint_hook = None
        if window.plan.mode == "live_text":
            async def after_network_preflight(request):
                if (
                    request.url.path not in {
                        "/get_login_info", "/get_status", "/get_version_info", "/send_private_msg"
                    }
                    or not private_request_matches(
                        request, self.config.onebot_http_url, request.url.path,
                        synthetic=window.plan.value["execution"] == "local_synthetic",
                    )
                    or request.headers.get("Authorization") != "Bearer " + self.config.onebot_token
                ):
                    raise ValueError("LIVE_TEXT_ONEBOT_REQUEST_CHANGED")

            self.endpoint_hook = after_network_preflight
            protocol.event_hooks["request"].append(self.endpoint_hook)

    def detach(self):
        if self.endpoint_hook is not None:
            self.delegate.client.event_hooks["request"].remove(self.endpoint_hook)

    async def probe(self):
        return await self.delegate.probe()

    async def send_text(self, text):
        self.window.before_io(text)

        async def after_network_preflight(request):
            if request.url.path != "/send_private_msg":
                raise ValueError("WINDOW_ACTION_REFUSED")
            if self.window.plan.mode == "live_text":
                expected = {"user_id": int(self.config.owner_qq_id),
                            "message": [{"type": "text", "data": {"text": text}}],
                            "auto_escape": True}
                if (
                    not private_request_matches(
                        request, self.config.onebot_http_url, "/send_private_msg",
                        synthetic=self.window.plan.value["execution"] == "local_synthetic",
                    )
                    or request.headers.get("Authorization") != "Bearer " + self.config.onebot_token
                    or json.loads(request.content) != expected
                ):
                    raise ValueError("LIVE_TEXT_ONEBOT_REQUEST_CHANGED")
                self.window.check_core_client(self.window.core_client)
                item = self.window.current_item
                approval = self.window.approval
                receipt = await bounded_post(
                    self.window.core_client, "/internal/v1/live-text/send-check",
                    {key: approval[key] for key in (
                        "window_sha256", "event_id", "message_id", "reply_sha256"
                    )} | {"lease_token": item["lease_token"]},
                )
                if receipt != {"approved": True}:
                    raise ValueError("LIVE_TEXT_SEND_FENCED")
            self.window.before_dispatch()

        hooks = self.delegate.client.event_hooks["request"]
        hooks.append(after_network_preflight)
        try:
            return await self.delegate.send_text(text)
        finally:
            hooks.remove(after_network_preflight)


async def command(config, args):
    if args.command in {"window-status", "window-stop"}:
        database = Path(config.data_dir) / "spool.sqlite3"
        if not database.is_file() or database.is_symlink():
            raise ValueError("WINDOW_NOT_FOUND")
        with Spool(config) as spool:
            if not spool.db.execute(
                "SELECT 1 FROM sqlite_master WHERE name='test_window'"
            ).fetchone():
                raise ValueError("WINDOW_NOT_FOUND")
            value = json.loads(spool.db.execute("SELECT plan_json FROM test_window").fetchone()[0])
            # Stop/status performs no network I/O and does not require unexpired authorization.
            plan = object.__new__(WindowPlan)
            plan.value, plan.config, plan.sha256 = value, config, digest(value)
            plan.mode = value.get("mode", "demo")
            plan.journal = Path(value["journal_path"])
            window = TestWindow(spool, plan)
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
                return await window.open(client)
            if args.command == "window-ingest":
                if args.event is None or args.approval is None:
                    raise ValueError("INPUT_AND_APPROVAL_REQUIRED")
                return await window.ingest(
                    client, private_json(args.event), private_json(args.approval)
                )
            if args.command == "window-send-once":
                if args.approval is None:
                    raise ValueError("INDIVIDUAL_REPLY_APPROVAL_REQUIRED")
                async with private_http_client(
                    config.onebot_http_url, config.onebot_token
                ) as protocol:
                    return await window.send_once(client, protocol, private_json(args.approval))
    raise ValueError("INVALID_WINDOW_COMMAND")
