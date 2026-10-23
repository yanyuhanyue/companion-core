"""Short-lived interaction continuity from explicit owner feedback, without model polling."""

import json
import re
from contextlib import nullcontext

from companion.store import after, encode

NEUTRAL = {
    "interaction_tone": "neutral",
    "engagement": "neutral",
    "cognitive_load": "normal",
    "novelty_interest": "unknown",
}
RULES = (
    (
        r"(?:请)?(?:回答)?(?:简短一点|简洁一点|说短一点|更简短)",
        "interaction_tone",
        "concise",
        "OWNER_REQUESTED_CONCISE",
    ),
    (
        r"(?:请)?(?:详细解释|展开说说|说详细一点|更详细)",
        "interaction_tone",
        "detailed",
        "OWNER_REQUESTED_DETAIL",
    ),
    (
        r"(?:请)?(?:分步骤说|一步一步来|先讲一步)",
        "cognitive_load",
        "stepwise",
        "OWNER_REQUESTED_STEPS",
    ),
    (
        r"(?:先不聊了|先别聊这个|暂停这个话题|目前不想聊)",
        "engagement", "pause", "OWNER_REQUESTED_PAUSE",
    ),
    (
        r"(?:继续聊|继续这个话题|可以继续|欢迎继续)",
        "engagement", "continue", "OWNER_REQUESTED_CONTINUE",
    ),
    (r"(?:想试试新的话题|换个新话题吧)", "novelty_interest", "open", "OWNER_REQUESTED_NOVELTY"),
)


def owner_feedback(store, settings, event_id, now):
    """Revalidate original, retained owner wording; no summaries or inferred mood."""
    event = store.one(
        "SELECT e.body_json,e.ingested_at,e.occurred_at FROM events e "
        "JOIN conversations c USING(conversation_id) WHERE e.event_id=? AND e.redacted=0 "
        "AND e.actor_id='owner' AND e.source='qq.onebot' AND e.privacy_scope='private' "
        "AND e.event_type='message.received' AND c.scope='private' "
        "AND c.owner_actor_id='owner' AND length(e.body_json)<=16000",
        (event_id,),
    )
    if not event or not (
        after(now, -settings.raw_event_retention_days * 86400) < event["ingested_at"] <= now
        and event["occurred_at"] <= now
    ):
        return None
    try:
        body = json.loads(event["body_json"])
    except ValueError:
        return None
    if not isinstance(body, dict) or body.get("diary_command") or body.get("control_command"):
        return None
    text = body.get("text", "")
    if not isinstance(text, str):
        return None
    text = text.strip().rstrip("。！!？? ")
    matched = next((rule for rule in RULES if re.fullmatch(rule[0], text)), None)
    if not matched:
        return None
    _, dimension, value, cause = matched
    return {
        "event_id": event_id,
        "dimension": dimension,
        "new_state": value,
        "reason_code": cause,
        "created_at": event["ingested_at"],
        "occurred_at": event["occurred_at"],
    }


def erase_affect(store, event_ids):
    if not store.one("SELECT 1 FROM sqlite_master WHERE name='affect_evidence' AND type='table'"):
        return
    changed = 0
    for event_id in event_ids:
        changed += store.execute(
            "DELETE FROM affect_evidence WHERE event_id=?", (event_id,)
        ).rowcount
    if changed:
        store.execute("DELETE FROM affect_states WHERE identity_id='companion'")


class AffectEngine:
    def __init__(self, store, settings, clock):
        self.store, self.settings, self.clock = store, settings, clock

    def snapshot(self):
        result = {"state": dict(NEUTRAL), "confidence": 0.0, "evidence": [], "expires_at": None}
        if not self.settings.affect_enabled:
            return result
        rows = self.store.rows(
            "SELECT a.* FROM affect_evidence a JOIN events e USING(event_id) "
            "WHERE identity_id='companion' AND e.redacted=0 AND a.expires_at>? "
            "AND a.created_at<=? ORDER BY a.rowid DESC LIMIT ?",
            (self.clock(), self.clock(), self.settings.affect_max_evidence),
        )
        seen = set()
        valid = []
        for row in rows:
            proposed = self.preview(row["event_id"])
            if proposed is None or any(
                row[key] != proposed[key]
                for key in ("dimension", "new_state", "reason_code", "expires_at")
            ):
                continue
            if row["dimension"] in seen:
                continue
            valid.append(row)
            seen.add(row["dimension"])
            result["state"][row["dimension"]] = row["new_state"]
            result["evidence"].append(
                {k: row[k] for k in ("event_id", "dimension", "reason_code", "expires_at")}
            )
        if valid:
            result["confidence"] = 1.0
            result["expires_at"] = min(row["expires_at"] for row in valid)
        return result

    def preview(self, event_id):
        """Read-only shadow: direct private owner feedback, never inferred emotion."""
        feedback = owner_feedback(self.store, self.settings, event_id, self.clock())
        if feedback is None:
            return None
        expires = min(
            after(feedback["created_at"], self.settings.affect_decay_minutes * 60),
            after(feedback["created_at"], self.settings.raw_event_retention_days * 86400),
        )
        if not feedback["created_at"] <= self.clock() < expires:
            return None
        return {
            "namespace": "owner.feedback",
            "mode": "SHADOW",
            "event_id": event_id,
            **{key: feedback[key] for key in (
                "dimension", "new_state", "reason_code", "created_at",
            )},
            "expires_at": expires,
        }

    def observe(self, event_id):
        if not self.settings.affect_enabled:
            return False
        s = self.store
        with nullcontext() if s.db.in_transaction else s.transaction():
            proposal = self.preview(event_id)
            if proposal is None:
                return False
            dimension, value, cause, expires = (
                proposal[key] for key in ("dimension", "new_state", "reason_code", "expires_at")
            )
            prior = self.snapshot()["state"][dimension]
            changed = s.execute(
                "INSERT OR IGNORE INTO affect_evidence VALUES ('companion',?,?,?,?,?,?,?,?)",
                (event_id, dimension, prior, value, cause, 1.0, proposal["created_at"], expires),
            ).rowcount
            if not changed:
                return False
            s.execute(
                "DELETE FROM affect_evidence WHERE identity_id='companion' AND rowid NOT IN "
                "(SELECT rowid FROM affect_evidence WHERE identity_id='companion' "
                "ORDER BY rowid DESC LIMIT ?)",
                (self.settings.affect_max_evidence,),
            )
            current = self.snapshot()
            s.execute(
                "INSERT INTO affect_states VALUES ('companion',?,?,?,?,1) "
                "ON CONFLICT(identity_id) DO UPDATE SET state_json=excluded.state_json,"
                "confidence=excluded.confidence,updated_at=excluded.updated_at,"
                "expires_at=excluded.expires_at,version=version+1",
                (
                    encode(current["state"]),
                    current["confidence"],
                    self.clock(),
                    current["expires_at"],
                ),
            )
            s.audit("affect.observed", event_id, event_id, self.clock(), reason=cause)
            return True
