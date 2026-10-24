"""Bounded owner-resolution wire values, with no database or delivery authority.

Receipt hashes detect changes. Authenticity comes from a direct authenticated Core
lookup; accepting an arbitrary offline receipt is deliberately not a wire operation.
"""

import hashlib
import json
import re
from datetime import datetime

QUIESCENCE_DOMAIN = b"companion-core/delivery-quiescence/v1\x00"
QUIESCENCE_KIND = "delivery_quiescence_v1"
RECEIPT_KIND = "owner_delivery_resolution_v1"
PRIVATE_ACTIONS = {"qq.private.reply", "qq.private.proactive", "qq.private.poke"}
ACCOUNT_ACTIONS = {
    "qzone.publish", "qq.profile.nickname", "qq.profile.signature", "qq.profile.avatar",
}
ACTIONS = PRIVATE_ACTIONS | ACCOUNT_ACTIONS
MAX_INPUT_DELIVERIES = 100
MAX_RECEIPT_BYTES = 60 * 1024
JOB_TERMINAL = {"SUCCEEDED", "FAILED", "CANCELLED"}
DELIVERY_TERMINAL = {"SENT", "FAILED", "CANCELLED"}
SHA = re.compile(r"[a-f0-9]{64}")
PROOF_FIELDS = {
    "kind", "version", "window_id", "plan_sha256", "budget_journal_id", "message_id",
    "event_id", "action_type", "fence", "item_sha256", "content_sha256", "payload_sha256",
    "payload_kind", "lease_sha256", "send_check_sha256", "reply_revision", "attempt_state",
    "attempt_record_sha256", "reserved_record_sha256", "stop_record_sha256",
    "core_stop_record_sha256", "bridge_journal_head_sha256", "original_result_sha256",
    "stopped_at", "quiesced_at", "signature",
}
DELIVERY_FIELDS = {
    "message_id", "event_id", "action_type", "content_sha256", "delivery_key_sha256",
    "authority_sha256", "attempt_count", "created_at", "lease_until", "original_state",
}
ACCOUNT_PROOF_FIELDS = (PROOF_FIELDS - {"event_id", "reply_revision"}) | {
    "account_plan_id", "authorization_sha256",
}
ACCOUNT_DELIVERY_FIELDS = DELIVERY_FIELDS | {
    "account_plan_id", "authorization_sha256", "payload_sha256", "reservation_sha256",
    "account_receipt_sha256",
}
RECEIPT_FIELDS = {
    "kind", "version", "state", "outcome", "at", "journal_id", "record_sha256",
    "account_ref", "policy_sha256", "owner_operation_id", "owner_operation_sha256",
    "source_sha256", "prior_liability_sha256", "prior_quota_state", "core_stop_sha256",
    "delivery", "quiescence", "paused", "receipt_sha256",
    "input_closure",
}
INPUT_CLOSURE_FIELDS = {
    "version", "event_id", "source_sha256", "input_sha256", "job", "work_sha256", "complete",
    "unsettled", "deliveries", "terminal",
}
CLOSURE_DELIVERY_FIELDS = {
    "message_id", "state", "attempt_count", "content_sha256", "authority_sha256",
    "owner_resolution_sha256", "provider_receipt_sha256",
}
VOICE_CHECK_FIELDS = {
    "binding_sha256", "job_id", "generation", "result_sha256", "audio_sha256", "audio_bytes",
    "retirement_token",
}


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()


def digest(value):
    return hashlib.sha256(canonical(value)).hexdigest()


def timestamp(value):
    if not isinstance(value, str) or len(value) > 48:
        raise ValueError("DELIVERY_RESOLUTION_TIMESTAMP_INVALID")
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("DELIVERY_RESOLUTION_TIMESTAMP_INVALID")
    return parsed


def identifier(value, pattern):
    return isinstance(value, str) and re.fullmatch(pattern, value) is not None


def hash_value(value):
    return isinstance(value, str) and SHA.fullmatch(value) is not None


