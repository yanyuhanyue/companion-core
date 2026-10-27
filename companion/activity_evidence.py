"""Goal read evidence AFTER original source admissions, inside the caller transaction."""

import json
from dataclasses import dataclass
from datetime import datetime

from companion import owner_authority, owner_input
from companion.agent_reading import checked_result
from companion.cognition_goal import execution
from companion.contracts import CoreError
from companion.goals import GoalEngine
from companion.memory import Memory
from companion.memory_context import MemoryContext, MemorySources, check_version, read_version
from companion.memory_projections import MemoryProjections
from companion.paid_budget import sha
from companion.privacy import ErasureLedger
from companion.store import after, encode


@dataclass(frozen=True)
class SourceAdmissions:
    job_id: str
    conversation_id: str
    authority_sha256: str
    origins_sha256: str
    origins: tuple

    def revalidate(self, store, settings, clock, job, *, executing):
        if (self.job_id != job["job_id"] or self.conversation_id != job["conversation_id"]
            or self.authority_sha256 != sha(json.loads(job["authority_json"]))
            or self.origins_sha256 != sha(self.origins)):
            raise CoreError("ACTIVITY_ADMISSIONS_CHANGED", 409)
        view = owner_authority.check_job(store, settings, clock, job,
            purpose="chat", executing=executing)
        owner_input.source(store, settings, view, job["event_id"], clock())
        owners = {job["event_id"]}
        memory = Memory(store, settings, ErasureLedger(settings.erasure_ledger))
        cutoff = after(clock(), -86400 * min(settings.raw_event_retention_days,
                                            view.policy.source_retention_days))
        for entry in self.origins:
            kind = entry["kind"]
            if kind == "history":
                owner_input.check_history(store, settings, clock, job, entry["proof"])
                owners.update(item["event_id"] for item in entry["proof"])
            elif kind == "memory":
                owner_authority.check_job(store, settings, clock, job,
                    purpose="memory", executing=executing)
                ids = set(entry["proof"]["source_event_ids"])
                try:
                    native_lineage(store, ids, owner_ids=ids,
                        conversation_id=self.conversation_id, now=clock(), cutoff=cutoff)
                except CoreError as error:
                    if error.code != "ACTIVITY_SOURCE_UNAVAILABLE":
                        raise
                    continue  # Narrow runtime scope, not permission to read this Memory body.
                MemoryContext(memory).revalidate(entry["proof"], clock())
                owners.update(r["event_id"] for r in store.rows(
                    "SELECT event_id FROM events WHERE actor_id='owner' AND "
                    "event_id IN (SELECT value FROM json_each(?))", (encode(sorted(ids)),)))
        if len(owners) > 100:
            raise CoreError("ACTIVITY_SOURCE_UNAVAILABLE", 409)
        for entry in self.origins:
            if entry["kind"] == "reader":
                from companion.agent_reading import revalidate_results

                ids = {r["event_id"] for r in store.rows(
                    "SELECT event_id FROM exploration_evidence WHERE request_id=? LIMIT 101",
                    (entry["request_id"],))}
                native_lineage(store, ids, owner_ids=owners,
                    conversation_id=self.conversation_id, now=clock(), cutoff=cutoff)
                revalidate_results(store, settings, clock, job, [entry], executing=executing)
        return owners


