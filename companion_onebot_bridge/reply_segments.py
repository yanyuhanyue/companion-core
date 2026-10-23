"""Bounded HTTP reply-fragment contract; independent of the Core package."""

import hashlib
import re

from .spool import encode

FIELDS = {"version", "group_id", "index", "count", "group_sha256", "total_chars",
          "interval_ms", "part_sha256s"}


def wire_text(item):
    """Omit outer blank lines in a bubble; retain the lossless source for its receipt."""
    text = item["content"]["text"]
    if envelope(item) is None:
        return text
    lines = text.splitlines(keepends=True)
    start, end = 0, len(lines)
    while start < end and not lines[start].strip():
        start += 1
    while end > start and not lines[end - 1].strip():
        end -= 1
    return "".join(lines[start:end]).rstrip("\r\n")


def digest(value):
    return hashlib.sha256(encode(value).encode()).hexdigest()


def scope(plan):
    return plan.value.get("live_text", {}).get("segmented_reply")


def validate_scope(value, outer):
    policy = value.get("segmented_reply")
    if (not isinstance(policy, dict)
        or set(policy) not in ({"max_segments", "min_chars", "interval_ms"},
                               {"max_segments", "min_chars", "interval_ms", "mode"})
        or policy.get("mode", "sentence") not in {"sentence", "semantic"}
        or (policy.get("max_segments") != "unlimited" and (
            type(policy.get("max_segments")) is not int
            or not 2 <= policy["max_segments"] <= 2**31 - 1))
        or any(type(policy[name]) is not int or not low <= policy[name] <= high
               for name, low, high in (("min_chars", 4, 100),
                                       ("interval_ms", 0, 3000)))
        or type(value.get("max_send_attempts")) is not int
        or not 1 <= value["max_send_attempts"] <= 2**31 - 1
        or value["max_send_attempts"] != min(outer["max_attempts"], outer["max_visible_replies"])):
        raise ValueError("INVALID_SEGMENTED_REPLY_SCOPE")


def event_id(item):
    if "window_origin_event_id" in item:
        value = item["window_origin_event_id"]
        if not isinstance(value, str) or not re.fullmatch(
            r"[A-Za-z0-9_:-]{8,128}", value,
        ):
            raise ValueError("INVALID_WINDOW_ORIGIN")
        return value
    key = item.get("delivery_key", "")
    match = (re.fullmatch(r"reply:(evt_[a-f0-9]{64}):v(?:1|2:(?:0|[1-9][0-9]{0,9}))", key)
             if isinstance(key, str) else None)
    if not match:
        raise ValueError("WINDOW_REPLY_KEY_REQUIRED")
    return match[1]


def envelope(item):
    """Validate a v2 manifest and its current text; v1 remains byte-compatible."""
    key = item.get("delivery_key", "")
    present = "reply_segment" in item
    v2 = isinstance(key, str) and ":v2" in key
    if not present and not v2:
        return None
    value = item.get("reply_segment")
    match = (re.fullmatch(r"reply:(evt_[a-f0-9]{64}):v2:(0|[1-9][0-9]{0,9})", key)
             if isinstance(key, str) else None)
    if (not match or not isinstance(value, dict) or set(value) != FIELDS
        or type(value["version"]) is not int or value["version"] != 1
        or any(type(value[name]) is not int for name in
               ("index", "count", "total_chars", "interval_ms"))
        or not 1 <= value["count"] <= 2**31 - 1 or not 0 <= value["index"] < value["count"]
        or value["index"] != int(match[2]) or not 0 <= value["interval_ms"] <= 3000
        or not isinstance(value["group_id"], str)
        or not re.fullmatch(r"job_[a-f0-9]{32}", value["group_id"])
        or not isinstance(value["part_sha256s"], list)
        or len(value["part_sha256s"]) != value["count"]
        or any(not isinstance(part, str) or not re.fullmatch(r"[a-f0-9]{64}", part)
               for part in value["part_sha256s"])):
        raise ValueError("INVALID_REPLY_SEGMENT")
    text = item["content"]["text"]
    if (not len(text) <= value["total_chars"] <= 20000
        or digest(text) != value["part_sha256s"][value["index"]]
        or value["group_sha256"] != digest({
            "job_id": value["group_id"], "parts_sha256": value["part_sha256s"],
            "interval_ms": value["interval_ms"], "total_chars": value["total_chars"],
        })):
        raise ValueError("INVALID_REPLY_SEGMENT")
    return value


def journal_value(value, text):
    return {key: value[key] for key in FIELDS - {"group_id"}} | {
        "group_ref_sha256": digest(value["group_id"]), "chars": len(text),
    }
