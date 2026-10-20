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
        r"(?:请)?(?:回答)?(?:简短一点|简洁一点|说短一点)",
        "interaction_tone",
        "concise",
        "OWNER_REQUESTED_CONCISE",
    ),
    (
        r"(?:请)?(?:详细解释|展开说说|说详细一点)",
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
    (r"(?:先不聊了|先别聊这个|暂停这个话题)", "engagement", "pause", "OWNER_REQUESTED_PAUSE"),
    (r"(?:继续聊|继续这个话题|可以继续)", "engagement", "continue", "OWNER_REQUESTED_CONTINUE"),
    (r"(?:想试试新的话题|换个新话题吧)", "novelty_interest", "open", "OWNER_REQUESTED_NOVELTY"),
)


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
        for row in rows:
            if row["dimension"] in seen:
                continue
            seen.add(row["dimension"])
            result["state"][row["dimension"]] = row["new_state"]
            result["evidence"].append(
                {k: row[k] for k in ("event_id", "dimension", "reason_code", "expires_at")}
            )
        if rows:
            result["confidence"] = min(row["confidence"] for row in rows)
            result["expires_at"] = min(row["expires_at"] for row in rows)
        return result

    def observe(self, event_id):
        if not self.settings.affect_enabled:
            return False
        s = self.store
        with nullcontext() if s.db.in_transaction else s.transaction():
            event = s.one("SELECT * FROM events WHERE event_id=? AND redacted=0", (event_id,))
            if not event or event["actor_id"] != "owner" or event["source"] != "qq.onebot":
                return False
            body = json.loads(event["body_json"])
            if body.get("diary_command"):
                return False
            text = body.get("text", "").strip().rstrip("。！!？? ")
            matched = next((rule for rule in RULES if re.fullmatch(rule[0], text)), None)
            if not matched:
                return False
            _, dimension, value, cause = matched
            expires = after(event["ingested_at"], self.settings.affect_decay_minutes * 60)
            if expires <= self.clock():
                return False
            prior = self.snapshot()["state"][dimension]
            changed = s.execute(
                "INSERT OR IGNORE INTO affect_evidence VALUES ('companion',?,?,?,?,?,?,?,?)",
                (event_id, dimension, prior, value, cause, 1.0, event["ingested_at"], expires),
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
            s.audit("affect.observed", event_id, event["trace_id"], self.clock(), reason=cause)
            return True