def owner_operation(operation_id, operation_sha256):
    if (not isinstance(operation_id, str) or not 1 <= len(operation_id) <= 128
        or not operation_id.isascii() or any(ord(c) < 33 or ord(c) == 127 for c in operation_id)
        or not hash_value(operation_sha256)):
        raise ValueError("DELIVERY_OWNER_OPERATION_INVALID")


def validate_quiescence(value, *, signed=True):
    """Validate shape only; the Core verifies the frozen Bridge Ed25519 key."""
    native = isinstance(value, dict) and value.get("version") == 2
    account = isinstance(value, dict) and value.get("action_type") in ACCOUNT_ACTIONS
    fields = ACCOUNT_PROOF_FIELDS if account else PROOF_FIELDS
    if native:
        from companion_delivery_protocol.authority import validate_authority_ref

        fields = (fields - {"window_id", "plan_sha256"}) | {"authority"}
    voice = isinstance(value, dict) and "voice_check" in value
    if voice and not account:
        fields = fields | {"voice_check"}
    fields = fields if signed else fields - {"signature"}
    if (not isinstance(value, dict) or set(value) != fields
        or value["kind"] != ("owner_delivery_quiescence_v2" if native else QUIESCENCE_KIND)
        or type(value["version"]) is not int
        or value["version"] != (2 if native else 1) or value["action_type"] not in ACTIONS
        or not native and not identifier(value["window_id"], r"win_[a-f0-9]{32}")
        or not identifier(value["budget_journal_id"], r"budget_[a-f0-9]{32}")
        or not identifier(value["message_id"], r"msg_[a-f0-9]{32}")
        or not identifier(value["account_plan_id"] if account else value["event_id"],
                          r"[A-Za-z0-9_:-]{8,128}")
        or type(value["fence"]) is not int or not 1 <= value["fence"] <= 2**53 - 1
        or value["payload_kind"] not in {"reply_sha256", "content_sha256", "account_payload_sha256"}
        or value["attempt_state"] not in {"RESERVED", "IO_STARTED", "UNKNOWN"}
        or (not account and value["reply_revision"] is not None and (
            type(value["reply_revision"]) is not int or not 0 <= value["reply_revision"] < 2**53))
        or any(not hash_value(value[name]) for name in fields if name.endswith("_sha256")
               and name not in {"original_result_sha256", "content_sha256"})
        or any(value[name] is not None and not hash_value(value[name])
               for name in ("original_result_sha256", "content_sha256"))
        or (signed and not identifier(value["signature"], r"[a-f0-9]{128}"))):
        raise ValueError("DELIVERY_QUIESCENCE_INVALID")
    if native:
        validate_authority_ref(value["authority"])
    if (account != (value["payload_kind"] == "account_payload_sha256")
        or account and value["content_sha256"] is None
        or (value["action_type"] == "qq.private.poke")
        != (value["payload_kind"] == "content_sha256")
        or timestamp(value["quiesced_at"]) < timestamp(value["stopped_at"])):
        raise ValueError("DELIVERY_QUIESCENCE_INVALID")
    if value["payload_kind"] == "content_sha256" and (
        value["payload_sha256"] != value["content_sha256"] or value["reply_revision"] is not None
    ):
        raise ValueError("DELIVERY_QUIESCENCE_INVALID")
    if voice:
        if account or value["payload_kind"] != "reply_sha256" or value["reply_revision"] is None:
            raise ValueError("DELIVERY_QUIESCENCE_INVALID")
        validate_voice_check(value["voice_check"], allow_legacy=True)
    return dict(value)


def validate_voice_check(value, *, allow_legacy=False):
    """Current checks plus historical signed v1 proofs; live callers require v2 equality."""
    if (not isinstance(value, dict) or set(value) not in (
            VOICE_CHECK_FIELDS, VOICE_CHECK_FIELDS - {"retirement_token"}
            if allow_legacy else VOICE_CHECK_FIELDS)
        or not identifier(value["job_id"], r"voice_[a-f0-9]{32}")
        or type(value["generation"]) is not int or not 1 <= value["generation"] <= 2**63 - 1
        or type(value["audio_bytes"]) is not int or not 44 <= value["audio_bytes"] <= 4_000_000
        or any(not hash_value(value[k]) for k in VOICE_CHECK_FIELDS if k.endswith("_sha256"))
        or "retirement_token" in value and not identifier(value["retirement_token"],
                                                          r"[a-zA-Z0-9_-]{32,256}")):

        raise ValueError("DELIVERY_VOICE_CHECK_INVALID")
    return dict(value)


