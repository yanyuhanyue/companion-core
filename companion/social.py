"""One owner-only sharing path, with common proactive limits and source erasure."""

import hashlib
import json
import re
from datetime import UTC, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from companion.privacy import epoch
from companion.store import after, uid
from companion.transport import enqueue


def quiet_hours(settings, now):
    hour = datetime.fromisoformat(now).astimezone(ZoneInfo(settings.timezone)).hour
    start, end = settings.quiet_start, settings.quiet_end
    return start != end and (start <= hour < end if start < end else hour >= start or hour < end)


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


class SocialPlanner:
    def __init__(self, core):
        self.core, self.store, self.settings, self.clock = (
            core,
            core.store,
            core.settings,
            core.clock,
        )

    def consider(self, event_id):
        if not self.settings.social_enabled:
            return None
        s, now, captured = self.store, self.clock(), epoch(self.store)
        event = s.one(
            "SELECT o.*,e.body_json FROM observations o JOIN events e USING(event_id) "
            "WHERE o.event_id=? AND o.redacted=0 AND e.redacted=0 AND o.result_status='FETCHED' "
            "AND e.source IN ('observation.bilibili','observation.heybox')",
            (event_id,),
        )
        if not event:
            return None
        existing = s.one("SELECT candidate_id FROM social_candidates WHERE event_id=?", (event_id,))
        if existing:
            return existing["candidate_id"]
        body = json.loads(event["body_json"])
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
                (event["canonical_url"], key),
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
                    canonical_url=event["canonical_url"],
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
        if not cfg.social_enabled or not cfg.proactive_enabled or quiet_hours(cfg, now):
            return False
        with s.transaction():
            for key in ("life_paused", "proactive_paused"):
                paused = s.one("SELECT value FROM runtime_meta WHERE key=?", (key,))
                if paused and paused["value"] == "1":
                    return False
            s.execute(
                "UPDATE social_candidates SET state='EXPIRED' WHERE state='CANDIDATE' "
                "AND expires_at<=?",
                (now,),
            )
            if self.core.affect.snapshot()["state"]["engagement"] == "pause":
                return False
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
                "SELECT c.*,e.body_json FROM social_candidates c JOIN events e USING(event_id) "
                "WHERE c.state='CANDIDATE' AND e.redacted=0 ORDER BY c.created_at LIMIT 1"
            )
            if not candidate:
                return False
            body = json.loads(candidate["body_json"])
            source = s.one("SELECT source FROM events WHERE event_id=?", (candidate["event_id"],))
            label = "视频简介" if source["source"] == "observation.bilibili" else "帖子正文"
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