def native_lineage(store, ids, *, owner_ids, conversation_id, now, cutoff):
    """Metadata only. The initial scope accepts native QQ roots and native observations."""
    if not store.db.in_transaction:
        raise RuntimeError("activity evidence requires caller transaction")
    rows = store.rows(
        "WITH RECURSIVE n(id) AS (SELECT value FROM json_each(?) UNION "
        "SELECT d.source_event_id FROM event_dependencies d JOIN n ON d.event_id=n.id) "
        "SELECT n.id,e.event_id,e.source,e.event_type,e.actor_id,e.conversation_id,"
        "e.privacy_scope,e.occurred_at,e.ingested_at,e.redacted,c.scope,c.owner_actor_id,"
        "r.key erased FROM n LEFT JOIN events e ON e.event_id=n.id "
        "LEFT JOIN conversations c USING(conversation_id) "
        "LEFT JOIN runtime_meta r ON r.key='erased_event:'||n.id LIMIT 101",
        (encode(sorted(ids)),))
    if not rows or len(rows) > 100:
        raise CoreError("ACTIVITY_SOURCE_UNAVAILABLE", 409)
    found = {r["event_id"]: r for r in rows}
    for row in rows:
        if (not row["event_id"] or row["redacted"] or row["erased"]
            or row["privacy_scope"] != "private" or row["scope"] != "private"
            or row["owner_actor_id"] != "owner" or row["source"] == "observation.simulation"
            or datetime.fromisoformat(row["ingested_at"]) < datetime.fromisoformat(cutoff)
            or any(datetime.fromisoformat(row[k]) > datetime.fromisoformat(now)
                   for k in ("occurred_at", "ingested_at"))):
            raise CoreError("ACTIVITY_SOURCE_UNAVAILABLE", 409)
        if row["actor_id"] == "owner":
            if (row["event_id"] not in owner_ids or row["conversation_id"] != conversation_id
                or (row["source"], row["event_type"]) != ("qq.onebot", "message.received")):
                raise CoreError("ACTIVITY_SOURCE_UNAVAILABLE", 409)
        elif row["event_type"] != "content.observed":
            raise CoreError("ACTIVITY_SOURCE_UNAVAILABLE", 409)
    parents = {key: {r["source_event_id"] for r in store.rows(
        "SELECT source_event_id FROM event_dependencies WHERE event_id=?", (key,))}
        for key in found}
    for key in found:
        seen, pending = set(), [key]
        while pending:
            current = pending.pop()
            if current not in seen:
                seen.add(current)
                pending.extend(parents.get(current, ()))
        if not seen <= found.keys() or not seen & owner_ids:
            raise CoreError("ACTIVITY_SOURCE_UNAVAILABLE", 409)
    return set(found)