def signing_payload(value):
    payload = {k: v for k, v in value.items() if k != "signature"}
    validate_quiescence(payload, signed=False)
    if value.get("version") == 2:
        from companion_delivery_protocol.authority import QUIESCENCE_DOMAIN as owner_domain

        return owner_domain + canonical(payload)
    return QUIESCENCE_DOMAIN + canonical(payload)


def validate_delivery(value):
    account = isinstance(value, dict) and value.get("action_type") in ACCOUNT_ACTIONS
    fields = ACCOUNT_DELIVERY_FIELDS if account else DELIVERY_FIELDS
    if (not isinstance(value, dict) or set(value) != fields
        or not identifier(value["message_id"], r"msg_[a-f0-9]{32}")
        or not (account and value["event_id"] is None)
            and not identifier(value["event_id"], r"[A-Za-z0-9_:-]{8,128}")
        or account and not identifier(value["account_plan_id"], r"[A-Za-z0-9_:-]{8,128}")
        or value["action_type"] not in ACTIONS
        or value["original_state"] not in {"SENDING", "DELIVERY_UNCERTAIN"}
        or type(value["attempt_count"]) is not int or not 1 <= value["attempt_count"] < 2**31
        or any(not hash_value(value[name]) for name in fields if name.endswith("_sha256")
               and name not in {"content_sha256", "account_receipt_sha256"})
        or account and value["account_receipt_sha256"] is not None
            and not hash_value(value["account_receipt_sha256"])
        or (value["content_sha256"] is not None and not hash_value(value["content_sha256"]))
        or (value["action_type"] == "qq.private.poke" or account)
            and value["content_sha256"] is None):
        raise ValueError("DELIVERY_RESOLUTION_BINDING_INVALID")
    timestamp(value["created_at"])
    if value["lease_until"] is not None:
        timestamp(value["lease_until"])
    return dict(value)


def validate_input_closure(value):
    if (not isinstance(value, dict) or set(value) != INPUT_CLOSURE_FIELDS
        or type(value["version"]) is not int or value["version"] != 1
        or not identifier(value["event_id"], r"[A-Za-z0-9_:-]{8,128}")
        or not hash_value(value["source_sha256"]) or not hash_value(value["work_sha256"])
        or value["input_sha256"] is not None and not hash_value(value["input_sha256"])
        or type(value["complete"]) is not bool or type(value["terminal"]) is not bool
        or not isinstance(value["unsettled"], dict)
        or set(value["unsettled"]) != {"jobs", "models", "reads", "deliveries"}
        or any(type(v) is not bool for v in value["unsettled"].values())
        or not isinstance(value["deliveries"], list)
        or not 1 <= len(value["deliveries"]) <= MAX_INPUT_DELIVERIES):
        raise ValueError("DELIVERY_INPUT_CLOSURE_INVALID")
    job = value["job"]
    if job is not None and (
        not isinstance(job, dict) or set(job) != {"job_id", "kind", "state", "record_sha256"}
        or not identifier(job["job_id"], r"job_[a-f0-9]{32}")
        or job["kind"] not in {"chat", "poke"}
        or job["state"] not in JOB_TERMINAL | {"QUEUED", "RUNNING", "RETRY_WAIT"}
        or not hash_value(job["record_sha256"])
    ):
        raise ValueError("DELIVERY_INPUT_CLOSURE_INVALID")
    ids = []
    for item in value["deliveries"]:
        if (not isinstance(item, dict) or set(item) != CLOSURE_DELIVERY_FIELDS
            or not identifier(item["message_id"], r"msg_[a-f0-9]{32}")
            or item["state"] not in DELIVERY_TERMINAL | {
                "PENDING", "LEASED", "SENDING", "RETRY_WAIT", "DELIVERY_UNCERTAIN"}
            or type(item["attempt_count"]) is not int or not 0 <= item["attempt_count"] < 2**31
            or not hash_value(item["provider_receipt_sha256"])
            or any(item[k] is not None and not hash_value(item[k]) for k in (
                "content_sha256", "authority_sha256", "owner_resolution_sha256"))):
            raise ValueError("DELIVERY_INPUT_CLOSURE_INVALID")
        ids.append(item["message_id"])
    terminal = bool(value["complete"] and value["input_sha256"] and job
        and job["state"] in JOB_TERMINAL and not any(value["unsettled"].values())
        and all(item["state"] in DELIVERY_TERMINAL for item in value["deliveries"])
        and all(item["authority_sha256"] or not item["attempt_count"]
                for item in value["deliveries"]))
    if ids != sorted(set(ids)) or value["terminal"] != terminal:
        raise ValueError("DELIVERY_INPUT_CLOSURE_INVALID")
    return dict(value)


