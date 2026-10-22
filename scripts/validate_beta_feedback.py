"""Validate one private feedback record without printing its private fields."""

import argparse
import hashlib
import json
from datetime import datetime
from pathlib import Path

from jsonschema import Draft202012Validator, FormatChecker

ROOT = Path(__file__).resolve().parents[1]


def validate_record(record):
    schema = json.loads((ROOT / "contracts/p11-beta-feedback.schema.json").read_text("utf-8"))
    errors = sorted(
        Draft202012Validator(schema, format_checker=FormatChecker()).iter_errors(record),
        key=lambda error: str(list(error.absolute_path)),
    )
    if errors:
        # Only locations are printed; validator messages can contain private values.
        return {"schema_valid": False, "invalid_fields": [
            ".".join(map(str, error.absolute_path)) or "record" for error in errors
        ]}
    if record["evidence_type"] == "authorized_runtime":
        missing = [key for key in ("trace_ref_sha256", "inspector_sha256", "candidate_sha")
                   if record[key] is None]
        refs = record["authorization_refs"]
        required = {"R3"}
        coverage = record.get("coverage_observations", {})
        experience = record.get("experience_observations", {})
        if any(coverage.get(key) in {"BODY_FETCHED", "CONTENT_FETCHED", "FETCHED", "ANALYZED"}
               for key in ("reply", "forward", "card", "image")):
            required.add("media")
        if experience.get("diary") is not None or record["ratings"].get("diary_authenticity"):
            required.add("diary")
        if experience.get("sharing") in {"RECEIVED_WELCOME", "RECEIVED_UNWELCOME"}:
            required.add("autonomous_send")
        missing += ["authorization_refs." + key for key in sorted(required) if refs[key] is None]
        if missing:
            return {"schema_valid": False, "invalid_fields": missing}
    phone = record["phone"]
    expected = phone.get("expected_reply_count", 1)
    if expected > 1:
        missing = [key for key in ("inspector_sha256", "trace_ref_sha256") if record[key] is None]
        if missing:
            return {"schema_valid": False, "invalid_fields": missing}
    if any(value is not None and kind not in record["qq_types"]
           for kind, value in record.get("coverage_observations", {}).items()):
        return {"schema_valid": False, "invalid_fields": ["coverage_observations.qq_type"]}
    visible = phone.get("visible_reply_count")
    if visible is not None and (
        phone["received"] is None or (visible > 0) != phone["received"]
        or (visible > expected and "duplicate_visible_reply" not in (record["incidents"] or []))
    ):
        return {"schema_valid": False, "invalid_fields": ["phone.visible_reply_count"]}
    elapsed = None
    if phone["sent_at_utc"] is not None:
        elapsed = (datetime.fromisoformat(phone["received_at_utc"])
                   - datetime.fromisoformat(phone["sent_at_utc"])).total_seconds() * 1000
        if not 0 <= elapsed <= 86400000:
            return {"schema_valid": False, "invalid_fields": ["phone.time_order_or_span"]}
    return {"schema_valid": True, "evidence_authenticated": False,
            "reported_evidence_type": record["evidence_type"],
            "reported_phone_pair_ms": elapsed,
            "supplier_invoice_verified": False}


def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate key")
        result[key] = value
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("record")
    args = parser.parse_args()
    try:
        with Path(args.record).open("rb") as stream:
            raw = stream.read(65537)
        if len(raw) > 65536:
            raise ValueError("too large")
        result = validate_record(json.loads(raw, object_pairs_hook=_unique))
        result["record_sha256"] = hashlib.sha256(raw).hexdigest()
    except (ValueError, OSError, RecursionError):
        result = {"schema_valid": False, "invalid_fields": ["unreadable_or_invalid_record"]}
    print(json.dumps(result))
    raise SystemExit(0 if result["schema_valid"] else 2)


if __name__ == "__main__":
    main()
