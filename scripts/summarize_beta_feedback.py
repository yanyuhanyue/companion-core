"""Summarize explicitly selected private feedback/Inspector files; no runtime access."""

import argparse
import hashlib
import json
import math
from collections import Counter
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from scripts.validate_beta_feedback import _unique, validate_record

MAX_BYTES = 16 * 1024 * 1024
KINDS = ("NOT_RUN", "local_synthetic", "authorized_runtime")
USAGE_FIELDS = (
    "prompt_tokens", "prompt_cache_hit_tokens", "completion_tokens",
    "configured_price_micro_usd", "accounted_or_reserved_micro_usd", "reservation_micro_usd",
)
OUTBOX_STATES = {
    "PENDING", "LEASED", "SENDING", "RETRY_WAIT", "SENT", "DELIVERY_UNCERTAIN",
    "FAILED", "CANCELLED",
}


def require(value, code):
    if not value:
        raise ValueError(code)


def number(value):
    return type(value) is int and 0 <= value < 2**63


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def read_json(path, limit):
    with path.open("rb") as stream:
        raw = stream.read(limit + 1)
    require(len(raw) <= limit, "FILE_TOO_LARGE")
    return json.loads(raw, object_pairs_hook=_unique), raw


def snapshot(record, report, raw):
    require(record["inspector_sha256"] == digest(raw), "INSPECTOR_HASH_MISMATCH")
    require(isinstance(report, dict) and type(report.get("schema_version")) is int
            and report["schema_version"] == 1
            and report.get("read_only") is True and report.get("diagnostic_only") is True,
            "INSPECTOR_SHAPE")
    require(report.get("trace_ref_sha256") == record["trace_ref_sha256"]
            and record["trace_ref_sha256"] is not None, "TRACE_MISMATCH")
    configured = report.get("configured_candidate_sha")
    require(configured is None or configured == record["candidate_sha"], "CANDIDATE_MISMATCH")
    approval = report.get("configured_approval_sha256")
    require(approval is None or approval == record["authorization_refs"]["R3"],
            "APPROVAL_MISMATCH")
    runtime = record["evidence_type"] == "authorized_runtime"
    if runtime:
        require(approval is not None and configured is not None, "RUNTIME_PINS_REQUIRED")
    require(report.get("blockers") == [], "INSPECTOR_BLOCKED")
    trace = report.get("trace", {})
    require(trace.get("state") in {"RECORDED", "REDACTED"}, "TRACE_UNAVAILABLE")
    if runtime:
        binding = trace.get("recorded_window", {})
        require(binding.get("state") == "RECORDED"
                and binding.get("execution") == "approved_live"
                and binding.get("approval_sha256") == record["authorization_refs"]["R3"]
                and binding.get("candidate_sha") == record["candidate_sha"],
                "EVENT_APPROVAL_MISMATCH")
    require(isinstance(trace.get("jobs"), list) and len(trace["jobs"]) <= 20, "JOB_LIMIT")
    calls = []
    for job in trace["jobs"]:
        items = job["model_calls"]
        require(isinstance(items, list) and len(items) <= 100
                and number(job["model_attempt_count"])
                and job["model_attempt_count"] == len(items), "USAGE_SHAPE")
        for call in items:
            require(call.get("state") in {"RESERVED", "SETTLED", "UNKNOWN"}
                    and type(call.get("paid_dispatch_recorded")) is bool, "USAGE_STATE")
            require(all(call.get(key) is None or number(call[key]) for key in USAGE_FIELDS),
                    "USAGE_NUMBER")
            if call["state"] != "SETTLED" or not call["paid_dispatch_recorded"]:
                require(all(call.get(key) is None for key in USAGE_FIELDS[:4]),
                        "UNKNOWN_IS_NOT_OBSERVED_USAGE")
            calls.append(call)
        if runtime and (job.get("context", {}).get("memory")
                        or any(call.get("scenario") == "memory_review" for call in items)):
            require(record["authorization_refs"]["R5"] is not None, "MEMORY_APPROVAL_REQUIRED")
        if runtime and (job.get("context", {}).get("images")
                        or any(call.get("scenario") == "content_vision" for call in items)):
            require(record["authorization_refs"]["media"] is not None, "MEDIA_APPROVAL_REQUIRED")
    if runtime and any(call["paid_dispatch_recorded"] for call in calls):
        require(record["authorization_refs"]["R4"] is not None, "PAID_APPROVAL_REQUIRED")
    require(isinstance(trace.get("outbox"), dict)
            and set(trace["outbox"]) <= OUTBOX_STATES
            and all(number(n) for n in trace["outbox"].values()), "OUTBOX_SHAPE")
    return {"trace": trace, "calls": calls}


