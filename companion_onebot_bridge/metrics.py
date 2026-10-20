import json
import re
import time
import uuid

STAGES = {
    "t_onebot_received",
    "t_spool_commit",
    "t_core_ingest_ack",
    "t_outbox_lease",
    "t_begin_send",
    "t_onebot_send_ack",
    "delivery_uncertain",
    "duplicate_suppressed",
    "event_rejected",
    "ws_connected",
    "ws_disconnected",
    "core_unavailable",
    "send_unavailable",
    "t_bridge_send_ack",
}


class Metrics:
    def __init__(self, evidence_type="LIVE_ONEBOT", sink=None):
        if evidence_type not in {"OFFLINE_FIXTURE", "LIVE_ONEBOT"}:
            raise ValueError("INVALID_EVIDENCE_TYPE")
        self.evidence_type = evidence_type
        self.sink = sink or (lambda record: print(json.dumps(record), flush=True))
        self.session = uuid.uuid4().hex
        self.received = {}

    def emit(self, stage, trace=None, *, elapsed_ms=None, at_ns=None):
        if stage not in STAGES:
            raise ValueError("INVALID_METRIC_STAGE")
        record = {
            "schema_version": 1,
            "evidence_type": self.evidence_type,
            "session": self.session,
            "stage": stage,
            "monotonic_ns": time.perf_counter_ns() if at_ns is None else at_ns,
        }
        if trace and re.fullmatch(r"trace_[a-f0-9]{32,64}", trace):
            record["trace_id"] = trace
        if elapsed_ms is not None:
            record["elapsed_ms"] = round(max(0, elapsed_ms), 4)
        try:
            self.sink(record)
        except OSError:
            pass  # Telemetry cannot change delivery state or request a retry.

    def start(self, payload, received_ns):
        key = payload["event_id"]
        if key not in self.received:
            if len(self.received) >= 1000:
                self.received.pop(next(iter(self.received)))
            self.received[key] = (received_ns, payload["trace_id"])
        self.emit("t_onebot_received", payload["trace_id"], at_ns=received_ns)

    def accepted(self, payload):
        previous = self.received.get(payload["event_id"])
        duration = (time.perf_counter_ns() - previous[0]) / 1e6 if previous else None
        self.emit("t_core_ingest_ack", payload["trace_id"], elapsed_ms=duration)

    def delivered(self, delivery_key, provider_id, ack_ns):
        key = delivery_key.split(":")[1] if delivery_key.startswith("reply:") else ""
        previous = self.received.pop(key, None)
        if previous and provider_id:
            self.emit(
                "t_bridge_send_ack",
                previous[1],
                elapsed_ms=(ack_ns - previous[0]) / 1e6,
                at_ns=ack_ns,
            )
