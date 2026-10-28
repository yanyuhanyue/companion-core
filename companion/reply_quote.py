"""Native QQ quote targets for owner-authority replies.

The model may only name a local alias of an owner message it was shown. Core maps the
alias back to an input it recorded itself and decides whether a quote box is useful;
an unknown, erased, recalled or out-of-scope target drops the quote, never the reply.
"""

import hashlib
import re

from companion import input_recall, reply_revision

CHOICE = re.compile(r"【引用\s*(m[0-9a-f]{4})】")
MESSAGE_ID = re.compile(r"-?[1-9][0-9]{0,19}")
RULES = (
    "\n主人消息开头的[编号 m加四位字符]是本地编号，不是主人原话。想明确回应主人较早的某一句时，"
    "可以在回复最前面写【引用 编号】，例如【引用 m1a2b】；回应的就是最新一条时不用写。"
    "不要编造编号，也不要在其他地方使用这个格式。"
)


def alias(event_id):
    return "m" + hashlib.sha256(event_id.encode()).hexdigest()[:4]


def label(message, event_id):
    """Prefix one owner message with its alias without changing its source binding."""
    tag = "[编号 " + alias(event_id) + "] "
    content = message["content"]
    if isinstance(content, str):
        return {**message, "content": tag + content}
    parts = list(content)
    for index, part in enumerate(parts):
        if part.get("type") == "text":
            parts[index] = {**part, "text": tag + part["text"]}
            break
    return {**message, "content": parts}


def label_turn(turn, source):
    """Label the owner's own text message in a rendered history turn."""
    for index, message in enumerate(turn):
        if message["role"] == "user" and message["content"] == source["user_text"]:
            return [*turn[:index], label(message, source["event_id"]), *turn[index + 1:]]
    return turn


def extract(reply):
    """Return the first chosen alias and the reply text without any choice markers."""
    found = CHOICE.search(reply)
    return (found.group(1) if found else None), CHOICE.sub("", reply).strip()


def target(store, settings, clock, job, chosen):
    """Resolve the provider message id to quote, or None.

    never: no quote. occasionally: quote an older owner message the model named, or the
    current input when it answers several coalesced inputs. often: also quote the
    current input when named. The id always comes from Core's own source record.
    """
    from companion import owner_input

    if settings.quote_policy == "never" or job["kind"] != "chat":
        return None
    current = store.one("SELECT * FROM events WHERE event_id=?", (job["event_id"],))
    if not current:
        return None
    sources = owner_input.history_sources(store, settings, clock, job)
    # Unanswered inputs right before this one were folded into this reply.
    coalesced = bool(sources) and sources[-1].get("reply_complete") is False and not (
        sources[-1]["reply_text"])
    if chosen is None:
        if not coalesced:
            return None
        row = current
    else:
        matches = [row for row in (*sources, current) if alias(row["event_id"]) == chosen]
        if len({row["event_id"] for row in matches}) != 1:
            return None
        row = matches[0]
        if (row["event_id"] == current["event_id"] and not coalesced
                and settings.quote_policy != "often"):
            return None
    if (row["conversation_id"] != job["conversation_id"] or row["actor_id"] != "owner"
            or row["source"] != "qq.onebot" or row["privacy_scope"] != "private"
            or not reply_revision.source_active(store, row["event_id"])
            or input_recall.is_recalled(store, row["source_event_id"])):
        return None
    message_id = row["source_event_id"].rpartition(":")[2]
    return message_id if MESSAGE_ID.fullmatch(message_id) else None
