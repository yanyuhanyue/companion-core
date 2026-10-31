"""Bounded views of original evidence, never inferred owner ratings or new memory."""

import json

from companion import affect, interest, owner_authority
from companion.contracts import CoreError
from companion.paid_budget import sha
from companion.privacy import redact, sensitive
from companion.store import encode


def read_targets(settings, sources):
    from companion.content import shared_links
    from companion.sources import web_hosts

    hosts = web_hosts(settings)
    targets = []
    for source in sources:
        for token in [source.get("url", ""), *source["text"].split()]:
            for target in shared_links(token, hosts):
                if target not in targets:
                    targets.append(target)
                if len(targets) == 24:
                    return targets
    return targets


def permitted(store, settings, now, purpose):
    if not owner_authority.required(store, settings):
        return True
    try:
        owner_authority.current(store, settings, lambda: now, purpose=purpose)
        return True
    except CoreError:
        return False


def decision_context(store, settings, sources, authority_sha256, now):
    """Read only evidence already admitted into this exact activity's source set."""
    ids = {source["event_id"] for source in sources}
    result = {"owner_feedback": [], "self_interests": [], "execution_outcomes": []}
    if settings.affect_enabled and permitted(store, settings, now, "affect"):
        feedback = [affect.owner_feedback(store, settings, event_id, now)
                    for event_id in sorted(ids)]
        latest = {}
        for item in sorted((x for x in feedback if x),
                           key=lambda x: (x["occurred_at"], x["event_id"])):
            latest[item["dimension"]] = item
        result["owner_feedback"] = [latest[key] for key in sorted(latest)]
    goals = store.rows(
        "SELECT DISTINCT g.* FROM goals g JOIN goal_evidence e USING(goal_id) "
        "WHERE e.event_id IN (SELECT value FROM json_each(?)) AND g.kind='self' "
        "AND g.redacted=0 ORDER BY g.updated_at DESC,g.goal_id LIMIT 8", (encode(sorted(ids)),),
    )
    for goal in goals:
        roots = {row["event_id"] for row in store.rows(
            "SELECT event_id FROM goal_evidence WHERE goal_id=?", (goal["goal_id"],))}
        if not roots or not roots <= ids:
            continue
        if owner_authority.required(store, settings):
            if (not goal["authority_json"]
                or sha(json.loads(goal["authority_json"])) != authority_sha256):
                continue
        steps = store.rows("SELECT state,last_error_code,outcome_event_id FROM goal_steps "
                           "WHERE goal_id=? ORDER BY step_id", (goal["goal_id"],))
        unknown = any(step["state"] == "EXECUTION_UNCERTAIN" for step in steps) or bool(store.one(
            "SELECT 1 FROM exploration_requests WHERE state='UNKNOWN' "
            "AND json_extract(goal_step_json,'$.goal_id')=? LIMIT 1", (goal["goal_id"],),
        ))
        if unknown or goal["state"] in {"DONE", "FAILED"}:
            result["execution_outcomes"].append({
                "goal_id": goal["goal_id"], "version": goal["version"],
                "state": "UNKNOWN" if unknown else goal["state"],
                "failure_code": goal["last_error_code"],
                "requires_reconciliation": unknown,
                "source_event_ids": sorted(roots),
            })
        # Phase 1: Interest from any completed goal, not just result_event_id in sources.
        if (goal["state"] != "DONE" or not goal["result_event_id"]
            or not settings.memory_enabled or not permitted(store, settings, now, "memory")):
            continue
        topic = goal["topic"][:80]
        if sensitive(topic) or redact(topic, settings) != topic:
            continue
        trait = store.one("SELECT trait_id FROM traits WHERE category='interest' AND topic=?",
                          (topic,))
        if not trait:
            continue
        # A plan contributes its final completed result once, never one vote per step.
        evidence = store.rows(
            "SELECT e.event_id,e.occurred_at FROM trait_evidence t "
            "JOIN events e USING(event_id) JOIN observations o USING(event_id) "
            "WHERE t.trait_id=? AND e.event_id=? AND e.redacted=0 AND o.redacted=0 "
            "AND o.result_status IN ('FETCHED','WATCHED') AND o.source_sha256<>'' "
            "AND o.canonical_url<>'' AND e.occurred_at<=?",
            (trait["trait_id"], goal["result_event_id"], now),
        )
        if evidence:
            state, sessions, days = interest.tendency(evidence, settings)
            result["self_interests"].append({
                "namespace": "self.interest", "topic": topic, "status": state,
                "trait_id": trait["trait_id"],
                "goal_id": goal["goal_id"], "independent_sessions": sessions,
                "independent_days": days,
                "source_event_ids": sorted(roots | {goal["result_event_id"]}),
            })
    return result
