"""Bounded metadata over native decisions and receipts; never replay cognition."""

import json

from companion.contracts import CoreError
from companion.memory_context import MemorySources, check_version, read_version
from companion.memory_projections import MemoryProjections

KINDS = {
    "wake": ("wake_decisions", "decision_id"),
    "life": ("life_activity_results", "job_id"),
    "goal": ("goals", "goal_id"),
    "diary": ("diary_runs", "run_id"),
    "reflection": ("reflection_runs", "run_id"),
    "nightly": ("consolidation_runs", "run_id"),
    "model": ("model_usage", "request_id"),
}
STATES = frozenset("PENDING SHADOW SKIPPED DEFERRED INVALIDATED CANDIDATE ACCEPTED QUEUED "
                   "RUNNING DONE WAITING WAITING_INPUT WAITING_RESOURCE RETRY_WAIT FAILED "
                   "CANCELLED EXECUTION_UNCERTAIN COMMITTED SCHEDULED LEASED PARTIAL COMPLETE "
                   "RESERVED SUCCEEDED UNKNOWN SETTLED".split())
REASONS = frozenset("NO_CANDIDATE PAUSED QUIET_HOURS OWNER_REQUESTED_PAUSE RESOURCE_UNAVAILABLE "
                    "DAILY_LIMIT COOLDOWN GOAL_DUE DISCOVERY_DUE PRIVACY_CHANGED "
                    "OWNER_AUTHORITY_DISABLED OWNER_AUTHORITY_EXPIRED".split())


def symbol(value, allowed):
    return value if value in allowed else "UNKNOWN" if value else "NOT_RECORDED"


def object_value(value):
    try:
        result = json.loads(value or "{}")
    except (TypeError, ValueError):
        return {}
    return result if isinstance(result, dict) else {}


def measurement(value):
    receipt = object_value(value)
    if not receipt:
        return {"state": "NOT_RECORDED"}
    result = {"state": "RECORDED", "count_kind": symbol(receipt.get("count_kind"), {
        "exact_local_render", "provider_count", "estimate", "unsupported"})}
    for key in ("input_tokens", "reservation_margin", "output_allowance"):
        number = receipt.get(key)
        result[key] = number if type(number) is int and number >= 0 else None
    return result


class CognitionRead:
    def __init__(self, core):
        self.core, self.store = core, core.store
        self.projections = MemoryProjections(core.memory)

    def page(self, kind, *, limit=30, before=None):
        if kind not in KINDS or type(limit) is not int or not 1 <= limit <= 50:
            raise CoreError("INVALID_COGNITION_SELECTION", 400)
        # HTTP owner authentication is mandatory at registration; runtime feature
        # switches do not revoke the owner's ability to inspect stopped work.
        version = read_version(self.core.memory)
        now = self.core.clock()
        sources = MemorySources(self.core.memory, now)
        table, key = KINDS[kind]
        cursor = None
        if before is not None:
            cursor = self.store.one(f"SELECT rowid AS position FROM {table} WHERE {key}=?",
                                    (before,))
            if cursor is None:
                raise CoreError("COGNITION_CURSOR_UNAVAILABLE", 409)
        rows = self.store.rows(
            f"SELECT rowid AS position,* FROM {table} WHERE (? IS NULL OR rowid<?) "
            "ORDER BY rowid DESC LIMIT ?",
            (cursor and cursor["position"], cursor and cursor["position"], limit + 1),
        )
        items = []
        for row in rows[:limit]:
            item = self._record(kind, row, sources, version[0])
            if item is not None:
                items.append(item)
        check_version(self.core.memory, version)
        return {"kind": kind, "items": items, "as_of": now, "privacy_epoch": version[0],
                "next_cursor": rows[limit - 1][key] if len(rows) > limit else None,
                "truncated": len(rows) > limit, "reasoning": "NOT_EXPOSED",
                "candidate_set": "NOT_RECORDED"}

    def _roots(self, kind, row):
        if kind == "wake":
            return [row["trigger_event_id"]] if row["trigger_event_id"] else []
        if kind == "life":
            query, identifier = "life_topic_sources WHERE topic_id=?", row["topic_id"]
        elif kind == "goal":
            query, identifier = "goal_evidence WHERE goal_id=?", row["goal_id"]
        elif kind == "diary":
            query = ("diary_evidence JOIN diary_entries USING(entry_id) WHERE run_id=? "
                     "AND status='COMMITTED'")
            identifier = row["run_id"]
        elif kind in {"reflection", "nightly"}:
            query = ("reflection_evidence" if kind == "reflection" else
                     "consolidation_evidence") + " WHERE run_id=?"
            identifier = row["run_id"]
        else:
            query, identifier = "job_sources WHERE job_id=?", row["job_id"]
        return [r["event_id"] for r in self.store.rows(
            "SELECT DISTINCT event_id FROM " + query + " ORDER BY event_id LIMIT 101",
            (identifier,),
        )]

    def _record(self, kind, row, sources, current_epoch):
        roots = self._roots(kind, row)
        if (row.get("redacted") or len(roots) > 100 or any(
            not self.projections._goal_source(key, sources) for key in roots
        )):
            return None
        captured = row.get("captured_privacy_epoch")
        if captured is not None and captured != current_epoch:
            return None
        if kind in {"goal", "life"} and not roots:
            return None
        key = KINDS[kind][1]
        item = {"id": row[key], "state": symbol(row.get("state"), STATES),
                "source_event_ids": roots,
                "source_evidence": "RECORDED" if roots else "NOT_RECORDED",
                "reason_code": symbol(row.get("reason_code") or row.get("last_error_code"),
                                      REASONS)}
        if kind == "wake":
            item["choice"] = symbol(row["decision"], {"SKIP", "EXPLORE", "OBSERVE"})
            item["state"] = "RECORDED"
            item["goal_id"] = row["goal_id"]
        elif kind == "life":
            item.update(topic_id=row["topic_id"], choice=symbol(row["action"], {
                "SKIP", "CONTINUE", "EXPLORE", "FOLLOW_UP", "SHARE", "DEFER"}),
                reason_code="NOT_EXPOSED")
        elif kind == "goal":
            goals, _ = self.projections._goals(roots, sources, None)
            match = next((g for g in goals if g["goal_id"] == row["goal_id"]), None)
            if match is None:
                return None
            item["execution"] = match
            step = self.store.one("SELECT step_id,state,attempts,max_attempts FROM goal_steps "
                                  "WHERE goal_id=?", (row["goal_id"],))
            item["step"] = step
        elif kind == "nightly":
            steps = object_value(row["steps_json"])
            item["steps"] = {name: symbol(steps.get(name), STATES)
                             for name in ("memory", "diary", "reflection", "goals")}
        elif kind == "model":
            item["usage"] = {key: row[key] for key in (
                "input_tokens", "cached_input_tokens", "output_tokens", "billed_micro_usd",
                "is_estimate")}
            admission = self.store.one(
                "SELECT measurement_json FROM model_admissions WHERE request_id=?",
                (row["request_id"],),
            )
            item["measurement"] = measurement(admission and admission["measurement_json"])
            item["job_id"] = row["job_id"]
        if kind in {"diary", "reflection"}:
            item["model_request_id"] = row["model_request_id"]
        return item
