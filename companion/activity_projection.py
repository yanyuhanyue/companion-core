"""On-demand owner inspection of native work; never a presence or execution authority."""

from types import SimpleNamespace
from typing import Annotated

from pydantic import Field, model_validator

from companion import capabilities, private_life
from companion.autonomous_selector import foreground_clear
from companion.contracts import CoreError, StrictModel
from companion.memory_context import MemorySources, check_version, read_version
from companion.store import after

Identifier = Annotated[str, Field(strict=True, min_length=1, max_length=160)]


class ActivitySelection(StrictModel):
    conversation_id: Identifier
    limit: int = Field(default=20, strict=True, ge=1, le=20)
    memory_ids: list[Identifier] = Field(default_factory=list, max_length=20)
    episode_ids: list[Identifier] = Field(default_factory=list, max_length=20)

    @model_validator(mode="after")
    def bounded_selection(self):
        if len(self.memory_ids) + len(self.episode_ids) > 20:
            raise ValueError("Select at most 20 memories and episodes together")
        return self


class ConversationSources(MemorySources):
    """Narrow the original provenance reader to one existing private conversation."""

    def __init__(self, memory, now, conversation_id):
        super().__init__(memory, now)
        self.conversation_id = conversation_id
        self.valid_until = None

    def _lineage(self, event_id):
        rows = list(super()._lineage(event_id))
        # Native observations live in Core's internal observation conversation.
        # Their owner roots, rather than that storage container, bind this view.
        if (not any(row["conversation_id"] == self.conversation_id
                    and row["actor_id"] == "owner" for row in rows)
            or any(row["conversation_id"] != self.conversation_id
                   and row["event_type"] != "content.observed"
                   and not (row["source"] == "owner.decision"
                            and row["event_type"] == "memory.corrected") for row in rows)):
            return []
        deadlines = [after(row["ingested_at"], self.settings.raw_event_retention_days * 86400)
                     for row in rows if row["ingested_at"]]
        deadlines = [deadline for deadline in deadlines if deadline > self.now]
        if deadlines:
            self.valid_until = min([*deadlines, *([self.valid_until] if self.valid_until else [])])
        return rows


def _roots(store, table, key, identifier):
    # All identifiers here are module constants, never caller-supplied SQL.
    return [row["event_id"] for row in store.rows(
        f"SELECT event_id FROM {table} WHERE {key}=? ORDER BY event_id LIMIT 101",
        (identifier,),
    )]


def _available(reader, sources, roots):
    return (1 <= len(roots) <= 100
            and all(reader.projections._goal_source(key, sources) for key in roots))


def _result(reader, sources, key):
    if not key or not reader.projections._goal_source(key, sources):
        return None
    row = reader.store.one(
        "SELECT o.event_id,o.result_status,o.source_sha256,e.occurred_at "
        "FROM observations o JOIN events e USING(event_id) "
        "WHERE o.event_id=? AND o.redacted=0 AND e.redacted=0", (key,),
    )
    if row:
        row["meaning"] = ("MEDIA_ANALYSIS_COVERAGE" if row["result_status"] == "WATCHED"
                          else "OBSERVATION_RECEIPT")
    return row


def _candidates(store, conversation_id, limit):
    # Ordering nominates records only. Original readers still validate every source
    # and completed step. One combined cap covers jobs, goals/commitments and reads.
    return store.rows(
        "SELECT * FROM ("
        "SELECT 'job' kind,j.job_id id,j.created_at recorded_at,"
        "CASE WHEN j.state IN ('UNKNOWN','EXECUTION_UNCERTAIN') THEN 0 ELSE 2 END priority "
        "FROM jobs j JOIN events e USING(event_id) WHERE e.conversation_id=? AND e.redacted=0 "
        "UNION ALL SELECT 'goal',g.goal_id,g.created_at,CASE WHEN EXISTS "
        "(SELECT 1 FROM goal_steps s WHERE s.goal_id=g.goal_id "
        "AND s.state='EXECUTION_UNCERTAIN') THEN 0 WHEN g.kind='commitment' AND "
        "EXISTS (SELECT 1 FROM commitments c LEFT JOIN outbox o "
        "ON o.message_id=c.feedback_message_id WHERE c.goal_id=g.goal_id "
        "AND c.state NOT IN ('ERASED','FULFILLED','EXPLAINED_FAILURE') "
        "AND COALESCE(o.state,'')<>'SENT') THEN 1 ELSE 2 END "
        "FROM goals g WHERE g.redacted=0 AND EXISTS (SELECT 1 FROM goal_evidence ge "
        "JOIN events e USING(event_id) WHERE ge.goal_id=g.goal_id AND e.conversation_id=?) "
        "UNION ALL SELECT 'read',r.request_id,r.created_at,"
        "CASE WHEN r.state='UNKNOWN' OR a.state='UNKNOWN' THEN 0 ELSE 2 END "
        "FROM exploration_requests r LEFT JOIN reading_attempts a USING(request_id) "
        "WHERE r.redacted=0 AND r.operation='read' AND EXISTS "
        "(SELECT 1 FROM exploration_evidence re JOIN events e USING(event_id) "
        "WHERE re.request_id=r.request_id AND e.conversation_id=?)) "
        "ORDER BY priority,recorded_at DESC,id LIMIT ?",
        (conversation_id, conversation_id, conversation_id, limit + 1),
    )


