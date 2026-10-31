"""Owner interaction signals judged by the existing memory_extract call, kept for mood.

The model only picks a closed reason and quotes the owner's words; code fixes the PAD
delta, checks the quote against the retained original message and records nothing while
mood is off. Rows are derived data: native erasure removes them with their source event,
and the mood projection re-checks lineage, quote and model settlement on every read.
"""

import json
from datetime import datetime
from zoneinfo import ZoneInfo

from companion.paid_budget import sha
from companion.store import after

# P/A/D deltas. Her reaction to the owner's words, never a copy of the owner's feeling:
# distress raises attention, not sadness; hurt makes her want to make up, not sulk.
REASONS = {
    "OWNER_AFFECTION": (0.05, 0.02, 0.01),
    "OWNER_HURT_BY_REPLY": (-0.04, 0.03, -0.03),
    "OWNER_DISTRESS": (0.0, 0.03, 0.0),
    "OWNER_EXPECTATION": (0.0, 0.025, 0.0),
    "OWNER_PLAYFUL": (0.03, 0.02, 0.0),
}
DAILY_CAP = 2
HURT_REPLY_WINDOW = 3600


def quote_digest(quote):
    return sha(quote.strip())


def owner_text(store, event_id):
    """The retained private owner wording, or None for anything that is not a plain message."""
    event = store.one(
        "SELECT body_json FROM events WHERE event_id=? AND redacted=0 AND actor_id='owner' "
        "AND source='qq.onebot' AND privacy_scope='private' AND event_type='message.received' "
        "AND length(body_json)<=16000",
        (event_id,),
    )
    if not event:
        return None
    body = json.loads(event["body_json"])
    if (
        not isinstance(body, dict)
        or body.get("control_command")
        or body.get("diary_command")
        or body.get("quote")
        or body.get("rich")
        or body.get("shared_content")
    ):
        return None
    text = body.get("text", "")
    return text if isinstance(text, str) and text.strip() else None


def replied_before(store, event_id):
    """Hurt needs an actual delivered reply shortly before the owner's message."""
    event = store.one(
        "SELECT conversation_id,occurred_at FROM events WHERE event_id=?", (event_id,)
    )
    return bool(
        event
        and store.one(
            "SELECT 1 FROM outbox WHERE conversation_id=? AND state='SENT' "
            "AND action_type IN ('qq.private.reply','qq.private.proactive') "
            "AND sent_at<=? AND sent_at>? LIMIT 1",
            (
                event["conversation_id"],
                event["occurred_at"],
                after(event["occurred_at"], -HURT_REPLY_WINDOW),
            ),
        )
    )


def validate(store, signals, event_ids):
    """Keep at most one signal per owner message whose quote is really in that message."""
    kept, seen = [], set()
    for signal in signals:
        if signal.event_id in seen or signal.event_id not in event_ids:
            continue
        text, quote = owner_text(store, signal.event_id), signal.quote.strip()
        start = text.find(quote) if text is not None and len(quote) >= 2 else -1
        if start < 0:
            continue
        if signal.reason == "OWNER_HURT_BY_REPLY" and not replied_before(store, signal.event_id):
            continue
        seen.add(signal.event_id)
        kept.append(
            {
                "event_id": signal.event_id,
                "reason": signal.reason,
                "quote_start": start,
                "quote_length": len(quote),
                "quote_sha256": quote_digest(quote),
            }
        )
    return kept


def record(store, settings, signals, request_id, now):
    """Inside the memory commit transaction; nothing is kept while mood is off."""
    if not store.db.in_transaction:
        raise RuntimeError("MOOD_APPRAISAL_REQUIRES_TRANSACTION")
    if not settings.mood_enabled:
        return
    for item in signals:
        store.execute(
            "INSERT OR IGNORE INTO mood_appraisals VALUES (?,?,?,?,?,?,?)",
            (
                item["event_id"],
                item["reason"],
                item["quote_start"],
                item["quote_length"],
                item["quote_sha256"],
                request_id,
                now,
            ),
        )


def erase(store, event_ids):
    """Native erasure/replay cleanup: appraisals die with their source message."""
    if not store.one("SELECT 1 FROM sqlite_master WHERE type='table' AND name='mood_appraisals'"):
        return
    store.execute(
        "DELETE FROM mood_appraisals WHERE event_id IN (SELECT value FROM json_each(?))",
        (json.dumps(sorted(event_ids)),),
    )


def still_valid(store, row):
    """Re-check on every projection read; a later edit, redaction or unsettled call drops it."""
    text = owner_text(store, row["event_id"])
    usage = store.one(
        "SELECT state,scenario FROM model_usage WHERE request_id=?", (row["request_id"],)
    )
    if (
        text is None
        or not usage
        or usage["state"] != "SETTLED"
        or usage["scenario"] != "memory_extract"
        or row["reason"] not in REASONS
    ):
        return False
    quote = text[row["quote_start"] : row["quote_start"] + row["quote_length"]]
    return len(quote) == row["quote_length"] and quote_digest(quote) == row["quote_sha256"]


def capped(rows, timezone):
    """At most DAILY_CAP signals per reason per local day, oldest first, deterministically."""
    zone, counts, kept = ZoneInfo(timezone), {}, []
    for row in sorted(rows, key=lambda item: (item["created_at"], item["event_id"])):
        day = datetime.fromisoformat(row["created_at"]).astimezone(zone).date().isoformat()
        key = (day, row["reason"])
        if counts.get(key, 0) < DAILY_CAP:
            counts[key] = counts.get(key, 0) + 1
            kept.append(row)
    return kept
