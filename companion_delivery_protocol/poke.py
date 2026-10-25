"""Original private-poke proof bytes shared by Core and the independent Bridge."""

from datetime import datetime

from companion_delivery_protocol import canonical, hash_value, identifier
from companion_delivery_protocol.authority import validate_authority_ref

OWNER_POKE_DOMAIN = b"companion-core/onebot-owner-poke/v2\x00"


def owner_poke_signing_payload(value):
    """Validate the finite wire shape; policy, provenance and leases stay in Core."""
    fields = {"kind", "authority", "event_id", "source_event_id", "notice", "protocol_epoch",
              "identity_sha256", "config_sha256", "frame_sha256", "evidence_sha256",
              "occurred_at", "received_at"}
    if not isinstance(value, dict) or set(value) != fields:
        raise ValueError("OWNER_POKE_CAPTURE_INVALID")
    result = {**value, "authority": validate_authority_ref(value["authority"])}
    notice = result["notice"]
    if (result["kind"] != "authenticated_onebot_poke"
        or not identifier(result["event_id"], r"evt_[a-f0-9]{64}")
        or not isinstance(result["source_event_id"], str)
        or not 1 <= len(result["source_event_id"]) <= 512
        or not identifier(result["protocol_epoch"], r"[A-Za-z0-9_-]{8,64}")
        or any(not hash_value(result[key]) for key in fields if key.endswith("_sha256"))
        or not isinstance(notice, dict)
        or set(notice) != {"time", "self_id", "user_id", "sender_id", "target_id",
                           "action", "suffix"}
        or type(notice["time"]) is not int or not 1 <= notice["time"] < 2**53
        or any(not identifier(notice[key], r"[1-9][0-9]{4,19}") for key in (
            "self_id", "user_id", "sender_id", "target_id"))
        or any(not isinstance(notice[key], str) or len(notice[key]) > 80
               or any(ord(char) < 32 or ord(char) == 127 for char in notice[key])
               for key in ("action", "suffix"))):
        raise ValueError("OWNER_POKE_CAPTURE_INVALID")
    for key in ("occurred_at", "received_at"):
        if not isinstance(result[key], str) or len(result[key]) > 48:
            raise ValueError("OWNER_POKE_CAPTURE_INVALID")
        parsed = datetime.fromisoformat(result[key])
        if parsed.utcoffset() is None:
            raise ValueError("OWNER_POKE_CAPTURE_INVALID")
        result[key] = parsed.isoformat()
    return OWNER_POKE_DOMAIN + canonical(result)
