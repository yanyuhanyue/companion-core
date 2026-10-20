"""Self exploration trends use actual completed reads, separately from owner preferences."""

from datetime import datetime
from zoneinfo import ZoneInfo

from companion.store import uid


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
    evidence = store.rows(
        "SELECT o.source_sha256,o.canonical_url,e.occurred_at FROM trait_evidence t "
        "JOIN observations o ON "
        "o.event_id=t.event_id JOIN events e ON e.event_id=t.event_id WHERE t.trait_id=? AND "
        "o.redacted=0 AND e.redacted=0 AND o.result_status IN ('FETCHED','WATCHED')",
        (trait_id,),
    )
    sources = {row["source_sha256"] for row in evidence}
    targets = {row["canonical_url"] for row in evidence if row["canonical_url"]}
    independent = min(len(sources), len(targets))
    days = {
        datetime.fromisoformat(row["occurred_at"]).astimezone(ZoneInfo(settings.timezone)).date()
        for row in evidence
    }
    state = "EXPLORING"
    if independent >= settings.emerging_min_sessions:
        state = "EMERGING"
        if len(days) >= settings.stable_min_days:
            state = "STABLE"
    store.execute(
        "UPDATE traits SET status=?,independent_sessions=?,independent_days=?,strength=?,"
        "confidence=?,last_evidence_at=?,updated_at=?,version=version+1 WHERE trait_id=?",
        (
            state,
            independent,
            len(days),
            min(0.9, 0.1 * independent),
            min(0.9, 0.2 + len(days) / 10),
            now,
            now,
            trait_id,
        ),
    )
