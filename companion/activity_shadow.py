"""One initial, non-executing diagnostic over already-consumed private sources.

The owner inspector is not a model grant. Global Goal references never nominate
sources here. Original context checks run outside this optional diagnostic, and
the original checkpoint/erasure path owns its lifetime; restore never refreshes it.
"""

import json

from companion import owner_authority, owner_input
from companion.contracts import CoreError
from companion.memory_context import check_version, read_version
from companion.store import after, encode

MAX_BYTES = 2048
MAX_SOURCES = 100
MAX_ITEMS = 3


def _empty():
    return {"kind": "activity_shadow", "version": 1, "mode": "SHADOW",
            "used_in_request": False, "work": [], "attention": [],
            "presence": "UNKNOWN", "progress": "NOT_RECORDED",
            "interruptibility": "NOT_RECORDED",
            "omitted": {"scope": 0, "limit": 0, "unavailable": 0}}


def _omit(result, reason):
    result["omitted"][reason] = min(MAX_SOURCES, result["omitted"][reason] + 1)


def _append(result, group, item):
    if len(result[group]) == MAX_ITEMS:
        _omit(result, "limit")
        return
    result[group].append(item)
    roots = {source for row in [*result["work"], *result["attention"]]
             for source in row["source_event_ids"]}
    bounded = {**result, "omitted": {key: MAX_SOURCES for key in result["omitted"]}}
    if len(roots) > MAX_SOURCES or len(encode(bounded).encode()) > MAX_BYTES:
        result[group].pop()
        _omit(result, "limit")


def _scope(core, job, snapshots, view, result):
    """Only current/selected history/selected memory qualify before job_sources."""
    s, cfg, now = core.store, core.settings, core.clock()
    owner_input.source(s, cfg, view, job["event_id"], now)
    legal, memories = {job["event_id"]}, []
    for snapshot in snapshots:
        if snapshot["kind"] == "history" and "history" in view.policy.purposes:
            owner_input.check_history(s, cfg, core.clock, job, snapshot["proof"])
            legal.update(item["event_id"] for item in snapshot["proof"])
        elif (snapshot["kind"] == "memory" and "memory" in view.policy.purposes
              and view.policy.memory_scope == "owner_private"):
            proof = snapshot["proof"]
            roots = set(proof["source_event_ids"])
            local = {row["event_id"] for row in s.rows(
                "SELECT event_id FROM events WHERE conversation_id=? AND actor_id='owner' "
                "AND privacy_scope='private' AND redacted=0 "
                "AND ingested_at>=? AND ingested_at<=? AND occurred_at<=? "
                "AND event_id IN (SELECT value FROM json_each(?))",
                (job["conversation_id"], after(now, -86400 * min(
                    cfg.raw_event_retention_days, view.policy.source_retention_days)),
                 now, now, encode(sorted(roots)[:MAX_SOURCES])),
            )}
            if not roots or len(roots) > MAX_SOURCES or roots != local:
                _omit(result, "scope")
                continue
            core.memory_context.revalidate(proof, now)
            legal.update(proof["source_event_ids"])
            memories.extend(proof["rows"])
    if len(legal) > MAX_SOURCES:
        _omit(result, "limit")
    legal = {job["event_id"], *sorted(legal - {job["event_id"]})[:MAX_SOURCES - 1]}
    # Metadata-only filter, before any candidate Goal/Observation content is read.
    legal = {row["event_id"] for row in s.rows(
        "SELECT e.event_id FROM events e JOIN conversations c USING(conversation_id) "
        "JOIN job_sources j ON j.event_id=e.event_id WHERE j.job_id=? "
        "AND e.event_id IN (SELECT value FROM json_each(?)) AND e.conversation_id=? "
        "AND e.actor_id='owner' AND e.privacy_scope='private' AND e.redacted=0 "
        "AND c.scope='private' AND c.owner_actor_id='owner'",
        (job["job_id"], encode(sorted(legal)), job["conversation_id"]),
    )}
    return legal, memories


