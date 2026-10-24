"""Profile-purpose inputs from native owner decisions and complete Memory source proofs."""

import json
from datetime import datetime

from companion.commands import command_parts
from companion.contracts import CoreError
from companion.memory import Memory
from companion.memory_context import MEMORY_FIELDS, MemoryContext, MemorySources
from companion.privacy import ErasureLedger, redact, sensitive
from companion.store import encode

ERROR = "SOCIAL_PROFILE_SOURCE_REQUIRED"
FIELDS = {"nickname", "signature"}


def _deny():
    raise CoreError(ERROR, 403)


def _owner_lineage(memory, event_id, now):
    sources = MemorySources(memory, now)
    if not sources.event(event_id):
        _deny()
    rows = list(sources._lineage(event_id))
    if not rows or len(rows) > 256:
        _deny()
    for row in rows:
        if (row["actor_id"] != "owner" or (row["source"], row["event_type"]) not in {
            ("qq.onebot", "message.received"), ("owner.decision", "memory.corrected"),
        } or memory.store.one("SELECT 1 FROM observations WHERE event_id=?", (row["event_id"],))
            or memory.store.one("SELECT 1 FROM source_grant_events WHERE event_id=?",
                                 (row["event_id"],))):
            _deny()
        conversation = memory.store.one("SELECT * FROM conversations WHERE conversation_id=?",
                                         (row["conversation_id"],))
        if (not conversation or conversation["platform"] != "qq.onebot"
            or conversation["platform_session_key"] != (
                "onebot-direct:FriendMessage:" + memory.settings.owner_qq_id)):
            _deny()
    return rows


def _decision(memory, row, now):
    # Automatic CONFIRMED/user_confirmed labels are not an owner-decision receipt.
    for audit in memory.store.rows(
        "SELECT * FROM audit_log WHERE action='memory.owner_decision' AND entity_id IN (?,?) "
        "ORDER BY rowid DESC LIMIT 64", (row["memory_id"], row["supersedes_memory_id"] or ""),
    ):
        detail = json.loads(audit["safe_detail_json"])
        event_id, decision = detail.get("source_event_id"), detail.get("decision")
        if not isinstance(event_id, str) or decision not in {"confirm", "correct"}:
            continue
        event = memory.store.one("SELECT * FROM events WHERE event_id=?", (event_id,))
        if (not event or event["source"] != "qq.onebot" or event["trace_id"] != audit["trace_id"]
            or datetime.fromisoformat(audit["created_at"]) > datetime.fromisoformat(now)):
            continue
        command, args = command_parts(json.loads(event["body_json"]).get("text", ""))
        if decision == "confirm":
            valid = (audit["entity_id"] == row["memory_id"] and command == "确认这一条"
                     and args == row["memory_id"])
        else:
            valid = (audit["entity_id"] == row["supersedes_memory_id"] and command == "纠正这一条"
                     and args.split(maxsplit=1) == [row["supersedes_memory_id"], row["content"]]
                     and memory.store.one(
                         "SELECT 1 FROM memory_evidence v JOIN events e USING(event_id) "
                         "WHERE v.memory_id=? AND v.polarity=1 AND e.source='owner.decision' "
                         "AND e.event_type='memory.corrected' AND e.body_json=?",
                         (row["memory_id"], encode({"text": row["content"]})),
                     ))
        if valid:
            _owner_lineage(memory, event_id, now)
            return audit, event_id
    _deny()


def material(store, settings, now, memory_ids):
    """Reuse Memory pinning and erasure ancestry; no new authority or source ledger."""
    if not memory_ids or len(memory_ids) > settings.social_autonomy.max_source_refs:
        _deny()
    if len(set(memory_ids)) != len(memory_ids):
        _deny()
    memory = Memory(store, settings, ErasureLedger(settings.erasure_ledger))
    context, sources = MemoryContext(memory), MemorySources(memory, now)
    memory.require_owner("owner")
    result = []
    for memory_id in sorted(memory_ids):
        row = store.one("SELECT * FROM memories WHERE memory_id=?", (memory_id,))
        if (not row or row["status"] != "CONFIRMED" or row["needs_review"]
            or row["origin"] != "user_confirmed" or row["privacy_scope"] != "private"
            or row["confidence"] <= 0 or not sources.current(row)
            or not ((row["subject"] == "self" and row["kind"] in {"fact", "preference"})
                    or (row["subject"] == "user" and row["kind"] == "preference"))
            or sensitive(row["content"]) or redact(row["content"], settings) != row["content"]):
            _deny()
        evidence = store.rows("SELECT * FROM memory_evidence WHERE memory_id=? ORDER BY event_id",
                              (memory_id,))
        if not evidence or any(e["polarity"] != 1 for e in evidence):
            _deny()
        for event in evidence:
            _owner_lineage(memory, event["event_id"], now)
        audit, decision_event = _decision(memory, row, now)
        expected = {**{key: row[key] for key in MEMORY_FIELDS},
                    "evidence_ids": [event["event_id"] for event in evidence]}
        proof = context.pin(now, rows=[expected], event_ids=[decision_event])
        result.append({"memory_id": memory_id, "subject": row["subject"], "kind": row["kind"],
                       "content": row["content"], "owner_decision": audit, "proof": proof})
    return result


def candidates(store, settings, now):
    if not settings.memory_enabled:
        return []
    ids = []
    for row in store.rows(
        "SELECT memory_id FROM memories WHERE status='CONFIRMED' AND needs_review=0 "
        "AND ((subject='self' AND kind IN ('fact','preference')) "
        "OR (subject='user' AND kind='preference')) ORDER BY updated_at DESC,memory_id LIMIT 40"
    ):
        try:
            material(store, settings, now, [row["memory_id"]])
        except CoreError:
            continue
        ids.append(row["memory_id"])
        if len(ids) >= settings.social_autonomy.max_source_refs:
            break
    return ids
