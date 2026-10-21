"""Recent delivered conversation turns and their local soft context bound.

This byte-based bound is not a provider token count or paid admission. The budget
engine still checks the complete final prompt under its own admission contract.
"""

from companion.budget import token_upper_bound
from companion.qq_rich import context


def recent_sent_turns(store, conversation_id, current_event_id, max_turns):
    """Return oldest first, with one complete private SENT reply per owner input."""
    return [turn_messages(row) for row in recent_sent_sources(
        store, conversation_id, current_event_id, max_turns,
    )]


def turn_messages(source):
    import json

    rich = context(json.loads(source["body_json"])) if source.get("body_json") else None
    return [*([rich] if rich else []), {"role": "user", "content": source["user_text"]},
            {"role": "assistant", "content": source["reply_text"]}]


def recent_sent_sources(store, conversation_id, current_event_id, max_turns, *, event_ids=None):
    """Return source-bound turns; an optional small allowlist is applied before LIMIT."""
    if max_turns <= 0:
        return []
    if event_ids is not None and not event_ids:
        return []
    allowed = ""
    if event_ids is not None:
        allowed = "AND e.event_id IN (" + ",".join("?" for _ in event_ids) + ") "
    rows = store.rows(
        "SELECT e.*,o.message_id,o.content_json,o.content_sha256,o.delivery_key,"
        "json_extract(e.body_json,'$.text') AS user_text,"
        "json_extract(o.content_json,'$.text') AS reply_text FROM events e "
        "JOIN outbox o ON o.origin_event_id=e.event_id AND o.rowid IN "
        "(SELECT MAX(reply.rowid) FROM outbox reply WHERE reply.conversation_id=? "
        "AND reply.action_type='qq.private.reply' AND reply.state='SENT' "
        "AND json_type(reply.content_json,'$.text')='text' "
        "AND length(json_extract(reply.content_json,'$.text'))>0 GROUP BY reply.origin_event_id) "
        "WHERE e.conversation_id=? AND e.actor_id='owner' AND e.privacy_scope='private' "
        "AND e.source='qq.onebot' "
        "AND e.event_type IN ('message.received','message.media_received') "
        "AND e.redacted=0 AND e.rowid<(SELECT rowid FROM events WHERE event_id=?) "
        "AND json_type(e.body_json,'$.text')='text' "
        "AND (length(json_extract(e.body_json,'$.text'))>0 "
        "OR json_type(e.body_json,'$.rich')='object') "
        "AND COALESCE(json_extract(e.body_json,'$.diary_command'),0)=0 "
        "AND COALESCE(json_extract(e.body_json,'$.control_command'),0)=0 "
        "AND NOT EXISTS (SELECT 1 FROM memory_evidence me "
        "JOIN memories m ON m.memory_id=me.memory_id WHERE me.event_id=e.event_id "
        "AND m.status IN ('SUPERSEDED','FORGOTTEN')) " + allowed + "ORDER BY e.rowid DESC LIMIT ?",
        (conversation_id, conversation_id, current_event_id, *(event_ids or ()), max_turns),
    )
    return list(reversed(rows))


def fit_context(rules, references, turns, current_input, soft_limit):
    """Drop optional references, then oldest whole turns; preserve rules/current input.

    Current input also includes its bounded shared-content reference, when present.
    If those required parts exceed the soft bound, leave them complete for admission.
    """
    groups = [[message] for message in references] + turns
    messages = [rules, *(message for group in groups for message in group), *current_input]
    while groups and token_upper_bound(messages) > soft_limit:
        del messages[1 : 1 + len(groups.pop(0))]
    return messages