def _lease(row, now):
    deadline = row.get("lease_until")
    return {"until": deadline, "valid_at_as_of": bool(
        row.get("state") == "RUNNING" and row.get("lease_token") and deadline and now < deadline),
        "meaning": "RECORDED_LEASE_ONLY", "worker_presence": "UNKNOWN"}


def _bounded_steps(store, key):
    steps = store.rows("SELECT * FROM goal_steps WHERE goal_id=? LIMIT 4", (key,))
    return (steps, len(steps) <= 3 and all(
        len(_roots(store, "goal_step_evidence", "step_id", step["step_id"])) <= 100
        for step in steps))


def _work(reader, sources, candidate):
    from companion.cognition_read import STATES, symbol

    store, kind, key = reader.store, candidate["kind"], candidate["id"]
    if kind == "goal":
        row = store.one("SELECT * FROM goals WHERE goal_id=?", (key,))
        # The original cognition reader allows 256 step roots; this view's approved
        # selection is narrower. Never shorten a required source set to fit it.
        raw_steps, bounded = _bounded_steps(store, key)
        if not bounded:
            return None
        item = reader._record("goal", row, sources)
        if item is None:
            return None
        item["native_state"] = item.pop("state")
        leases = {step["step_id"]: _lease(step, sources.now) for step in raw_steps}
        for step in item["steps"]:
            step["lease"] = leases[step["step_id"]]
        commitment = store.one(
            "SELECT c.commitment_id,c.state,c.deadline,o.state delivery_state "
            "FROM commitments c LEFT JOIN outbox o ON o.message_id=c.feedback_message_id "
            "AND o.action_type='qq.private.reply' WHERE c.goal_id=? AND c.state<>'ERASED'", (key,),
        )
        if commitment:
            commitment["told"] = commitment["delivery_state"] == "SENT"
            commitment["state"] = symbol(commitment["state"], {
                "ACKNOWLEDGED", "IN_PROGRESS", "FULFILLED", "EXPLAINED_FAILURE"})
            commitment["delivery_state"] = symbol(commitment["delivery_state"], {
                "PENDING", "LEASED", "SENDING", "SENT", "DELIVERY_UNCERTAIN",
                "CANCELLED", "FAILED", "RETRY_WAIT", "BLOCKED"})
        item.update(kind="goal", commitment=commitment,
                    result=(_result(reader, sources, row["result_event_id"])
                            if item["execution"]["state"] == "done" else None))
        return item
    if kind == "job":
        row = store.one("SELECT * FROM jobs WHERE job_id=?", (key,))
        roots = sorted(set([row["event_id"], *_roots(store, "job_sources", "job_id", key)]))
        if not _available(reader, sources, roots):
            return None
        return {"kind": "job", "id": key, "native_state": symbol(row["state"], STATES),
                "source_event_ids": roots, "lease": _lease(row, sources.now),
                "progress": "NOT_RECORDED"}
    row = store.one("SELECT * FROM exploration_requests WHERE request_id=?", (key,))
    roots = _roots(store, "exploration_evidence", "request_id", key)
    if not _available(reader, sources, roots):
        return None
    attempt = store.one("SELECT * FROM reading_attempts WHERE request_id=?", (key,))
    results = []
    if attempt:
        import json

        raw = attempt["result_event_ids_json"]
        try:
            keys = json.loads(raw) if isinstance(raw, str) and len(raw) <= 16384 else None
        except (ValueError, TypeError, RecursionError):
            keys = None
        if (not isinstance(keys, list) or len(keys) > 20
            or any(not isinstance(value, str) or not 1 <= len(value) <= 160 for value in keys)):
            return None
        results = [_result(reader, sources, value) for value in keys]
        if any(value is None for value in results):
            return None
    return {"kind": "read", "id": key, "native_state": symbol(row["state"], STATES),
            "attempt_state": symbol(attempt and attempt["state"], STATES),
            "source_event_ids": roots, "lease": _lease(attempt or row, sources.now),
            "progress": "NOT_RECORDED", "results": results}


