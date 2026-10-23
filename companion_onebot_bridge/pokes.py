"""Authenticated owner poke intake, independent of conversational input revisions."""

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey

from . import capture
from .event_normalizer import decode
from .native_actions import content_hash
from .onebot_client import bounded_post
from .spool import encode

DOMAIN = b"companion-core/onebot-owner-poke/v1\x00"
KIND = "authenticated_onebot_poke"
FIELDS = {"kind", "window_sha256", "event_id", "source_event_id", "notice", "protocol_epoch",
          "identity_sha256", "config_sha256", "frame_sha256", "evidence_sha256", "occurred_at",
          "received_at", "signature"}


def enabled(window):
    scope = window.plan.value.get("live_text", {})
    return (scope.get("companion_scope") == "owner_private_v1"
            and scope.get("automatic_reply") is True
            and scope.get("poke_scope") == "owner_poke_v1")


def notice(frame, config):
    if (not isinstance(frame, dict) or frame.get("post_type") != "notice"
        or frame.get("notice_type") != "notify" or frame.get("sub_type") != "poke"
        or frame.get("group_id") is not None):
        raise ValueError("PRIVATE_OWNER_POKE_REQUIRED")
    value = {key: frame.get(key, "") for key in ("time", "action", "suffix")}
    if (type(value["time"]) is not int or not 1 <= value["time"] <= 2**53 - 1
        or any(not isinstance(value[key], str) or len(value[key]) > 80
               or any(ord(char) < 32 or ord(char) == 127 for char in value[key])
               for key in ("action", "suffix"))):
        raise ValueError("PRIVATE_OWNER_POKE_REQUIRED")
    for key, expected in (("self_id", config.bot_qq_id), ("user_id", config.owner_qq_id),
                          ("sender_id", config.owner_qq_id), ("target_id", config.bot_qq_id)):
        actual = frame.get(key)
        if type(actual) is int:
            actual = str(actual)
        if (not isinstance(actual, str) or not re.fullmatch(r"[1-9][0-9]{4,19}", actual)
            or actual != expected or config.owner_qq_id == config.bot_qq_id):
            raise ValueError("PRIVATE_OWNER_POKE_REQUIRED")
        value[key] = actual
    return value


def source(value, config):
    native = f"{config.bot_qq_id}:{config.origin}:poke:{content_hash(value)}"
    return {"source_event_id": native,
            "event_id": "evt_" + hashlib.sha256(native.encode()).hexdigest()}


def signed_payload(receipt):
    value = {key: item for key, item in receipt.items() if key != "signature"}
    for key in ("occurred_at", "received_at"):
        value[key] = datetime.fromisoformat(value[key]).isoformat()
    return DOMAIN + encode(value).encode()


def verify(window, receipt):
    try:
        if not isinstance(receipt, dict) or set(receipt) != FIELDS:
            raise ValueError()
        Ed25519PublicKey.from_public_bytes(bytes.fromhex(
            window.plan.config.capture_public_key)).verify(
                bytes.fromhex(receipt["signature"]), signed_payload(receipt))
        value = notice({"post_type": "notice", "notice_type": "notify", "sub_type": "poke",
                        **receipt["notice"]}, window.plan.config)
        occurred, received = (datetime.fromisoformat(receipt[key]) for key in (
            "occurred_at", "received_at"))
        if (receipt["kind"] != KIND or value != receipt["notice"]
            or any(receipt[key] != item for key, item in source(value, window.plan.config).items())
            or receipt["window_sha256"] != window.plan.sha256
            or receipt["protocol_epoch"] != window.plan.config.protocol_epoch
            or receipt["identity_sha256"] != window.plan.value["identity_sha256"]
            or receipt["config_sha256"] != window.plan.value["config_sha256"]
            or occurred != datetime.fromtimestamp(value["time"], UTC)
            or occurred.utcoffset() is None or received.utcoffset() is None
            or not window.plan.start <= occurred <= received < window.plan.end
            or (received - occurred).total_seconds() > capture.MAX_AGE_SECONDS
            or any(not isinstance(receipt[key], str)
                   or not re.fullmatch(r"[a-f0-9]{64}", receipt[key])
                   for key in ("frame_sha256", "evidence_sha256"))):
            raise ValueError()
    except (InvalidSignature, ValueError, TypeError, KeyError, OverflowError):
        raise ValueError("AUTHENTICATED_POKE_REQUIRED") from None
    return receipt


