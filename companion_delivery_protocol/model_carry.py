"""Explicit retained model liability; never an input-terminal or settlement proof."""

from companion_delivery_protocol import canonical, digest, hash_value, identifier
from companion_delivery_protocol.authority import validate_authority_ref

KIND = "owner_model_unknown_carry_v1"
VERSION = 1
LOCAL_KIND = "owner_model_unknown_carry_v2"
MODE = "local_active_attempts_v2"
FIELDS = {
    "kind", "version", "state", "authority", "core_stop_sha256", "budget_journal_id",
    "carry_sha256", "record_sha256", "successor_policy_sha256", "successor_window_id",
    "inputs", "request_ids", "retained_micro_usd", "closed_executors_sha256", "receipt_sha256",
}
INPUT_FIELDS = {"event_id", "input_sha256", "work_sha256", "request_ids"}
LOCAL_FIELDS = FIELDS | {"origin_authority", "local_closures"}


def _local(value):
    validate_authority_ref(value["origin_authority"])
    origin, current = value["origin_authority"], value["authority"]
    closures = value["local_closures"]
    if (origin["grant_id"] != current["grant_id"]
        or origin["restore_generation"] != current["restore_generation"]
        or current["revision"] < origin["revision"] + 2
        or current["policy_sha256"] != value["successor_policy_sha256"]
        or not isinstance(closures, list) or not 1 <= len(closures) <= 100
        or any(not isinstance(item, dict) or set(item) != {"request_id", "record_sha256"}
               or not hash_value(item["record_sha256"]) for item in closures)
        or [item["request_id"] for item in closures] != value["request_ids"]
        or digest(closures) != value["closed_executors_sha256"]):
        raise ValueError("OWNER_MODEL_CARRY_RECEIPT_INVALID")


def receipt(value):
    if not isinstance(value, dict) or type(value.get("version")) is not int:
        raise ValueError("OWNER_MODEL_CARRY_RECEIPT_INVALID")
    local = value["version"] == 2
    if (set(value) != (LOCAL_FIELDS if local else FIELDS)
        or value["kind"] != (LOCAL_KIND if local else KIND)
        or value["version"] != (2 if local else VERSION)
        or value["state"] != "PREPARED"
        or not identifier(value["budget_journal_id"], r"budget_[a-f0-9]{32}")
        or not identifier(value["successor_window_id"], r"[A-Za-z0-9_-]{8,96}")
        or type(value["retained_micro_usd"]) is not int or value["retained_micro_usd"] <= 0
        or any(not hash_value(value[k]) for k in FIELDS if k.endswith("sha256"))
        or not isinstance(value["inputs"], list) or not 1 <= len(value["inputs"]) <= 100
        or not isinstance(value["request_ids"], list) or not 1 <= len(value["request_ids"]) <= 100
        or value["request_ids"] != sorted(set(value["request_ids"]))
        or any(not identifier(k, r"model_[a-f0-9]{32}") for k in value["request_ids"])):
        raise ValueError("OWNER_MODEL_CARRY_RECEIPT_INVALID")
    validate_authority_ref(value["authority"])
    if local:
        _local(value)
    events, requests = [], set()
    for item in value["inputs"]:
        if (not isinstance(item, dict) or set(item) != INPUT_FIELDS
            or not identifier(item["event_id"], r"evt_[a-f0-9]{64}")
            or not hash_value(item["input_sha256"]) or not hash_value(item["work_sha256"])
            or not isinstance(item["request_ids"], list) or not item["request_ids"]
            or item["request_ids"] != sorted(set(item["request_ids"]))
            or not set(item["request_ids"]) <= set(value["request_ids"])):
            raise ValueError("OWNER_MODEL_CARRY_RECEIPT_INVALID")
        events.append(item["event_id"])
        requests.update(item["request_ids"])
    if (events != sorted(set(events)) or requests != set(value["request_ids"])
        or value["receipt_sha256"] != digest({k: v for k, v in value.items()
                                             if k != "receipt_sha256"})
        or len(canonical(value)) > 60 * 1024):
        raise ValueError("OWNER_MODEL_CARRY_RECEIPT_INVALID")
    return value


def carried(state, capture, proof):
    """Validate original nonterminal evidence without changing any of its booleans."""
    receipt(proof)
    fields = {"authority", "event_id", "input_sha256", "reply_revision", "terminal", "state",
              "unsettled_work", "unsettled_model", "unsettled_delivery", "work_sha256"}
    matches = [item for item in proof["inputs"] if item["event_id"] == capture["event_id"]]
    if (not isinstance(state, dict) or set(state) != fields or len(matches) != 1
        or proof["authority"] != capture["authority"]
        or any(state[k] != capture[k] for k in ("authority", "event_id", "input_sha256"))
        or state["terminal"] is not False or state["unsettled_model"] is not True
        or state["unsettled_work"] is not False or state["unsettled_delivery"] is not False
        or state["state"] not in {"PENDING", "CANCELLED", "RECALLED"}
        or type(state["reply_revision"]) is not int or state["reply_revision"] < 0
        or any(state[k] != matches[0][k] for k in ("input_sha256", "work_sha256"))):
        raise ValueError("OWNER_MODEL_CARRY_INPUT_CHANGED")
    return state
