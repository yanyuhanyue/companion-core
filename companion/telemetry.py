"""Opt-in, content-free local stage timings; never a delivery or billing receipt."""

import json
import os
import re
import time
import uuid

SESSION = uuid.uuid4().hex
STAGES = {
    "t_job_started",
    "t_context_ready",
    "t_model_request",
    "t_model_done",
    "t_model_failed",
    "t_job_done",
    "job_failed",
}


def emit(
    stage, trace_id, *, elapsed_ms=None, budget_micro_usd=None, model_usage_state=None, at_ns=None
):
    if os.environ.get("COMPANION_TELEMETRY") != "1":
        return
    evidence = os.environ.get("COMPANION_TELEMETRY_EVIDENCE", "OFFLINE_FIXTURE")
    if (
        stage not in STAGES
        or evidence not in {"OFFLINE_FIXTURE", "LIVE_ONEBOT", "PAID_MODEL"}
        or not re.fullmatch(r"trace_[a-f0-9]{32,64}", trace_id)
    ):
        return
    record = {
        "schema_version": 1,
        "evidence_type": evidence,
        "session": SESSION,
        "trace_id": trace_id,
        "stage": stage,
        "monotonic_ns": time.perf_counter_ns() if at_ns is None else at_ns,
    }
    if elapsed_ms is not None:
        record["elapsed_ms"] = round(max(0, elapsed_ms), 4)
    if budget_micro_usd is not None:
        record["budget_micro_usd"] = budget_micro_usd
    if model_usage_state in {"SETTLED", "UNKNOWN"}:
        record["model_usage_state"] = model_usage_state
    try:
        print(json.dumps(record), flush=True)
    except OSError:
        pass
