"""Signed owner input evidence; durable policy and current execution are separate checks."""

import hashlib
import json
import re
from datetime import datetime

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from companion import cumulative, input_recall, owner_authority
from companion.contracts import CoreError
from companion.owner_authority_protocol import OwnerCapture
from companion.privacy import epoch
from companion.qq_rich import input_digest
from companion.store import encode
from companion_delivery_protocol.authority import capture_signing_payload

INPUT = "owner_input:"


def verify(store, settings, value):
    """Verify provenance with the original revision, including after STOP or restore."""
    try:
        capture = OwnerCapture.model_validate(value)
        view = owner_authority.evidence(store, settings, capture.authority)
        identity = view.policy.capture
        if identity is None:
            raise ValueError("capture identity absent")
        signed = capture.model_dump(mode="json", exclude={"signature"})
        Ed25519PublicKey.from_public_bytes(bytes.fromhex(identity.public_key)).verify(
            bytes.fromhex(capture.signature), capture_signing_payload(signed))
        prefix = (f"{settings.bot_qq_id}:onebot-direct:FriendMessage:{view.policy.owner_qq_id}:"
                  f"epoch:{identity.protocol_epoch}:")
        row = store.one("SELECT created_at FROM owner_authority_revisions "
                        "WHERE grant_id=? AND revision=?",
                        (view.authority.grant_id, view.authority.revision))
        if (
            capture.identity_sha256 != identity.identity_sha256
            or capture.protocol_epoch != identity.protocol_epoch
            or not capture.source_event_id.startswith(prefix)
            or not re.fullmatch(r"-?[1-9][0-9]{0,19}", capture.source_event_id[len(prefix):])
            or capture.event_id != "evt_" + hashlib.sha256(
                capture.source_event_id.encode()).hexdigest()
            or not datetime.fromisoformat(row["created_at"]) <= capture.occurred_at
            or not 0 <= (capture.received_at - capture.occurred_at).total_seconds() <= 30
            or (view.policy.expires_at is not None
                and capture.received_at >= view.policy.expires_at)
        ):
            raise ValueError("capture binding changed")
    except (ValueError, KeyError, TypeError, InvalidSignature):
        raise CoreError("OWNER_CAPTURE_INVALID", 403) from None
    return capture, view


def approve(store, settings, clock, capture):
    """Admit one authenticated frame through the original cumulative input accounting."""
    with store.transaction():
        view = owner_authority.current(store, settings, clock, purpose="chat",
                                       authority=capture.authority)
        value = capture.model_dump(mode="json")
        verify(store, settings, value)
        now = datetime.fromisoformat(clock())
        if (capture.received_at - now).total_seconds() > 2:
            raise CoreError("OWNER_CAPTURE_FUTURE", 403)
        if (now - capture.received_at).total_seconds() >= (
            86400 * min(view.policy.source_retention_days, settings.raw_event_retention_days)
        ):
            raise CoreError("OWNER_INPUT_EXPIRED", 403)
        input_recall.require_not_recalled(store, capture.source_event_id)
        if store.one("SELECT 1 FROM runtime_meta WHERE key=?",
                     ("erased_event:" + capture.event_id,)):
            raise CoreError("EVENT_ERASED", 410)
        key = INPUT + capture.event_id
        prior = store.one("SELECT value FROM runtime_meta WHERE key=?", (key,))
        if prior:
            recorded = json.loads(prior["value"])
            if recorded.get("capture") != value:
                raise CoreError("OWNER_INPUT_CONFLICT", 409)
            return {"approved": True, "duplicate": True}
        if store.one("SELECT 1 FROM events WHERE event_id=?", (capture.event_id,)):
            raise CoreError("OWNER_HISTORICAL_INPUT_REFUSED", 409)
        cumulative.admit_operation(store, settings, clock(), "input", capture.event_id,
                                    owner_authority._sha(value), at=capture.received_at)
        store.insert("runtime_meta", key=key, value=encode({
            "authority": view.authority.as_dict(), "event_id": capture.event_id,
            "input_sha256": capture.input_sha256, "privacy_epoch": epoch(store),
            "capture": value,
        }))
        return {"approved": True, "duplicate": False}


def check_input(store, settings, clock, event):
    """Bind an inbound event to its already admitted, signed capture before storing it."""
    view = owner_authority.current(store, settings, clock, purpose="chat")
    receipt = store.one("SELECT value FROM runtime_meta WHERE key=?", (INPUT + event.event_id,))
    try:
        value = json.loads(receipt["value"]) if receipt else {}
        capture, _ = verify(store, settings, value["capture"])
        body = event.payload.model_dump(mode="json")
        if (
            capture.authority != view.authority.as_dict()
            or value.get("authority") != capture.authority
            or value.get("event_id") != event.event_id
            or capture.event_id != event.event_id
            or capture.source_event_id != event.source_event_id
            or capture.occurred_at != event.occurred_at
            or capture.input_sha256 != input_digest(body)
            or value.get("input_sha256") != capture.input_sha256
            or type(value.get("privacy_epoch")) is not int
            or not 0 <= value["privacy_epoch"] <= epoch(store)
            or event.source != "qq.onebot" or event.event_type != "message.received"
            or event.actor.platform_user_id != view.policy.owner_qq_id
            or event.conversation.platform_session_key != (
                "onebot-direct:FriendMessage:" + view.policy.owner_qq_id)
            or body.get("media")
            or len(body["text"]) > view.policy.max_input_chars
            or not (body["text"].strip() or body.get("rich"))
            or body.get("rich") and settings.rich_input_scope != "references_v1"
            or any(item.kind not in view.policy.purposes for item in event.payload.shared_content)
        ):
            raise ValueError("capture binding changed")
        now = datetime.fromisoformat(clock())
        if (now - capture.received_at).total_seconds() >= (
            86400 * min(view.policy.source_retention_days, settings.raw_event_retention_days)
        ) or (capture.received_at - now).total_seconds() > 2:
            raise ValueError("capture outside source lifetime")
    except (ValueError, KeyError, TypeError):
        raise CoreError("OWNER_APPROVED_INPUT_REQUIRED", 403) from None
    input_recall.require_not_recalled(store, event.source_event_id)
    if store.one("SELECT 1 FROM runtime_meta WHERE key=?", ("erased_event:" + event.event_id,)):
        raise CoreError("EVENT_ERASED", 410)
    return view


