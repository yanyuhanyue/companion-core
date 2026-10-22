"""Pure DB proof of a signed current-owner input; callers own the live-window guard."""

import hashlib
import json
import re
from datetime import datetime

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from companion import paid_budget
from companion.contracts import CoreError
from companion.privacy import epoch
from companion.qq_rich import input_digest
from companion.store import encode


def source(store, settings, approval, event_id, *, allow_commands=False):
    event = store.one(
        "SELECT e.*,c.scope,c.owner_actor_id FROM events e "
        "JOIN conversations c USING(conversation_id) WHERE e.event_id=?", (event_id,),
    )
    receipt = store.one(
        "SELECT value FROM runtime_meta WHERE key=?", ("live_text_input:" + event_id,),
    )
    try:
        value = json.loads(receipt["value"]) if receipt else {}
        body = json.loads(event["body_json"]) if event else {}
        capture = value.get("capture")
        if (
            not event or event["redacted"] or not isinstance(body, dict)
            or (event["source"], event["actor_id"], event["scope"], event["owner_actor_id"])
            != ("qq.onebot", "owner", "private", "owner")
            or event["event_type"] != "message.received"
            or ((body.get("control_command") or body.get("diary_command")) and not allow_commands)
            or body.get("media")
            or not isinstance(body.get("text"), str)
            or (body["text"].strip().startswith("/") and not allow_commands)
            or value.get("event_id") != event_id
            or value.get("window_sha256") != approval.plan_sha256
            or (value.get("privacy_epoch") != epoch(store) and not (
                allow_commands and (body.get("control_command") or body.get("diary_command"))
                and type(value.get("privacy_epoch")) is int
                and value["privacy_epoch"] < epoch(store)))
            or value.get("input_sha256") != input_digest(body)
            or not isinstance(capture, dict)
            or capture.get("source_event_id") != event["source_event_id"]
        ):
            raise ValueError()
    except (ValueError, KeyError, TypeError, AttributeError):
        raise CoreError("LIVE_SOURCE_CHANGED", 403) from None
    try:
        signed = {key: item for key, item in capture.items() if key != "signature"}
        for key in ("received_at", "occurred_at"):
            signed[key] = datetime.fromisoformat(capture[key]).isoformat()
        Ed25519PublicKey.from_public_bytes(bytes.fromhex(approval.capture_public_key)).verify(
            bytes.fromhex(capture["signature"]),
            b"companion-core/onebot-capture/v1\x00" + encode(signed).encode(),
        )
        received, occurred = (
            datetime.fromisoformat(capture[key]) for key in ("received_at", "occurred_at")
        )
        prefix = (
            f"{settings.bot_qq_id}:onebot-direct:FriendMessage:{settings.owner_qq_id}:"
            f"epoch:{capture['protocol_epoch']}:"
        )
        if (
            capture["kind"] != "authenticated_onebot_ws"
            or capture["window_sha256"] != approval.plan_sha256
            or capture["event_id"] != event_id
            or capture["input_sha256"] != input_digest(body)
            or capture["identity_sha256"]
            != paid_budget.sha([settings.owner_qq_id, settings.bot_qq_id])
            or not re.fullmatch(r"[A-Za-z0-9_-]{8,64}", capture["protocol_epoch"])
            or not capture["source_event_id"].startswith(prefix)
            or not re.fullmatch(r"-?[1-9][0-9]{0,19}", capture["source_event_id"][len(prefix):])
            or event_id != "evt_" + hashlib.sha256(capture["source_event_id"].encode()).hexdigest()
            or occurred != datetime.fromisoformat(event["occurred_at"])
            or not approval.starts_at <= occurred <= received < approval.ends_at
            or not 0 <= (received - occurred).total_seconds() <= 30
        ):
            raise ValueError()
    except (ValueError, KeyError, TypeError, InvalidSignature):
        raise CoreError("LIVE_SOURCE_CAPTURE_CHANGED", 403) from None
    jobs = store.rows("SELECT * FROM jobs WHERE event_id=? AND kind='chat'", (event_id,))
    if len(jobs) != 1:
        raise CoreError("LIVE_SOURCE_CHAT_JOB_REQUIRED", 403)
    return event, body, jobs[0]
