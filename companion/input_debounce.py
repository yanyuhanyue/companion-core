"""Owner input debounce: a burst of private messages is answered once.

The tiers follow MaiBot's "natural" debounce (src/chat/debounce/controller.py at
c6429547af711555e3d4d1b8d04b413572cb616c): a batch opens with a short wait when the chat
was quiet and a longer one when it was active, each further message extends it a little,
a bare image waits longer, and the whole batch is bounded. No model call is involved:
the new chat job only gets a later `next_attempt_at`, and the existing reply revision
already supersedes the earlier job and carries its unanswered input into the next context.
"""

from datetime import datetime

from pydantic import Field

from companion.contracts import StrictModel
from companion.store import after

BATCH_KEY = "input_debounce_batch:"


class InputDebounceConfig(StrictModel):
    enabled: bool = True
    quiet_seconds: float = Field(default=2.0, ge=0, le=10)
    active_seconds: float = Field(default=5.0, ge=0, le=15)
    continue_seconds: float = Field(default=2.0, ge=0, le=10)
    media_seconds: float = Field(default=4.0, ge=0, le=15)
    batch_seconds: float = Field(default=8.0, ge=0, le=30)
    active_window_seconds: float = Field(default=30.0, ge=0, le=600)


def eligible(body):
    """Commands answer at once; only ordinary chat text and media are batched."""
    return not (body.get("control_command") or body.get("diary_command")
                or (body.get("text") or "").lstrip().startswith("/"))


def due(store, settings, conversation_id, event_id, body, now):
    """When the new chat job may first be claimed, or None for no delay.

    Call inside the ingestion transaction after the event row exists and before the
    reply revision supersedes the earlier job, so a waiting batch is still visible.
    """
    config = settings.input_debounce
    if not config.enabled or not eligible(body):
        return None
    key = BATCH_KEY + conversation_id
    waiting = store.one(
        "SELECT 1 FROM jobs j JOIN events e USING(event_id) WHERE e.conversation_id=? "
        "AND e.event_id!=? AND j.kind='chat' AND j.state='QUEUED' AND j.attempts=0 "
        "AND j.next_attempt_at>?", (conversation_id, event_id, now))
    start = store.one("SELECT value FROM runtime_meta WHERE key=?", (key,))
    if waiting and start:
        wait, opened = config.continue_seconds, start["value"]
    else:
        since = after(now, -config.active_window_seconds)
        active = store.one(
            "SELECT 1 FROM events WHERE conversation_id=? AND event_id!=? AND actor_id='owner' "
            "AND ingested_at>=? LIMIT 1", (conversation_id, event_id, since),
        ) or store.one(
            "SELECT 1 FROM outbox WHERE conversation_id=? AND sent_at>=? LIMIT 1",
            (conversation_id, since),
        )
        wait = config.active_seconds if active else config.quiet_seconds
        opened = now
        store.execute("INSERT OR REPLACE INTO runtime_meta VALUES (?,?)", (key, now))
    if not (body.get("text") or "").strip():
        wait = max(wait, config.media_seconds)
    limit = after(opened, config.batch_seconds)
    result = min(after(now, wait), limit, key=datetime.fromisoformat)
    return result if datetime.fromisoformat(result) > datetime.fromisoformat(now) else None