@dataclass(frozen=True)
class ObservedPoke:
    receipt: dict

    @property
    def event_id(self):
        return self.receipt["event_id"]

    @property
    def window_sha256(self):
        return self.receipt["window_sha256"]

    def verify(self, window):
        verify(window, self.receipt)


def put(owner_source, frame, received_at, peer):
    window, config = owner_source.window, owner_source.window.plan.config
    if not enabled(window):
        return
    raw = frame.encode() if isinstance(frame, str) else frame
    try:
        value = notice(decode(raw, config.max_frame_bytes), config)
    except ValueError:
        return
    identity = source(value, config)
    if window.db.execute("SELECT 1 FROM window_inputs WHERE event_id=?", (identity["event_id"],))\
            .fetchone() or window.db.execute("SELECT 1 FROM automatic_received WHERE event_id=?",
                                           (identity["event_id"],)).fetchone():
        return
    if window.db.execute("SELECT COUNT(*) FROM automatic_received").fetchone()[0] >= min(
        32, config.max_pending,
    ):
        raise ValueError("AUTOMATIC_RECEIVE_QUEUE_FULL")
    receipt = {"kind": KIND, "window_sha256": window.plan.sha256, **identity, "notice": value,
               "protocol_epoch": config.protocol_epoch,
               "identity_sha256": window.plan.value["identity_sha256"],
               "config_sha256": window.plan.value["config_sha256"],
               "frame_sha256": hashlib.sha256(raw).hexdigest(),
               "occurred_at": datetime.fromtimestamp(value["time"], UTC).isoformat(),
               "received_at": received_at.isoformat()}
    receipt["evidence_sha256"] = content_hash({"receipt": dict(receipt), "peer": list(peer),
        "endpoint": config.onebot_ws_url, "bearer_accepted": True})
    receipt["signature"] = Ed25519PrivateKey.from_private_bytes(bytes.fromhex(
        config.capture_signing_key)).sign(signed_payload(receipt)).hex()
    verify(window, receipt)
    window.spool.capacity(len(encode(receipt).encode()) * 3)
    with window.db:
        window.db.execute("INSERT INTO automatic_received VALUES(?,?,?)",
                          (identity["event_id"], window.plan.sha256, encode(receipt)))
    owner_source.available.set()


def receipt_valid(event_id, result):
    return (isinstance(result, dict) and set(result) <= {
        "event_id", "duplicate", "job_id", "state", "coalesced"}
        and result.get("event_id") == event_id and type(result.get("duplicate")) is bool
        and result.get("state") in {"SUPPRESSED", "QUEUED", "RUNNING", "RETRY_WAIT", "SUCCEEDED",
                                    "FAILED", "CANCELLED"}
        and (result.get("job_id") is None and result["state"] == "SUPPRESSED"
             or isinstance(result.get("job_id"), str)
             and re.fullmatch(r"job_[a-f0-9]{32}", result["job_id"]))
        and ("coalesced" not in result or type(result["coalesced"]) is bool))


