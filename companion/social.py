"""One owner-only sharing path, with common proactive limits and source erasure."""

import hashlib
import json
import re
from datetime import UTC, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from companion.contracts import CoreError
from companion.privacy import epoch
from companion.sources import content_target, observation_sources, source_definition, supports
from companion.store import after, uid, utcnow
from companion.transport import enqueue


def quiet_hours(settings, now):
    hour = datetime.fromisoformat(now).astimezone(ZoneInfo(settings.timezone)).hour
    start, end = settings.quiet_start, settings.quiet_end
    return start != end and (start <= hour < end if start < end else hour >= start or hour < end)


def proactive_allowed(store, settings, now):
    if store.one(
        "SELECT 1 FROM runtime_meta WHERE key IN ('proactive_paused','life_paused') AND value='1'"
    ):
        return False
    if settings is None:
        return True
    if not settings.proactive_enabled or quiet_hours(settings, now):
        return False
    from companion.affect import AffectEngine

    return AffectEngine(store, settings, lambda: now).snapshot()["state"]["engagement"] != "pause"


def proactive_counts(store, settings, now):
    day = (
        datetime.fromisoformat(now)
        .astimezone(ZoneInfo(settings.timezone))
        .replace(hour=0, minute=0, second=0, microsecond=0)
        .astimezone(UTC)
        .isoformat(timespec="microseconds")
    )
    recent = after(now, -settings.proactive_interval_seconds)
    # Activity jobs reserve a send slot before generation; social candidates reserve at queue.
    row = store.one(
        "SELECT COUNT(*) AS n,MAX(created_at)>? AS recent FROM ("
        "SELECT created_at FROM jobs WHERE kind='activity' AND created_at>=? UNION ALL "
        "SELECT updated_at AS created_at FROM social_candidates WHERE message_id IS NOT NULL "
        "AND updated_at>=?)",
        (recent, min(day, recent), min(day, recent)),
    )
    daily = store.one(
        "SELECT (SELECT COUNT(*) FROM jobs WHERE kind='activity' AND created_at>=?) + "
        "(SELECT COUNT(*) FROM social_candidates WHERE message_id IS NOT NULL AND updated_at>=?) n",
        (day, day),
    )["n"]
    return daily, bool(row["recent"])


def text_key(text):
    return re.sub(r"[^\w\u4e00-\u9fff]", "", text.casefold())


def semantic_duplicate(text, candidates, settings):
    if not candidates:
        return False
    import numpy as np

    from companion.retrieval import encoder

    model = encoder(str(Path(settings.embedding_cache_dir).resolve()))
    vectors = list(model.passage_embed([text] + candidates, batch_size=32))
    return any(
        float(np.dot(vectors[0], vector)) >= settings.social_semantic_threshold
        for vector in vectors[1:]
    )


def share_source(event):
    """Accepted historical text stays usable; revoked future reads do not erase it.

    Recheck stored attribution before queueing or sending, without fetching or
    requiring a still-live grant. Only explicit erasure invalidates that history.
    """
    try:
        if (
            event["source"] not in observation_sources("share")
            or event["source_kind"] != "text"
            or event["result_status"] != "FETCHED"
        ):
            return None
        definition = source_definition(event["source"].removeprefix("observation."))
        body = json.loads(event["body_json"])
        if (
            not isinstance(body["title"], str)
            or not isinstance(body["text"], str)
            or not body["text"].strip()
            or hashlib.sha256(body["text"].encode()).hexdigest() != event["source_sha256"]
        ):
            return None
        canonical = event["canonical_url"]
        if not isinstance(canonical, str):
            return None
        source = definition.name
        if canonical:
            source, canonical = content_target(canonical)
            if source != definition.name:
                return None
        evidence = json.loads(event["evidence_json"])
        if any(evidence.get(kind) is not None and not supports(source, kind)
               for kind in ("reading", "article")):
            return None
        return definition, body, canonical
    except (AttributeError, CoreError, KeyError, TypeError, ValueError):
        return None


