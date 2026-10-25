"""Signed native notices in the original Spool; they never become owner text."""

import hashlib
import json
from datetime import UTC, datetime

import httpx
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from companion_delivery_protocol.poke import owner_poke_signing_payload

from . import journal, pokes
from .event_normalizer import decode
from .onebot_client import bounded_post
from .spool import encode


async def put(runtime, client, frame, received_at, peer):
    raw = decode(frame, runtime.config.max_frame_bytes, max_depth=20)
    if (raw.get("post_type"), raw.get("notice_type"), raw.get("sub_type")) != (
        "notice", "notify", "poke",
    ):
        return False
    value = pokes.notice(raw, runtime.config)
    occurred = datetime.fromtimestamp(value["time"], UTC)
    if not 0 <= (received_at - occurred).total_seconds() <= 30:
        raise ValueError("OWNER_POKE_TIME_CHANGED")
    try:
        await runtime.check(client, purpose="poke")
    except httpx.HTTPStatusError as error:
        if (error.response.status_code == 403 and error.response.json().get("error")
            == "OWNER_AUTHORITY_PURPOSE_REQUIRED"):
            return False
        raise
    identity = pokes.source(value, runtime.config)
    if runtime.db.execute("SELECT 1 FROM window_inputs WHERE event_id=?",
                           (identity["event_id"],)).fetchone():
        return True
    frame_bytes = frame.encode() if isinstance(frame, str) else frame
    receipt = {"kind": "authenticated_onebot_poke", "authority": runtime.authority,
        **identity, "notice": value, "protocol_epoch": runtime.config.protocol_epoch,
        "identity_sha256": journal.digest([runtime.config.owner_qq_id, runtime.config.bot_qq_id]),
        "config_sha256": journal.digest(runtime.config.snapshot()),
        "frame_sha256": hashlib.sha256(frame_bytes).hexdigest(),
        "occurred_at": occurred.isoformat(), "received_at": received_at.isoformat()}
    receipt["evidence_sha256"] = journal.digest({"receipt": dict(receipt), "peer": list(peer),
        "endpoint": runtime.config.onebot_ws_url, "bearer_accepted": True})
    key = Ed25519PrivateKey.from_private_bytes(bytes.fromhex(runtime.config.capture_signing_key))
    receipt["signature"] = key.sign(owner_poke_signing_payload(receipt)).hex()
    if runtime.spool.pending_count >= runtime.config.max_pending:
        raise ValueError("SPOOL_QUEUE_CAPACITY")
    body = encode(receipt)
    runtime.spool.capacity(len(body.encode()) * 3)
    runtime.verify()
    event_id, capture_hash = identity["event_id"], journal.digest(receipt)
    with runtime.db:
        runtime.append("CAPTURED", {"event_id": event_id, "capture_sha256": capture_hash})
        runtime.db.execute("INSERT INTO window_inputs VALUES(?,?,?,'CAPTURED',?,?)",
            (event_id, journal.digest(value), capture_hash, body,
             journal.digest(runtime.authority)))
        runtime.db.execute("INSERT INTO inbox(key,fingerprint,body,trace) VALUES(?,?,?,?)",
            (event_id, journal.digest(value), body, "trace_" + event_id.removeprefix("evt_")))
    return True


async def flush(runtime, client, receipt):
    result = await bounded_post(client, "/internal/v1/pokes", receipt)
    if (not isinstance(result, dict) or set(result) not in (
        {"event_id", "duplicate", "job_id", "state"},
        {"event_id", "duplicate", "job_id", "state", "coalesced"})
        or result["event_id"] != receipt["event_id"] or type(result["duplicate"]) is not bool
        or "coalesced" in result and type(result["coalesced"]) is not bool
        or result["state"] not in {
            "SUPPRESSED", "QUEUED", "RUNNING", "SUCCEEDED", "FAILED", "CANCELLED"}):
        raise ValueError("OWNER_POKE_RECEIPT_INVALID")
    runtime.verify(active=False)
    with runtime.db:
        runtime.db.execute("UPDATE inbox SET state='accepted',body=NULL WHERE key=?",
                           (receipt["event_id"],))
    return {"event_id": receipt["event_id"],
            "trace_id": "trace_" + receipt["event_id"].removeprefix("evt_")}


def check_body(receipt, body):
    if receipt["kind"] == "authenticated_onebot_poke":
        if json.loads(body) != receipt:
            raise ValueError("OWNER_POKE_PROJECTION_CHANGED")
        return True
    return False
