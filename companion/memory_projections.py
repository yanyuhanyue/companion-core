"""Rebuildable attention and episode views; native evidence remains authoritative."""

from datetime import datetime

from companion.contracts import CoreError
from companion.memory_context import MemorySources, check_version, read_version
from companion.store import after, encode

ACTIVE = {"ACCEPTED", "QUEUED", "RUNNING", "WAITING_INPUT", "WAITING_RESOURCE", "RETRY_WAIT"}


def identifiers(values):
    if (not isinstance(values, (list, tuple, set, frozenset)) or len(values) > 100
        or any(not isinstance(value, str) or not value for value in values)):
        raise CoreError("INVALID_PROJECTION_SCOPE", 400)
    return sorted(set(values))


class MemoryProjections:
    """Explicit bounded selection, never an authorization grant or recall filter.

    Callers supply IDs selected under their existing context authorization. An
    optional event allowlist must cover every source of an episode or goal.
    Temperature is independent of truth status, importance and retrieval score.
    """

    def __init__(self, memory):
        self.memory, self.store = memory, memory.store

    def snapshot(self, now, *, memory_ids=(), episode_ids=(), current_event_ids=(),
                 used_memory_ids=(), event_ids=None, actor="owner"):
        self.memory.require_owner(actor)
        mids, eids = identifiers(memory_ids), identifiers(episode_ids)
        current, used = set(identifiers(current_event_ids)), set(identifiers(used_memory_ids))
        allowed = None if event_ids is None else set(identifiers(event_ids))
        if not used <= set(mids) or allowed is not None and not current <= allowed:
            raise CoreError("INVALID_PROJECTION_SCOPE", 400)
        version = read_version(self.memory)
        sources = MemorySources(self.memory, now)
        memories, episodes = [], []
        for row in self.store.rows(
            "SELECT * FROM memories WHERE memory_id IN (SELECT value FROM json_each(?)) "
            "AND privacy_scope='private' AND confidence>0 "
            "AND status IN ('CONFIRMED','HYPOTHESIS') ORDER BY memory_id", (encode(mids),),
        ):
            evidence = sources.evidence(row["memory_id"])
            if allowed is not None:
                evidence = [event for event in evidence if event["event_id"] in allowed]
            if not evidence or not sources.current(row):
                continue
            ids = [event["event_id"] for event in evidence]
            goals, truncated = self._goals(ids, sources, allowed)
            temperature, reasons = self._temperature(
                evidence, goals, current, now, row["memory_id"] in used,
            )
            memories.append({"memory_id": row["memory_id"], "status": row["status"],
                             "temperature": temperature, "attention_reasons": reasons,
                             "source_event_ids": ids, "goals": goals,
                             "goals_truncated": truncated})
        for row in self.store.rows(
            "SELECT * FROM episodes WHERE episode_id IN (SELECT value FROM json_each(?)) "
            "AND status='ACTIVE' AND privacy_scope='private' ORDER BY episode_id", (encode(eids),),
        ):
            ids = [r["event_id"] for r in self.store.rows(
                "SELECT event_id FROM episode_events WHERE episode_id=? ORDER BY event_id LIMIT 21",
                (row["episode_id"],),
            )]
            if not 1 <= len(ids) <= 20 or allowed is not None and not set(ids) <= allowed:
                continue
            evidence = [sources.event(key, require_text=False) for key in ids]
            if any(event is None or event["conversation_id"] != row["conversation_id"]
                   for event in evidence):
                continue
            goals, truncated = self._goals(ids, sources, allowed)
            temperature, reasons = self._temperature(evidence, goals, current, now)
            deliveries = self.store.rows(
                "SELECT message_id,state,sent_at FROM outbox "
                "WHERE origin_event_id IN (SELECT value FROM json_each(?)) "
                "AND conversation_id=? AND action_type='qq.private.reply' "
                "ORDER BY created_at,message_id LIMIT 21", (encode(ids), row["conversation_id"]),
            )
            episodes.append({"episode_id": row["episode_id"], "state": "discussed",
                             "temperature": temperature, "attention_reasons": reasons,
                             "source_event_ids": ids, "goals": goals,
                             "goals_truncated": truncated,
                             "deliveries": [{**delivery, "state": delivery["state"],
                                             "told": delivery["state"] == "SENT"}
                                            for delivery in deliveries[:20]],
                             "deliveries_truncated": len(deliveries) > 20})
        check_version(self.memory, version)
        return {"projection_version": 1, "as_of": now, "privacy_epoch": version[0],
                "memories": memories, "episodes": episodes}

    @staticmethod
    def _temperature(events, goals, current, now, used=False):
        reasons = []
        if any(event["event_id"] in current for event in events):
            reasons.append("CURRENT_CONTEXT")
        if used:
            reasons.append("USED_IN_CONTEXT")
        if any(goal["native_state"] in ACTIVE or goal["state"] == "UNKNOWN"
               or goal["kind"] == "commitment" and not goal["closed"] for goal in goals):
            reasons.append("UNRESOLVED_GOAL")
        if reasons:
            return "Hot", reasons
        if any(datetime.fromisoformat(event["occurred_at"]) >=
               datetime.fromisoformat(after(now, -7 * 86400)) for event in events):
            return "Warm", ["RECENT_EVIDENCE"]
        return "Cold", ["OLDER_EVIDENCE"]

    def _goals(self, ids, sources, allowed):
        rows = self.store.rows(
            "SELECT DISTINCT g.* FROM goals g JOIN goal_evidence ge USING(goal_id) "
            "WHERE g.redacted=0 AND ge.event_id IN (SELECT value FROM json_each(?)) "
            "ORDER BY g.goal_id LIMIT 21", (encode(ids),),
        )
        result = []
        for goal in rows[:20]:
            roots = [r["event_id"] for r in self.store.rows(
                "SELECT event_id FROM goal_evidence WHERE goal_id=? ORDER BY event_id LIMIT 101",
                (goal["goal_id"],),
            )]
            if (not 1 <= len(roots) <= 100 or allowed is not None and not set(roots) <= allowed
                or any(not self._goal_source(key, sources) for key in roots)):
                continue
            step = self.store.one("SELECT * FROM goal_steps WHERE goal_id=?", (goal["goal_id"],))
            state = "discussed"
            if goal["state"] in ACTIVE:
                state = "running" if goal["state"] == "RUNNING" else "planned"
            elif goal["state"] == "FAILED":
                state = "failed"
            elif goal["state"] == "CANCELLED":
                state = "closed"
            elif goal["state"] == "DONE":
                completed = self._completed(goal, step, roots, sources, allowed)
                state = "done" if completed else "UNKNOWN"
            if step and step["state"] == "EXECUTION_UNCERTAIN":
                state = "UNKNOWN"
            executions, executions_truncated = self._executions(goal, step, sources, allowed)
            if any(item["state"] == "UNKNOWN" or item["attempt_state"] == "UNKNOWN"
                   for item in executions):
                state = "UNKNOWN"
            commitment = self.store.one(
                "SELECT c.state,c.feedback_outcome,o.state AS delivery_state "
                "FROM commitments c LEFT JOIN outbox o ON o.message_id=c.feedback_message_id "
                "AND o.action_type='qq.private.reply' WHERE c.goal_id=?", (goal["goal_id"],),
            )
            told = bool(commitment and commitment["delivery_state"] == "SENT")
            closed = state == "closed" or bool(
                told and commitment["state"] in {"FULFILLED", "EXPLAINED_FAILURE"}
                and state in {"done", "failed"}
            )
            result.append({"goal_id": goal["goal_id"], "kind": goal["kind"],
                           "native_state": goal["state"], "state": state,
                           "executions": executions, "executions_truncated": executions_truncated,
                           "told": told, "closed": closed, "source_event_ids": roots})
        return result, len(rows) > 20

    def _goal_source(self, key, sources):
        # Facts remain owner-only. Native goals may also cite actual observations.
        if sources.event(key, require_text=False) is not None:
            return True
        observation = self.store.one(
            "SELECT 1 FROM observations o JOIN events e USING(event_id) "
            "WHERE o.event_id=? AND o.redacted=0 AND e.event_type='content.observed'",
            (key,),
        )
        return bool(observation and self._live_lineage(list(sources._lineage(key)), sources))

    def _executions(self, goal, step, sources, allowed):
        if not step:
            return [], False
        rows = self.store.rows(
            "SELECT r.request_id,r.state,a.state AS attempt_state FROM exploration_requests r "
            "LEFT JOIN reading_attempts a USING(request_id) WHERE r.redacted=0 "
            "AND r.operation='read' AND json_extract(r.goal_step_json,'$.goal_id')=? "
            "AND json_extract(r.goal_step_json,'$.step_id')=? "
            "ORDER BY (r.state='UNKNOWN' OR COALESCE(a.state='UNKNOWN',0)) DESC,"
            "r.created_at DESC,r.request_id LIMIT 21", (goal["goal_id"], step["step_id"]),
        )
        executions = []
        for row in rows[:20]:
            ids = [r["event_id"] for r in self.store.rows(
                "SELECT event_id FROM exploration_evidence WHERE request_id=? "
                "ORDER BY event_id LIMIT 101", (row["request_id"],),
            )]
            if (not 1 <= len(ids) <= 100 or allowed is not None and not set(ids) <= allowed
                or any(not self._goal_source(key, sources) for key in ids)):
                continue
            executions.append({**row, "step_id": step["step_id"]})
        return executions, len(rows) > 20

    def _completed(self, goal, step, roots, sources, allowed):
        key = goal["result_event_id"]
        if not key or not step or step["state"] != "DONE" or step["outcome_event_id"] != key:
            return False
        if allowed is not None and key not in allowed:
            return False
        receipt = self.store.one(
            "SELECT result_status FROM observations WHERE event_id=? AND redacted=0", (key,),
        )
        if not receipt or receipt["result_status"] not in {"FETCHED", "WATCHED"}:
            return False
        lineage = list(sources._lineage(key))
        if not set(roots) <= {event["source_id"] for event in lineage}:
            return False
        return self._live_lineage(lineage, sources)

    @staticmethod
    def _live_lineage(lineage, sources):
        now = datetime.fromisoformat(sources.now)
        if not lineage:
            return False
        for event in lineage:
            if (event["event_id"] is None or event["redacted"] or event["erased"]
                or event["privacy_scope"] != "private" or event["scope"] != "private"
                or event["owner_actor_id"] != "owner" or event["source"] == "observation.simulation"
                or datetime.fromisoformat(event["ingested_at"]) < sources.cutoff
                or datetime.fromisoformat(event["occurred_at"]) > now
                or datetime.fromisoformat(event["ingested_at"]) > now):
                return False
        return True