async def flush(window, client):
    changed = False
    rows = window.db.execute("SELECT * FROM window_inputs WHERE plan_hash=? "
                             "AND state='POKE_PENDING' ORDER BY rowid",
                             (window.plan.sha256,)).fetchall()
    if rows:
        window.verify(active=False)
        if window.stop_path.exists() or window.row()["state"] == "STOPPED":
            await window.stop_core(client)
    for row in rows:
        receipt = verify(window, json.loads(row["capture_json"]))
        async with window.core_http(client) as scoped:
            status, response = await bounded_post(scoped, "/internal/v1/live-text/pokes", receipt,
                                                  statuses=(200, 410), with_status=True)
        if not (status == 410 and response == {"error": "EVENT_ERASED"}) and not (
            status == 200 and receipt_valid(row["event_id"], response)
        ):
            raise ValueError("INVALID_CORE_POKE_ACK")
        summary = {"kind": "onebot_poke_receipt", "capture_sha256": content_hash(receipt),
                   "input_sha256": content_hash(receipt["notice"]),
                   "evidence_sha256": receipt["evidence_sha256"], "core_receipt": response}
        head = window.append("POKE_ACCEPTED", {"event_hash": content_hash(row["event_id"]),
                                               **summary})
        with window.db:
            window.db.execute("UPDATE window_inputs SET state='ACK',capture_json=?,approval_hash=? "
                               "WHERE event_id=?", (encode(summary), content_hash(response),
                                                    row["event_id"]))
            window.db.execute("UPDATE test_window SET journal_head=?", (head,))
            if window.db.execute("SELECT 1 FROM sqlite_master WHERE name='automatic_received'")\
                    .fetchone():
                window.db.execute("DELETE FROM automatic_received WHERE event_id=?",
                                  (row["event_id"],))
        changed = True
    if changed:
        window.db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    return changed


async def receive(window, client, observed):
    observed.verify(window)
    if not enabled(window):
        raise ValueError("PRIVATE_POKE_SCOPE_REQUIRED")
    receipt = observed.receipt
    if not window.db.execute("SELECT 1 FROM window_inputs WHERE event_id=?", (observed.event_id,))\
            .fetchone():
        capture.capacity(window)
        await window.core_counts(client)
        capture.capacity(window)
        head = window.append("CAPTURED", {"event_hash": content_hash(observed.event_id),
            "capture_sha256": content_hash(receipt), "frame_sha256": receipt["frame_sha256"]})
        with window.db:
            window.db.execute("INSERT INTO window_inputs VALUES(?,?,?,'POKE_PENDING',?,?)",
                (observed.event_id, content_hash(receipt["notice"]), "", encode(receipt),
                 window.plan.sha256))
            window.db.execute("UPDATE test_window SET journal_head=?", (head,))
    await flush(window, client)
    return {"poke_received": observed.event_id}


def admitted(window, event_id):
    row = window.db.execute("SELECT * FROM window_inputs WHERE event_id=? AND plan_hash=? "
                            "AND state='ACK'", (event_id, window.plan.sha256)).fetchone()
    if not row or not row["capture_json"]:
        return False
    value = json.loads(row["capture_json"])
    return (value.get("kind") == "onebot_poke_receipt"
            and receipt_valid(event_id, value.get("core_receipt")))


def check_history(window, history):
    for plan, records in history:
        captured = {r["data"]["event_hash"]: r["data"] for r in records if r["kind"] == "CAPTURED"}
        accepted = [r["data"] for r in records if r["kind"] == "POKE_ACCEPTED"]
        seen = set()
        for row in window.db.execute("SELECT * FROM window_inputs WHERE plan_hash=? "
                                     "AND capture_json IS NOT NULL", (plan.sha256,)):
            value = json.loads(row["capture_json"])
            if value.get("kind") not in {KIND, "onebot_poke_receipt"}:
                continue
            key = content_hash(row["event_id"])
            expected = captured.get(key, {}).get("capture_sha256")
            if value["kind"] == KIND:
                if (row["state"] != "POKE_PENDING" or content_hash(value) != expected
                    or row["input_hash"] != content_hash(value["notice"])
                    or row["approval_hash"]):
                    raise ValueError("POKE_JOURNAL_RECONCILIATION_REQUIRED")
            elif (row["state"] != "ACK" or value["capture_sha256"] != expected
                  or row["input_hash"] != value["input_sha256"]
                  or {"event_hash": key, **value} not in accepted
                  or row["approval_hash"] != content_hash(value["core_receipt"])
                  or not (receipt_valid(row["event_id"], value["core_receipt"])
                          or value["core_receipt"] == {"error": "EVENT_ERASED"})):
                raise ValueError("POKE_JOURNAL_RECONCILIATION_REQUIRED")
            else:
                seen.add(key)
        if len(accepted) != len(seen) or {r["event_hash"] for r in accepted} != seen:
            raise ValueError("POKE_JOURNAL_RECONCILIATION_REQUIRED")