def _goals(core, job, view, legal, result):
    if "goals" not in view.policy.purposes:
        return
    s = core.store
    candidates = s.rows(
        "SELECT DISTINCT g.goal_id,g.updated_at FROM goals g "
        "JOIN goal_evidence e USING(goal_id) WHERE g.redacted=0 "
        "AND e.event_id IN (SELECT value FROM json_each(?)) "
        "ORDER BY g.updated_at DESC,g.goal_id LIMIT 4", (encode(sorted(legal)),),
    )
    for candidate in candidates:
        key = candidate["goal_id"]
        roots = [row["event_id"] for row in s.rows(
            "SELECT event_id FROM goal_evidence WHERE goal_id=? ORDER BY event_id LIMIT 101",
            (key,),
        )]
        if not roots or not set(roots) <= legal:
            _omit(result, "scope")
            continue
        goal = s.one("SELECT goal_id,kind,state,version,authority_json FROM goals "
                     "WHERE goal_id=? AND redacted=0", (key,))
        if not goal or json.loads(goal["authority_json"] or "null") != view.authority.as_dict():
            _omit(result, "scope")
            continue
        core.goals.validate_authority(goal)
        core.goals.validate_sources(roots)
        item = {"kind": "goal", "goal_id": key, "recorded_state": goal["state"],
                "version": goal["version"], "execution": "NOT_EVALUATED",
                "source_event_ids": roots}
        if goal["kind"] == "commitment":
            accepted = s.one("SELECT accepted_event_id FROM commitments WHERE goal_id=?", (key,))
            if not accepted or accepted["accepted_event_id"] not in legal:
                _omit(result, "scope")
                continue
            item["commitment"] = s.one(
                "SELECT c.state recorded_state,c.deadline,o.state delivery_state "
                "FROM commitments c LEFT JOIN outbox o ON o.message_id=c.feedback_message_id "
                "WHERE c.goal_id=?", (key,),
            )
        _append(result, "work", item)


def _readers(core, job, snapshots, view, legal, result):
    if "content" not in view.policy.purposes:
        return
    from companion.agent_reading import revalidate_results

    proofs = [item for item in snapshots if item["kind"] == "reader"]
    if len(proofs) > MAX_ITEMS:
        _omit(result, "limit")
    for proof in proofs[:MAX_ITEMS]:
        key = proof["request_id"]
        roots = [row["event_id"] for row in core.store.rows(
            "SELECT event_id FROM exploration_evidence WHERE request_id=? "
            "ORDER BY event_id LIMIT 101", (key,),
        )]
        attempt = core.store.one("SELECT result_event_ids_json FROM reading_attempts "
                                  "WHERE request_id=?", (key,))
        results = json.loads(attempt["result_event_ids_json"]) if attempt else []
        if (not isinstance(results, list) or len(results) > 40
            or any(not isinstance(event_id, str) for event_id in results)):
            _omit(result, "unavailable")
            continue
        owners = set(roots) - set(results)
        if not owners or not owners <= legal:
            _omit(result, "scope")
            continue
        consumed = {row["event_id"] for row in core.store.rows(
            "SELECT event_id FROM job_sources WHERE job_id=? "
            "AND event_id IN (SELECT value FROM json_each(?))",
            (job["job_id"], encode(results)),
        )}
        if set(results) != consumed:
            _omit(result, "scope")
            continue
        # Original checker retains chat-only custody and captured Reader grants.
        # It never accepts Goal-driven results. No Observation body is persisted.
        revalidate_results(core.store, core.settings, core.clock, job, [proof], executing=True)
        row = core.store.one("SELECT state FROM exploration_requests WHERE request_id=?", (key,))
        _append(result, "work", {"kind": "reader", "request_id": key,
            "recorded_state": row["state"], "execution": "ORIGINAL_READER_PROOF",
            "source_event_ids": sorted(set(roots) | set(results))})


def _sample(core, job, snapshots):
    result = _empty()
    version = read_version(core.memory)
    view = owner_authority.check_job(core.store, core.settings, core.clock, job,
                                     purpose="chat", executing=True)
    legal, memories = _scope(core, job, snapshots, view, result)
    _goals(core, job, view, legal, result)
    _readers(core, job, snapshots, view, legal, result)
    if len(memories) > MAX_ITEMS:
        _omit(result, "limit")
    for memory in memories[:MAX_ITEMS]:
        roots = sorted(set(memory["evidence_ids"]))
        if not roots or not set(roots) <= legal:
            _omit(result, "scope")
            continue
        _append(result, "attention", {"memory_id": memory["memory_id"],
            "recorded_status": memory["status"], "source_event_ids": roots})
    check_version(core.memory, version)
    return result


def capture(core, job, snapshots):
    """Append at successful initial Core context only; no runtime proof or I/O effects."""
    cfg = core.settings.activity_context
    if (cfg is None or cfg.mode != "SHADOW"
        or any(item["kind"] == "activity_shadow" for item in snapshots)):
        return
    try:
        result = _sample(core, job, snapshots)
    except (CoreError, ValueError, KeyError, TypeError):
        # Optional sampling errors produce no data. Original context/authority/
        # privacy errors are checked by the caller outside this try/except.
        result = _empty()
        _omit(result, "unavailable")
    snapshots.append(result)