def load_samples(manifest_path):
    manifest, raw = read_json(manifest_path, 131072)
    require(isinstance(manifest, dict)
            and set(manifest) == {"schema_version", "timezone", "samples"}
            and type(manifest["schema_version"]) is int
            and manifest["schema_version"] == 1, "MANIFEST_SHAPE")
    zone = ZoneInfo(manifest["timezone"])
    require(isinstance(manifest["samples"], list) and len(manifest["samples"]) <= 512,
            "SAMPLE_LIMIT")
    total, samples = len(raw), []
    base = manifest_path.resolve().parent

    def local_file(name, limit):
        nonlocal total
        require(isinstance(name, str) and name and not Path(name).is_absolute(), "LOCAL_PATH")
        path = (base / name).resolve()
        require(path.is_relative_to(base) and path.is_file(), "LOCAL_PATH")
        value, data = read_json(path, limit)
        total += len(data)
        require(total <= MAX_BYTES, "TOTAL_BYTE_LIMIT")
        return value, data

    for item in manifest["samples"]:
        require(isinstance(item, dict) and set(item) == {"feedback", "inspector"}, "SAMPLE_SHAPE")
        record, feedback_raw = local_file(item["feedback"], 65536)
        validation = validate_record(record)
        require(validation["schema_valid"], "FEEDBACK_INVALID")
        inspection = None
        if item["inspector"] is not None:
            report, report_raw = local_file(item["inspector"], 262144)
            inspection = snapshot(record, report, report_raw)
        else:
            require(record["inspector_sha256"] is None, "INSPECTOR_FILE_REQUIRED")
        samples.append((record, validation, inspection, digest(feedback_raw)))
    return zone, samples


def observed(values):
    known = [value for value in values if value is not None]
    return {"observed_n": len(known), "missing_n": len(values) - len(known),
            "sum": sum(known) if known else None}


def percentile(values, quantile):
    # Nearest rank; one observation does not establish a latency distribution.
    return sorted(values)[math.ceil(len(values) * quantile) - 1] if len(values) >= 2 else None