def _attention(reader, sources, selection):
    store, projections, result = reader.store, reader.projections, []
    truncated = False
    for kind, keys in (("memory", selection.memory_ids), ("episode", selection.episode_ids)):
        for key in sorted(set(keys)):
            if kind == "memory":
                row = store.one("SELECT * FROM memories WHERE memory_id=? "
                                "AND privacy_scope='private' AND confidence>0 "
                                "AND status IN ('CONFIRMED','HYPOTHESIS')", (key,))
                roots = [r["event_id"] for r in store.rows(
                    "SELECT event_id FROM memory_evidence WHERE memory_id=? AND polarity=1 "
                    "ORDER BY event_id LIMIT 101", (key,))]
            else:
                row = store.one("SELECT * FROM episodes WHERE episode_id=? AND conversation_id=? "
                                "AND privacy_scope='private' AND status='ACTIVE'",
                                (key, selection.conversation_id))
                roots = _roots(store, "episode_events", "episode_id", key)
            if (not row or kind == "episode" and len(roots) > 20
                or not _available(reader, sources, roots)
                or kind == "memory" and not sources.current(row)):
                truncated = True
                continue
            events = [sources.event(value, require_text=False) for value in roots]
            if any(event is None for event in events):
                truncated = True
                continue
            goals, cut = projections._goals(roots, sources, None)
            bounded_goals = [goal for goal in goals
                             if _bounded_steps(store, goal["goal_id"])[1]]
            cut |= len(bounded_goals) != len(goals)
            goals = bounded_goals
            temperature, reasons = projections._temperature(events, goals, set(), sources.now)
            result.append({"kind": kind, "id": key, "temperature": temperature,
                           "attention_reasons": reasons, "source_event_ids": roots,
                           "goals": [{field: goal[field] for field in (
                               "goal_id", "native_state", "state", "told", "closed")}
                                     for goal in goals], "goals_truncated": cut})
            truncated |= cut
    return {"items": result, "truncated": truncated, "used_in_request": "NOT_RECORDED"}


def snapshot(reader, selection):
    selection = ActivitySelection.model_validate(selection)
    core, store = reader.core, reader.store
    if store.db.in_transaction:
        raise CoreError("ACTIVITY_SNAPSHOT_BUSY", 409)
    version = read_version(core.memory)
    now = core.clock()
    # A deferred read transaction pins all native rows without taking a writer
    # reservation. Recheck *after* ending it, on the latest epoch and Ledger.
    store.execute("BEGIN")
    try:
        conversation = store.one("SELECT 1 FROM conversations WHERE conversation_id=? "
                                 "AND scope='private' AND owner_actor_id='owner'",
                                 (selection.conversation_id,))
        if not conversation:
            raise CoreError("ACTIVITY_CONVERSATION_UNAVAILABLE", 404)
        sources = ConversationSources(core.memory, now, selection.conversation_id)
        candidates = _candidates(store, selection.conversation_id, selection.limit)
        items = []
        truncated = len(candidates) > selection.limit
        for candidate in candidates[:selection.limit]:
            item = _work(reader, sources, candidate)
            if item is None:
                truncated = True
                continue
            items.append({**item, "recorded_at": candidate["recorded_at"]})
        facts = capabilities.snapshot(store, core.settings, lambda: now)["capabilities"]
        result = {"projection_version": 1, "mode": "SHADOW", "as_of": now,
                  "privacy_epoch": version[0], "conversation_id": selection.conversation_id,
                  "items": items, "truncated": truncated,
                  "attention": _attention(reader, sources, selection),
                  "clues": {"state": "NOT_RECORDED", "items": []},
                  "capabilities": [{"id": fact["id"], "state": fact["state"],
                                    "reason_codes": fact["reason_codes"][:20],
                                    "truncated": len(fact["reason_codes"]) > 20}
                                   for fact in facts[:64]],
                  "capabilities_truncated": len(facts) > 64,
                  "foreground": {"clear": foreground_clear(SimpleNamespace(
                      store=store, settings=core.settings, clock=lambda: now)),
                                 "model_busy": private_life.busy(store, now)},
                  "presence": {"state": "UNKNOWN", "reason": "NO_TRUSTED_SCENE_SOURCE"},
                  "interruptibility": "NOT_RECORDED", "progress": "NOT_RECORDED",
                  "reasoning": "NOT_EXPOSED", "source_valid_until": sources.valid_until}
    finally:
        store.db.rollback()
    check_version(core.memory, version)
    if sources.valid_until and core.clock() >= sources.valid_until:
        raise CoreError("PRIVACY_CHANGED", 409)
    return result