def source(store, settings, view, event_id, now, *, allow_commands=False, history=False):
    """Read an admitted private source; history requires an explicit current source scope."""
    view = owner_authority.current(store, settings, lambda: now,
        purpose="history" if history else "chat", authority=view.authority)
    event = store.one("SELECT e.*,c.scope,c.owner_actor_id FROM events e "
                      "JOIN conversations c USING(conversation_id) WHERE event_id=?", (event_id,))
    receipt = store.one("SELECT value FROM runtime_meta WHERE key=?", (INPUT + event_id,))
    if history and receipt is None and store.one(
        "SELECT 1 FROM runtime_meta WHERE key=?", ("live_text_input:" + event_id,)
    ):
        from companion import legacy_owner_input

        return legacy_owner_input.source(store, settings, view, event_id, now,
                                          allow_commands=allow_commands)
    try:
        value = json.loads(receipt["value"]) if receipt else {}
        body = json.loads(event["body_json"]) if event else {}
        if (not event or event["redacted"] or not isinstance(body, dict)
            or (event["source"], event["actor_id"], event["scope"], event["owner_actor_id"])
            != ("qq.onebot", "owner", "private", "owner")
            or event["privacy_scope"] != "private"
            or event["event_type"] != "message.received" or body.get("media")
            or not isinstance(body.get("text"), str)
            or not allow_commands and (body.get("control_command") or body.get("diary_command")
                                       or body["text"].strip().startswith("/"))
            or value.get("event_id") != event_id
            or value.get("input_sha256") != input_digest(body)
            or type(value.get("privacy_epoch")) is not int
            or not 0 <= value["privacy_epoch"] <= epoch(store)):
            raise ValueError("source changed")
        capture, original = verify(store, settings, value["capture"])
        if (value.get("authority") != original.authority.as_dict()
            or capture.event_id != event_id or capture.input_sha256 != value["input_sha256"]
            or capture.source_event_id != event["source_event_id"]
            or capture.occurred_at != datetime.fromisoformat(event["occurred_at"])):
            raise ValueError("capture changed")
        if history:
            scope = view.policy.history
            if ("history" not in view.policy.purposes or scope.scope != "owner_private"
                or capture.occurred_at < scope.not_before):
                raise ValueError("history scope required")
        elif original.authority != view.authority:
            raise ValueError("current source revision changed")
        lifetime = 86400 * min(original.policy.source_retention_days,
                               view.policy.source_retention_days, settings.raw_event_retention_days)
        if (datetime.fromisoformat(now) - capture.received_at).total_seconds() >= lifetime:
            raise ValueError("source expired")
    except (ValueError, KeyError, TypeError):
        raise CoreError("OWNER_SOURCE_CHANGED", 403) from None
    input_recall.require_not_recalled(store, event["source_event_id"])
    jobs = store.rows("SELECT * FROM jobs WHERE event_id=? AND kind='chat'", (event_id,))
    if len(jobs) != 1 or json.loads(jobs[0]["authority_json"]) != original.authority.as_dict():
        raise CoreError("OWNER_SOURCE_JOB_BINDING_REQUIRED", 403)
    return event, body, jobs[0]


def history_sources(store, settings, clock, job):
    """Apply explicit current history bounds to native source-bound dialogue turns."""
    from companion.dialogue_history import recent_context_sources, turn_messages

    view = owner_authority.check_job(store, settings, clock, job, purpose="chat")
    if "history" not in view.policy.purposes:
        return []
    scope = view.policy.history
    result = []
    for item in recent_context_sources(store, job["conversation_id"], job["event_id"],
                                       scope.max_turns):
        try:
            source(store, settings, view, item["event_id"], clock(), history=True)
            if not store.one("SELECT 1 FROM runtime_meta WHERE key=?", (INPUT + item["event_id"],)):
                from companion import legacy_owner_input

                legacy_owner_input.validate_turn(store, settings, view, item, clock())
        except CoreError:
            continue
        result.append(item)
    while result and sum(len(message["content"].encode()) for item in result
                         for message in turn_messages(item)) > scope.max_utf8_bytes:
        result.pop(0)
    return result


def erase(store, event_ids):
    for event_id in event_ids:
        store.execute("DELETE FROM runtime_meta WHERE key=?", (INPUT + event_id,))