def summarize(manifest_path):
    zone, samples = load_samples(Path(manifest_path))
    groups = {kind: [] for kind in KINDS}
    seen, duplicates = {}, 0
    for sample in samples:
        record, _, inspection, file_hash = sample
        aliases = [("trace", record["trace_ref_sha256"])] if record["trace_ref_sha256"] else []
        if inspection and inspection["trace"].get("event_ref_sha256"):
            aliases.append(("event", inspection["trace"]["event_ref_sha256"]))
        aliases = aliases or [("file", file_hash)]
        matches = [seen[key] for key in aliases if key in seen]
        require(all(previous == file_hash for previous in matches), "CONFLICTING_TRACE_FEEDBACK")
        for key in aliases:
            seen[key] = file_hash
        if matches:
            duplicates += 1
            continue
        groups[record["evidence_type"]].append(sample)
    result = {"schema_version": 1, "state": "PRIVATE_REPORTED_OBSERVATIONS",
              "evidence_authenticated": False, "supplier_invoice_verified": False,
              "runtime_authorized": False, "source_validity": "NOT_REVALIDATED",
              "timezone": str(zone), "duplicate_records_ignored": duplicates, "groups": {}}
    for kind, entries in groups.items():
        records = [entry[0] for entry in entries]
        inspections = [entry[2] for entry in entries if entry[2] is not None]
        calls = [call for view in inspections for call in view["calls"]]
        pairs = [entry[1]["reported_phone_pair_ms"] for entry in entries
                 if entry[1]["reported_phone_pair_ms"] is not None]
        dates = set()
        for view in inspections:
            when = view["trace"].get("event_occurred_at_utc")
            if view["trace"]["state"] == "RECORDED" and when:
                stamp = datetime.fromisoformat(when)
                require(stamp.tzinfo is not None, "EVENT_TIMEZONE_REQUIRED")
                dates.add(stamp.astimezone(zone).date().isoformat())
        known_incidents = [r["incidents"] for r in records if r["incidents"] is not None]
        known_stops = [r["stop_receipts"] for r in records
                       if any(state != "NOT_RUN" for state in r["stop_receipts"].values())]
        outbox = Counter()
        for view in inspections:
            outbox.update(view["trace"]["outbox"])
        logical = [call.get("logical_request_ref_sha256") for call in calls]
        requests = [call.get("request_ref_sha256") for call in calls
                    if call.get("request_ref_sha256")]
        require(len(requests) == len(set(requests)), "DUPLICATE_MODEL_REQUEST")
        invoices = {}
        for record in records:
            invoice = record["provider_invoice"]
            if invoice["micro_usd"] is not None:
                evidence = invoice["evidence_sha256"]
                require(evidence not in invoices or invoices[evidence] == invoice["micro_usd"],
                        "CONFLICTING_INVOICE_AMOUNT")
                invoices[evidence] = invoice["micro_usd"]
        summary = {
            "feedback_records": len(entries), "inspector_records": len(inspections),
            "reported_event_dates": sorted(dates),
            "reported_natural_day_count": len(dates) if dates else None,
            "continuous_healthy_seconds": None, "authenticated_inputs": None,
            "send_attempts": None, "outbox_states": dict(outbox) if inspections else None,
            "phone": {
                "received": sum(r["phone"]["received"] is True for r in records)
                if any(r["phone"]["received"] is not None for r in records) else None,
                "missing_confirmations": sum(r["phone"]["received"] is None for r in records),
                "visible_replies": observed([r["phone"].get("visible_reply_count")
                                             for r in records]),
                "paired_e2e_n": len(pairs), "p50_ms": percentile(pairs, .50),
                "p95_ms": percentile(pairs, .95),
            },
            "model": {
                "attempt_records": len(calls) if inspections else None,
                "dispatch_fences_recorded": sum(c["paid_dispatch_recorded"] for c in calls)
                if inspections else None,
                "logical_requests": len(set(logical)) if logical and all(logical) else None,
                "model_request_identity_complete": len(requests) == len(calls) if calls else None,
                "states": dict(Counter(c["state"] for c in calls)) if inspections else None,
                **{key: observed([call.get(key) for call in calls]) for key in USAGE_FIELDS},
                "supplier_invoice_micro_usd": {
                    "distinct_evidence_n": len(invoices),
                    "reported_sum": sum(invoices.values()) if invoices else None,
                    "allocation_verified": False,
                },
                "billing_basis": "reported_configured_rates_not_reconciled_invoice",
            },
            "ratings": {key: {
                "n": len(values := [r["ratings"][key] for r in records
                                    if r["ratings"].get(key) is not None]),
                "mean": sum(values) / len(values) if values else None,
            } for key in ("naturalness", "relevance", "source_accuracy", "memory_correctness",
                          "initiative_comfort", "privacy_comfort", "duplication_disruption",
                          "diary_authenticity", "interest_spontaneity")},
            "coverage_observations": dict(Counter(
                f"{name}:{coverage}" for r in records
                for name, coverage in r.get("coverage_observations", {}).items()
                if coverage is not None)) or None,
            "experience_observations": dict(Counter(
                f"{name}:{outcome}" for r in records
                for name, outcome in r.get("experience_observations", {}).items()
                if outcome is not None)) or None,
            "incident_samples_checked": len(known_incidents),
            "reported_incidents": dict(Counter(code for codes in known_incidents for code in codes))
            if known_incidents else None,
            "stop_observations": {
                "checked_samples": len(known_stops),
                "both_confirmed_samples": sum(
                    all(state == "CONFIRMED" for state in row.values()) for row in known_stops
                ) if known_stops else None,
                "partial_samples": sum("NOT_RUN" in row.values() for row in known_stops)
                if known_stops else None,
                "not_run_samples": len(records) - len(known_stops),
                "component_states": {component: dict(Counter(
                    row["stop_receipts"][component] for row in records
                )) for component in ("core", "bridge")} if records else None,
                "unconfirmed_samples": sum("STOP_UNCONFIRMED" in row.values()
                                           for row in known_stops) if known_stops else None,
            },
        }
        # Dates in synthetic records remain test fixture dates, never real observation days.
        summary["cross_day_runtime_quality_proven"] = False
        result["groups"][kind] = summary
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", type=Path)
    args = parser.parse_args()
    try:
        print(json.dumps(summarize(args.manifest), ensure_ascii=False))
    except (ValueError, OSError, KeyError, TypeError, AttributeError, RecursionError):
        # Filenames, schema messages, bodies and provider errors may contain private data.
        print('{"state":"INVALID_PRIVATE_OBSERVATION_SET","evidence_authenticated":false}')
        raise SystemExit(2) from None


if __name__ == "__main__":
    main()