def validate_receipt(value):
    """Validate a receipt obtained directly from Core; never authorize from its hash."""
    if (not isinstance(value, dict) or set(value) != RECEIPT_FIELDS
        or value["kind"] != RECEIPT_KIND or type(value["version"]) is not int
        or value["version"] != 1 or value["state"] != "OWNER_RESOLVED"
        or value["outcome"] not in {"SENT", "CANCELLED"} or value["paused"] is not True
        or not identifier(value["journal_id"], r"budget_[a-f0-9]{32}")
        or any(not hash_value(value[name]) for name in (
            "record_sha256", "owner_operation_sha256", "source_sha256", "core_stop_sha256",
            "receipt_sha256"))
        or any(value[name] is not None and not hash_value(value[name]) for name in (
            "account_ref", "policy_sha256", "prior_liability_sha256"))
        or value["prior_quota_state"] not in {None, "RESERVED", "DELIVERY_UNCERTAIN", "UNKNOWN"}):
        raise ValueError("DELIVERY_RESOLUTION_RECEIPT_INVALID")
    owner_operation(value["owner_operation_id"], value["owner_operation_sha256"])
    proof = validate_quiescence(value["quiescence"])
    delivery = validate_delivery(value["delivery"])
    account = proof["action_type"] in ACCOUNT_ACTIONS
    closure = None if account else validate_input_closure(value["input_closure"])
    current = next((item for item in closure["deliveries"]
                    if item["message_id"] == delivery["message_id"]), None) if closure else None
    shared = ("message_id", "action_type", "account_plan_id", "authorization_sha256",
              "payload_sha256") if account else ("message_id", "event_id", "action_type")
    if (timestamp(value["at"]) < timestamp(proof["quiesced_at"])
        or value["journal_id"] != proof["budget_journal_id"]
        or value["source_sha256"] != proof["send_check_sha256"]
        or any(delivery[name] != proof[name] for name in shared)
        or (proof["content_sha256"] is not None
            and delivery["content_sha256"] != proof["content_sha256"])
        or (value["account_ref"] is None) != (value["policy_sha256"] is None)
        or (value["account_ref"] is None) != (value["prior_liability_sha256"] is None)
        or (value["account_ref"] is None) != (value["prior_quota_state"] is None)
        or account and value["input_closure"] is not None
        or not account and (
            closure["event_id"] != delivery["event_id"] or not current
            or current["state"] != value["outcome"]
            or current["owner_resolution_sha256"] is not None
            or any(current[key] != delivery[key] for key in (
                "attempt_count", "content_sha256", "authority_sha256")))
        or value["receipt_sha256"] != digest({k: v for k, v in value.items()
                                               if k != "receipt_sha256"})
        or len(canonical(value)) > MAX_RECEIPT_BYTES):
        raise ValueError("DELIVERY_RESOLUTION_RECEIPT_INVALID")
    return dict(value)
