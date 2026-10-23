"""Owner inbound source identities and durable recall entry points.

The HTTP boundary verifies authenticated notice capture before using this module.
A notice is only matched to a private owner input, never an outgoing provider ID.
The external ErasureLedger owns durability; runtime_meta is a rebuildable index.
"""

import hashlib
import json
import re
from contextlib import nullcontext

from companion.contracts import CoreError
from companion.store import encode

PREFIX = "input_recall:"


def canonical_source(
    *, bot_qq_id, owner_qq_id, protocol_epoch, provider_message_id, platform_session_key=None,
):
    for identity in (bot_qq_id, owner_qq_id):
        if not isinstance(identity, str) or not re.fullmatch(r"[1-9][0-9]{4,19}", identity):
            raise ValueError("INVALID_RECALL_IDENTITY")
    if bot_qq_id == owner_qq_id:
        raise ValueError("INVALID_RECALL_IDENTITY")
    if not isinstance(protocol_epoch, str) or not re.fullmatch(
        r"[A-Za-z0-9_-]{8,64}", protocol_epoch,
    ):
        raise ValueError("INVALID_RECALL_PROTOCOL_EPOCH")
    if type(provider_message_id) is int:
        provider_message_id = str(provider_message_id)
    if not isinstance(provider_message_id, str) or not re.fullmatch(
        r"-?[1-9][0-9]{0,19}", provider_message_id,
    ):
        raise ValueError("INVALID_RECALL_MESSAGE_ID")
    origin = f"onebot-direct:FriendMessage:{owner_qq_id}"
    if platform_session_key is not None and platform_session_key != origin:
        raise ValueError("INVALID_RECALL_PRIVATE_SESSION")
    return {
        "version": 1,
        "source": "qq.onebot",
        "source_event_id": f"{bot_qq_id}:{origin}:epoch:{protocol_epoch}:{provider_message_id}",
        "bot_qq_id": bot_qq_id,
        "owner_qq_id": owner_qq_id,
        "platform_session_key": origin,
        "protocol_epoch": protocol_epoch,
        "provider_message_id": provider_message_id,
    }


def validate_source(source):
    if not isinstance(source, dict):
        raise ValueError("INVALID_RECALL_SOURCE")
    expected = canonical_source(**{key: source.get(key) for key in (
        "bot_qq_id", "owner_qq_id", "protocol_epoch", "provider_message_id",
        "platform_session_key",
    )})
    if source != expected or type(source.get("version")) is not int:
        raise ValueError("INVALID_RECALL_SOURCE")
    return expected


def marker_key(source_event_id):
    return PREFIX + hashlib.sha256(source_event_id.encode()).hexdigest()


def is_recalled(store, source_event_id):
    if source_event_id.startswith("evt_"):
        return bool(store.one("SELECT 1 FROM runtime_meta WHERE key=?",
                              ("erased_event:" + source_event_id,)))
    row = store.one("SELECT value FROM runtime_meta WHERE key=?", (marker_key(source_event_id),))
    return bool(row and json.loads(row["value"])["state"] == "RECALLED")


def require_not_recalled(store, source_event_id):
    if is_recalled(store, source_event_id):
        # Existing Spools only consume this exact, authenticated 410 terminal receipt.
        raise CoreError("EVENT_ERASED", 410)


def matching_events(store, source):
    return {row["event_id"] for row in store.rows(
        "SELECT e.event_id FROM events e JOIN conversations c USING(conversation_id) "
        "WHERE e.source=? AND e.source_event_id=? AND e.actor_id='owner' "
        "AND e.privacy_scope='private' AND e.event_type IN "
        "('message.received','message.media_received') AND c.platform=? "
        "AND c.platform_session_key=? AND c.scope='private' AND c.owner_actor_id='owner'",
        (source["source"], source["source_event_id"], source["source"],
         source["platform_session_key"]),
    )}


def project_marker(store, record):
    source = record["recall_source"]
    if record["state"] == "PENDING_TARGET" and is_recalled(store, source["source_event_id"]):
        return
    store.execute("INSERT OR REPLACE INTO runtime_meta VALUES (?,?)", (
        marker_key(source["source_event_id"]),
        encode({**source, "erasure_id": record["erasure_id"], "state": record["state"],
                "occurred_at": record["occurred_at"], "event_id": record.get("event_id")}),
    ))


def recall_input(
    store, ledger, source, now, *, retention_cutoff=None, verified=False, occurred_at=None,
    event_id=None,
):
    try:
        validate_source(source)
    except ValueError:
        raise CoreError("INPUT_RECALL_SOURCE_INVALID", 400) from None
    with nullcontext() if store.db.in_transaction else store.transaction():
        owner = store.one("SELECT value FROM runtime_meta WHERE key='owner_qq_id'")
        if owner and owner["value"] != source["owner_qq_id"]:
            raise CoreError("OWNER_REQUIRED", 403)
        return ledger.recall_input(
            store, source, now, retention_cutoff=retention_cutoff, verified=verified,
            occurred_at=occurred_at, event_id=event_id,
        )
