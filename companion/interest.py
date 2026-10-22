"""Self exploration trends use actual completed reads, separately from owner preferences."""

from datetime import datetime
from zoneinfo import ZoneInfo

from companion.contracts import CoreError
from companion.observation import expired_sources
from companion.privacy import epoch, redact, sensitive
from companion.store import after, uid


def exploration_evidence(store, settings, trait_id, now):
    """Only completed self goals count; repeats never add another independent day."""
    cutoff = after(now, -86400 * settings.raw_event_retention_days)
    rows = store.rows(
        "SELECT e.event_id,e.occurred_at,e.source,o.source_sha256,o.canonical_url,o.result_status "
        "FROM trait_evidence t JOIN observations o USING(event_id) "
        "JOIN events e USING(event_id) JOIN traits tr USING(trait_id) "
        "WHERE t.trait_id=? AND tr.category='interest' AND o.redacted=0 AND e.redacted=0 "
        "AND e.ingested_at>=? AND e.occurred_at<=? AND o.result_status IN ('FETCHED','WATCHED') "
        "AND o.source_sha256<>'' AND o.canonical_url<>'' AND EXISTS (SELECT 1 FROM goals g "
        "WHERE g.kind='self' AND g.state='DONE' AND g.redacted=0 "
        "AND g.result_event_id=e.event_id AND substr(g.topic,1,80)=tr.topic) "
        "ORDER BY e.occurred_at,e.event_id",
        (trait_id, cutoff, now),
    )
    evidence, hashes, urls = [], set(), set()
    for row in rows:
        simulated = row["source"] == "observation.simulation"
        if simulated and settings.mode != "demo":
            continue
        if row["source_sha256"] in hashes or row["canonical_url"] in urls:
            continue
        if expired_sources(store, [row["event_id"]], cutoff):
            continue
        hashes.add(row["source_sha256"])
        urls.add(row["canonical_url"])
        evidence.append(
            {**row, "evidence_level": ("SIMULATED_" if simulated else "") + row["result_status"]}
        )
    return evidence


def tendency(evidence, settings):
    days = {
        datetime.fromisoformat(row["occurred_at"]).astimezone(ZoneInfo(settings.timezone)).date()
        for row in evidence
    }
    state = "EXPLORING" if evidence else "FADING"
    if len(evidence) >= settings.emerging_min_sessions:
        state = "STABLE" if len(days) >= settings.stable_min_days else "EMERGING"
    return state, len(evidence), len(days)


def profile(store, settings, now, requester):
    """Private, rebuilt evidence view. Neither namespace grants execution or sharing."""
    if requester != settings.owner_qq_id:
        raise CoreError("OWNER_REQUIRED", 403)
    captured = epoch(store)
    owner = []
    cutoff = after(now, -86400 * settings.raw_event_retention_days)
    for row in store.rows(
        "SELECT memory_id,content,fact_key FROM memories WHERE subject='user' "
        "AND kind='preference' AND status='CONFIRMED' AND needs_review=0 AND confidence>0 "
        "AND privacy_scope='private' ORDER BY updated_at DESC,memory_id LIMIT 40"
    ):
        evidence = store.rows(
            "SELECT e.event_id,e.source,e.occurred_at FROM memory_evidence me "
            "JOIN events e USING(event_id) JOIN conversations c USING(conversation_id) "
            "WHERE me.memory_id=? AND e.redacted=0 AND e.actor_id='owner' "
            "AND e.privacy_scope='private' AND c.scope='private' AND c.owner_actor_id='owner' "
            "AND e.ingested_at>=? AND e.occurred_at<=? AND e.source IN "
            "('qq.onebot','owner.decision') ORDER BY e.occurred_at,e.event_id",
            (row["memory_id"], cutoff, now),
        )
        if evidence and not expired_sources(store, [e["event_id"] for e in evidence], cutoff):
            if not sensitive(row["content"]) and redact(row["content"], settings) == row["content"]:
                owner.append({**row, "namespace": "owner.preference", "evidence": evidence})
    interests = []
    for row in store.rows(
        "SELECT trait_id,topic FROM traits WHERE category='interest' "
        "ORDER BY updated_at DESC,trait_id LIMIT 40"
    ):
        evidence = exploration_evidence(store, settings, row["trait_id"], now)
        state, sessions, days = tendency(evidence, settings)
        if (
            evidence
            and not sensitive(row["topic"])
            and redact(row["topic"], settings) == row["topic"]
        ):
            interests.append(
                {
                    **row,
                    "status": state,
                    "independent_sessions": sessions,
                    "independent_days": days,
                    "evidence": evidence,
                    "namespace": "self.interest",
                    "confidence": min(0.9, 0.2 + days / 10),
                }
            )
    if captured != epoch(store):
        raise CoreError("PRIVACY_CHANGED", 409)
    return {"privacy_epoch": captured, "owner.preference": owner, "self.interest": interests}


def record_exploration(store, settings, goal, event_id, now):
    # Caller owns the goal-completion transaction and has verified its result and source closure.
    topic = goal["topic"][:80]
    prior = store.one("SELECT * FROM traits WHERE category='interest' AND topic=?", (topic,))
    trait_id = prior["trait_id"] if prior else uid("trait")
    if not prior:
        store.insert(
            "traits",
            trait_id=trait_id,
            category="interest",
            topic=topic,
            strength=0.1,
            confidence=0.2,
            status="EXPLORING",
            updated_at=now,
        )
    store.execute(
        "INSERT OR IGNORE INTO trait_evidence VALUES (?,?,1,0.5,?)", (trait_id, event_id, now)
    )
    evidence = exploration_evidence(store, settings, trait_id, now)
    state, independent, days = tendency(evidence, settings)
    store.execute(
        "UPDATE traits SET status=?,independent_sessions=?,independent_days=?,strength=?,"
        "confidence=?,last_evidence_at=?,updated_at=?,version=version+1 WHERE trait_id=?",
        (
            state,
            independent,
            days,
            min(0.9, 0.1 * independent),
            min(0.9, 0.2 + days / 10) if independent else 0,
            now,
            now,
            trait_id,
        ),
    )
