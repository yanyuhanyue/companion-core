"""Summarize deidentified stage records; no network, QQ sender, or paid model calls."""

import argparse
import json
import math
from collections import Counter, defaultdict
from pathlib import Path

EVIDENCE = {"OFFLINE_FIXTURE", "LIVE_ONEBOT", "PAID_MODEL"}
FIELDS = {
    "schema_version",
    "evidence_type",
    "session",
    "trace_id",
    "stage",
    "monotonic_ns",
    "elapsed_ms",
    "budget_micro_usd",
    "model_usage_state",
}


def summarize(records, evidence_type, *, delivery_states=None):
    if evidence_type not in EVIDENCE:
        raise ValueError("INVALID_EVIDENCE")
    if delivery_states is not None and (
        not isinstance(delivery_states, dict)
        or set(delivery_states)
        - {
            "PENDING",
            "LEASED",
            "SENDING",
            "RETRY_WAIT",
            "SENT",
            "DELIVERY_UNCERTAIN",
            "FAILED",
            "CANCELLED",
        }
        or any(type(value) is not int or value < 0 for value in delivery_states.values())
    ):
        raise ValueError("INVALID_DURABLE_DELIVERY_COUNTS")
    stages, durations = Counter(), defaultdict(list)
    costs, usage_states = [], Counter()
    for record in records:
        if set(record) - FIELDS or record.get("schema_version") != 1:
            raise ValueError("UNRECOGNIZED_METRIC_FIELDS")
        if record.get("evidence_type") != evidence_type:
            continue
        stage = record["stage"]
        if not isinstance(stage, str) or len(stage) > 40:
            raise ValueError("INVALID_STAGE")
        stages[stage] += 1
        cost = record.get("budget_micro_usd")
        if cost is not None:
            if type(cost) is not int or cost < 0 or stage not in {"t_model_done", "t_model_failed"}:
                raise ValueError("INVALID_COST")
            costs.append(cost)
        usage_state = record.get("model_usage_state")
        if usage_state is not None:
            if usage_state not in {"SETTLED", "UNKNOWN"} or cost is None:
                raise ValueError("INVALID_USAGE_STATE")
            usage_states[usage_state] += 1
        value = record.get("elapsed_ms")
        if value is not None:
            if type(value) not in (int, float) or not math.isfinite(value) or value < 0:
                raise ValueError("INVALID_DURATION")
            durations[stage].append(value)
    latency = {}
    for stage, values in durations.items():
        values.sort()
        latency[stage] = {
            "samples": len(values),
            "p50_ms": values[math.ceil(len(values) * 0.5) - 1],
            "p95_ms": values[math.ceil(len(values) * 0.95) - 1],
            "max_ms": values[-1],
        }
    sends = stages["t_onebot_send_ack"] + stages["delivery_uncertain"]
    return {
        "schema_version": 1,
        "evidence_type": evidence_type,
        "stage_counts": dict(stages),
        "latency": latency,
        "durable_delivery_counts": delivery_states,
        "unknown_delivery_count": delivery_states.get("DELIVERY_UNCERTAIN", 0)
        if delivery_states is not None
        else None,
        "observed_uncertain_stage_count": stages["delivery_uncertain"],
        "observed_send_uncertainty_rate": stages["delivery_uncertain"] / sends if sends else None,
        "failed_job_attempts": stages["job_failed"],
        "duplicate_suppressed": stages["duplicate_suppressed"],
        "model_budget_estimate_micro_usd": sum(costs) if costs else None,
        "model_budget_estimate_complete": bool(stages["t_model_request"])
        and stages["t_model_request"] == len(costs),
        "unknown_model_usage_attempts": usage_states["UNKNOWN"],
        "model_invoice_verified": False,
        "real_qq_private_scenarios_accepted": 0,
        "latency_real_p50_ms": None,
        "latency_real_p95_ms": None,
        "note": "Stage durations are local spans; platform acceptance is not client receipt. "
        "Stage logs may be lost on crash; complete outstanding unknown counts require a "
        "matching Core delivery-state snapshot. Budget completeness covers recorded attempts "
        "only; reconcile with durable model_usage after a crash. Human QQ acceptance and "
        "provider invoices require separate evidence.",
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("inputs", nargs="+", type=Path)
    parser.add_argument("--evidence-type", choices=sorted(EVIDENCE), required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--delivery-states",
        type=Path,
        help="matching deidentified Core snapshot: state to count JSON object",
    )
    args = parser.parse_args()
    rows = []
    for path in args.inputs:
        if path.stat().st_size > 16777216:
            parser.error("metrics input exceeds 16 MiB")
        rows.extend(
            json.loads(line) for line in path.read_text("utf-8").splitlines() if line.strip()
        )
    states = json.loads(args.delivery_states.read_text("utf-8")) if args.delivery_states else None
    result = summarize(rows, args.evidence_type, delivery_states=states)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    markdown = [
        f"Evidence: {args.evidence_type}",
        "",
        result["note"],
        "",
        "| Stage | n | P50 ms | P95 ms | Max ms |",
        "|---|---:|---:|---:|---:|",
    ]
    for stage, summary in result["latency"].items():
        markdown.append(
            f"| {stage} | {summary['samples']} | {summary['p50_ms']} | "
            f"{summary['p95_ms']} | {summary['max_ms']} |"
        )
    args.output.with_suffix(".md").write_text("\n".join(markdown) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