def read_goal_execution(store, settings, clock, *, job, goal_id, admissions, executing=False):
    """Returns original execution view + closed sources + exact checked read results.

    SourceAdmissions.revalidate is the proof-derived interface specified in the
    proposal manifest, NOT a new grant and NOT an untrusted set of IDs.
    """
    if not store.db.in_transaction:
        raise RuntimeError("activity evidence requires caller transaction")
    memory = Memory(store, settings, ErasureLedger(settings.erasure_ledger))
    version, now = read_version(memory), clock()
    owner_ids = admissions.revalidate(store, settings, clock, job, executing=executing)
    view = owner_authority.check_job(store, settings, clock, job,
                                    purpose="goals", executing=executing)
    roots = {r["event_id"] for r in store.rows(
        "SELECT event_id FROM goal_evidence WHERE goal_id=? LIMIT 101", (goal_id,))}
    if not roots or not roots <= owner_ids:
        raise CoreError("ACTIVITY_SOURCE_UNAVAILABLE", 409)
    engine = GoalEngine(store, settings, None, clock)
    meta = store.one("SELECT authority_json FROM goals WHERE goal_id=? AND redacted=0", (goal_id,))
    if not meta:
        raise CoreError("ACTIVITY_SOURCE_UNAVAILABLE", 409)
    engine.validate_authority(meta)
    owner_authority.check_job(store, settings, clock, job, purpose="content", executing=executing)
    cutoff = after(now, -86400 * min(settings.raw_event_retention_days,
                                   view.policy.source_retention_days))
    allowed = native_lineage(store, roots, owner_ids=owner_ids,
        conversation_id=job["conversation_id"], now=now, cutoff=cutoff)
    goal = engine.get(goal_id)  # Topic/scope read only AFTER admission and domain checks.
    steps = store.rows("SELECT step_id,state,outcome_event_id FROM goal_steps "
                       "WHERE goal_id=? ORDER BY position LIMIT 4", (goal_id,))
    if not 1 <= len(steps) <= 3:
        raise CoreError("ACTIVITY_SOURCE_UNAVAILABLE", 409)
    reads = []
    for meta_step in steps:
        ids = {r["event_id"] for r in store.rows(
            "SELECT event_id FROM goal_step_evidence WHERE step_id=? LIMIT 101",
            (meta_step["step_id"],))}
        if meta_step["outcome_event_id"]:
            ids.add(meta_step["outcome_event_id"])
        qualified = native_lineage(store, ids, owner_ids=owner_ids,
            conversation_id=job["conversation_id"], now=now, cutoff=cutoff)
        if len(allowed | qualified) > 100:
            raise CoreError("ACTIVITY_SOURCE_UNAVAILABLE", 409)
        allowed.update(qualified)
        step = store.one("SELECT * FROM goal_steps WHERE step_id=?", (meta_step["step_id"],))
        engine.validate_step_scope(goal, step)
        requests = store.rows("SELECT request_id FROM exploration_requests WHERE redacted=0 "
            "AND operation='read' AND json_extract(goal_step_json,'$.goal_id')=? "
            "AND json_extract(goal_step_json,'$.step_id')=? ORDER BY request_id LIMIT 21",
            (goal_id, step["step_id"]))
        if len(requests) > 20:
            raise CoreError("ACTIVITY_SOURCE_UNAVAILABLE", 409)
        matched = False
        for request in requests:
            evidence = {r["event_id"] for r in store.rows(
                "SELECT event_id FROM exploration_evidence WHERE request_id=? LIMIT 101",
                (request["request_id"],))}
            qualified = native_lineage(store, evidence, owner_ids=owner_ids,
                conversation_id=job["conversation_id"], now=now, cutoff=cutoff)
            if len(allowed | qualified) > 100:
                raise CoreError("ACTIVITY_SOURCE_UNAVAILABLE", 409)
            allowed.update(qualified)
            row = store.one("SELECT * FROM exploration_requests WHERE request_id=?",
                            (request["request_id"],))
            ref = json.loads(row["goal_step_json"])
            if ref.get("scope_json") != step["scope_json"]:
                raise CoreError("GOAL_STEP_SCOPE_MISMATCH", 409)

            def admit_results(result_ids):
                qualified = native_lineage(store, result_ids, owner_ids=owner_ids,
                    conversation_id=job["conversation_id"], now=now, cutoff=cutoff)
                if len(allowed | qualified) > 100:
                    raise CoreError("ACTIVITY_SOURCE_UNAVAILABLE", 409)
                allowed.update(qualified)

            result = checked_result(store, settings, clock, row, sorted(evidence),
                                    authorize_results=admit_results)
            for item in result["items"]:
                if item["event_id"] == step["outcome_event_id"]:
                    engine.validate_result_target(step, {"canonical_url": item["url"],
                                                        "evidence_json": encode(item["evidence"])})
                    matched = True
            reads.append(result)
        if step["state"] == "DONE" and not matched:
            raise CoreError("GOAL_RESULT_REQUEST_MISMATCH", 409)
    if len(allowed) > 100:
        raise CoreError("ACTIVITY_SOURCE_UNAVAILABLE", 409)
    engine.validate_sources(sorted(allowed))
    value = execution(store, MemoryProjections(memory), goal, MemorySources(memory, now),
                      allowed=allowed)
    if value is None or value["execution"]["truncated"]:
        raise CoreError("ACTIVITY_SOURCE_UNAVAILABLE", 409)
    check_version(memory, version)
    return {"goal_id": goal_id, "execution": value,
            "source_event_ids": sorted(allowed), "reads": reads}