class SocialPlanner:
    def __init__(self, store, settings, clock=utcnow):
        self.store, self.settings, self.clock = store, settings, clock

    def consider(self, event_id):
        if not self.settings.social_enabled:
            return None
        s, now, captured = self.store, self.clock(), epoch(self.store)
        event = s.one(
            "SELECT o.*,e.body_json,e.source FROM observations o JOIN events e USING(event_id) "
            "WHERE o.event_id=? AND o.redacted=0 AND e.redacted=0 AND o.result_status='FETCHED' "
            "AND e.source IN (SELECT value FROM json_each(?))",
            (event_id, json.dumps(observation_sources("share"))),
        )
        if not event:
            return None
        resolved = share_source(event)
        if resolved is None:
            return None
        _, body, canonical = resolved
        existing = s.one("SELECT candidate_id FROM social_candidates WHERE event_id=?", (event_id,))
        if existing:
            return existing["candidate_id"]
        comparison = body["title"] + "\n" + body["text"][:1500]
        normalized = text_key(comparison)
        key = hashlib.sha256(normalized.encode()).hexdigest()
        candidate_id = uid("share")
        for attempt in range(3):
            generation = s.one("SELECT COALESCE(MAX(rowid),0) AS n FROM social_candidates")["n"]
            recent = s.rows(
                "SELECT * FROM social_candidates WHERE state<>'ERASED' AND created_at>=? "
                "ORDER BY rowid DESC LIMIT 100",
                (after(now, -30 * 86400),),
            )
            reason = None
            if s.one(
                "SELECT 1 FROM social_candidates WHERE state<>'ERASED' "
                "AND (canonical_url=? OR content_sha256=?)",
                (canonical, key),
            ):
                reason = "DUPLICATE_CONTENT"
            elif recent:
                try:
                    if semantic_duplicate(
                        comparison, [row["comparison_text"] for row in recent], self.settings
                    ):
                        reason = "SIMILAR_CONTENT"
                except Exception:
                    reason = "DEDUPE_UNAVAILABLE"
            with s.transaction():
                if captured != epoch(s):
                    return None
                if s.one("SELECT 1 FROM social_candidates WHERE event_id=?", (event_id,)):
                    return None
                latest = s.one("SELECT COALESCE(MAX(rowid),0) AS n FROM social_candidates")["n"]
                if latest != generation:
                    # Recompute outside the write transaction against concurrently added content.
                    if attempt < 2:
                        continue
                    reason = "DEDUPE_CONTENDED"
                s.insert(
                    "social_candidates",
                    candidate_id=candidate_id,
                    event_id=event_id,
                    canonical_url=canonical,
                    content_sha256=key,
                    comparison_text=comparison,
                    state="SKIPPED" if reason else "CANDIDATE",
                    reason_code=reason,
                    expires_at=after(now, 86400),
                    created_at=now,
                    updated_at=now,
                )
            return candidate_id

    def flush(self):
        cfg, s, now = self.settings, self.store, self.clock()
        if not cfg.social_enabled:
            return False
        with s.transaction():
            if not proactive_allowed(s, cfg, now):
                return False
            s.execute(
                "UPDATE social_candidates SET state='EXPIRED' WHERE state='CANDIDATE' "
                "AND expires_at<=?",
                (now,),
            )
            conversation = s.one(
                "SELECT * FROM conversations WHERE platform='qq.onebot' AND scope='private' "
                "AND owner_actor_id='owner' ORDER BY last_event_at DESC LIMIT 1"
            )
            if not conversation or conversation["last_event_at"] > after(
                now, -cfg.proactive_idle_seconds
            ):
                return False
            if s.one("SELECT 1 FROM jobs WHERE state IN ('QUEUED','RUNNING','RETRY_WAIT')"):
                return False
            if s.one(
                "SELECT 1 FROM outbox WHERE state IN "
                "('PENDING','LEASED','SENDING','RETRY_WAIT','DELIVERY_UNCERTAIN')"
            ):
                return False
            count, recent = proactive_counts(s, cfg, now)
            if recent or count >= cfg.proactive_daily_limit:
                return False
            candidate = s.one(
                "SELECT c.*,e.body_json,e.source,o.source_kind,o.result_status,"
                "o.source_sha256,o.evidence_json,o.canonical_url AS source_url "
                "FROM social_candidates c JOIN events e USING(event_id) "
                "JOIN observations o USING(event_id) WHERE c.state='CANDIDATE' "
                "AND e.redacted=0 AND o.redacted=0 ORDER BY c.created_at LIMIT 1"
            )
            if not candidate:
                return False
            resolved = share_source({**candidate, "canonical_url": candidate["source_url"]})
            if resolved is None or resolved[2] != candidate["canonical_url"]:
                s.execute(
                    "UPDATE social_candidates SET state='SKIPPED',reason_code='SOURCE_CHANGED',"
                    "updated_at=? WHERE candidate_id=?",
                    (now, candidate["candidate_id"]),
                )
                return False
            definition, body, _ = resolved
            label = definition.share_label
            text = (
                "读到一段"
                + label
                + "，想分享给你：\n"
                + body["title"]
                + "\n"
                + body["text"][:500]
                + "\n"
                + candidate["canonical_url"]
            )
            message_id = enqueue(
                s,
                candidate["event_id"],
                conversation["conversation_id"],
                text,
                now,
                action="qq.private.proactive",
                key="share:" + candidate["candidate_id"],
            )
            s.execute(
                "UPDATE social_candidates SET state='QUEUED',message_id=?,updated_at=? "
                "WHERE candidate_id=?",
                (message_id, now, candidate["candidate_id"]),
            )
        return True
