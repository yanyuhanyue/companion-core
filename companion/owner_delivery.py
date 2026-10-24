"""Current owner permission over native Outbox records and their original task evidence."""

import json
from datetime import datetime

from companion import cumulative, notices, owner_authority, owner_input, paid_budget, reply_revision
from companion.contracts import CoreError
from companion.privacy import epoch
from companion.private_life import event_digest
from companion.store import encode

DELIVERY = "owner_delivery:"


def _job(store, job_id):
    return store.one("SELECT j.*,e.conversation_id,e.body_json FROM jobs j "
                     "JOIN events e USING(event_id) WHERE job_id=?", (job_id,))


def _history_proof(store, job):
    if job["kind"] != "chat" or reply_revision.control_job(store, job):
        return []
    row = store.one("SELECT phase,snapshots_json FROM agent_conversations WHERE job_id=?",
                     (job["job_id"],))
    try:
        snapshots = json.loads(row["snapshots_json"]) if row else []
        history = [item["proof"] for item in snapshots if item["kind"] == "history"]
        if not row or row["phase"] not in {"OUTPUT", "COMPLETE"} or len(history) != 1:
            raise ValueError("original history snapshot required")
        return history[0]
    except (ValueError, KeyError, TypeError) as error:
        raise CoreError(reply_revision.CANCELLED, 409) from error


def _sources(store, settings, clock, job, *, executing=False):
    if job is None:
        raise CoreError("OWNER_DELIVERY_JOB_REQUIRED", 403)
    view = owner_authority.check_job(store, settings, clock, job,
        purpose="poke" if job["kind"] == "poke" else owner_authority.model_purpose(
            store, job["kind"], job["job_id"]),
        executing=executing)
    reply_revision.check_job(store, job)
    if job["kind"] == "chat":
        owner_input.source(store, settings, view, job["event_id"], clock(), allow_commands=True)
        owner_input.check_history(store, settings, clock, job, _history_proof(store, job))
    elif job["kind"] == "activity":
        notices.validate(store, settings, job, clock())
    elif job["kind"] == "poke":
        from companion import poke

        poke.check_job(store, settings, view, job, clock=clock)
    else:
        raise CoreError("OWNER_DELIVERY_JOB_REQUIRED", 403)
    return view


def _usage(store, job, view):
    rows = store.rows("SELECT m.request_id,m.state,m.scenario,a.authority_json "
                      "FROM model_usage m LEFT JOIN model_admissions a USING(request_id) "
                      "WHERE m.job_id=? ORDER BY m.rowid", (job["job_id"],))
    if (any(row["state"] != "SETTLED" or not row["authority_json"]
            or json.loads(row["authority_json"]) != view.authority.as_dict() for row in rows)
        or not reply_revision.control_job(store, job)
        and not any(row["scenario"] == ("qq_poke" if job["kind"] == "poke" else job["kind"])
                    for row in rows)):
        raise CoreError("OWNER_DELIVERY_SETTLED_MODEL_REQUIRED", 403)
    return [row["request_id"] for row in rows]


def _material(store, row, job, view, requests):
    event = store.one("SELECT * FROM events WHERE event_id=?", (row["origin_event_id"],))
    if (not event or row["origin_event_id"] != job["event_id"]
        or row["conversation_id"] != job["conversation_id"]
        or row["action_type"] not in ({"qq.private.reply", "qq.private.poke"}
            if job["kind"] == "poke" else {"qq.private.reply", "qq.private.proactive"})
        or row["content_sha256"] != paid_budget.sha(json.loads(row["content_json"]))):
        raise CoreError("OWNER_DELIVERY_SOURCE_CHANGED", 403)
    return {"authority": view.authority.as_dict(), "job_id": job["job_id"],
            "privacy_epoch": epoch(store),
            "message_id": row["message_id"], "event_id": row["origin_event_id"],
            "event_sha256": event_digest(event), "content_sha256": row["content_sha256"],
            "delivery_key": row["delivery_key"], "action_type": row["action_type"],
            "history_sha256": paid_budget.sha(_history_proof(store, job)),
            "request_ids": requests}


def record_replies(store, settings, clock, job, message_ids):
    """Pin replies created by the still-running native model job."""
    if job["kind"] not in {"chat", "activity"}:
        raise CoreError("OWNER_DELIVERY_JOB_REQUIRED", 403)
    return _record_replies(store, settings, clock, job, message_ids, executing=True)


def record_poke_reply(store, settings, clock, job, message_ids):
    """The settled native interaction pins its decision before its running job completes."""
    if job["kind"] != "poke" or len(message_ids) != 1:
        raise CoreError("PRIVATE_POKE_DELIVERY_REQUIRED", 403)
    return _record_replies(store, settings, clock, job, message_ids, executing=True)


def record_life_draft(store, settings, clock, job, message_ids):
    """Consume settled Life wording under its independent current private-send purpose."""
    original = _job(store, job["job_id"])
    if (not original or original["state"] != "SUCCEEDED" or not notices.shadow(original)):
        raise CoreError("LIFE_SETTLED_DRAFT_REQUIRED", 409)
    # notices.validate uses LifeTopics.delivery_material for completed Life jobs:
    # source/wording integrity, current life+proactive and conversation revision.
    return _record_replies(store, settings, clock, original, message_ids, executing=False)


def _record_replies(store, settings, clock, job, message_ids, *, executing):
    """Pin each native Outbox item in the transaction that creates its complete group."""
    from companion.transport import reply_group, segment_envelopes

    if not store.db.in_transaction:
        raise RuntimeError("owner delivery recording requires the enqueue transaction")
    if not message_ids:
        return
    view = _sources(store, settings, clock, job, executing=executing)
    requests = _usage(store, job, view)
    first = store.one("SELECT action_type FROM outbox WHERE message_id=?", (message_ids[0],))
    group = None if first and first["action_type"] == "qq.private.poke" else reply_group(
        store, message_ids[0])
    if group:
        envelopes = segment_envelopes(group)
        if (len(group["text"]) > view.policy.max_reply_chars
            or [row["message_id"] for row in group["rows"]] != message_ids):
            raise CoreError("OWNER_DELIVERY_GROUP_CHANGED", 403)
        policy = settings.segmented_reply
        if group["segmented"] and (policy is None
            or policy.max_segments != "unlimited" and len(message_ids) > policy.max_segments
            or any(item["interval_ms"] != policy.interval_ms for item in envelopes.values())):
            raise CoreError("OWNER_DELIVERY_GROUP_CHANGED", 403)
    for message_id in message_ids:
        row = store.one("SELECT * FROM outbox WHERE message_id=?", (message_id,))
        if row is None or row["state"] != "PENDING" or json.loads(row["authority_json"]):
            raise CoreError("OWNER_DELIVERY_ADMISSION_REQUIRED", 403)
        material = _material(store, row, job, view, requests)
        if job["kind"] == "poke":
            from companion import poke

            poke.check_delivery(store, settings, view, message_id, clock=clock)
        store.execute("UPDATE outbox SET authority_json=? WHERE message_id=?",
                      (encode(view.authority.as_dict()), message_id))
        store.insert("runtime_meta", key=DELIVERY + message_id, value=encode(material))


def check_delivery(store, settings, clock, message_id, *, check=None):
    from companion.social import proactive_allowed
    from companion.transport import check_segment_ready

    row = store.one("SELECT o.*,c.scope,c.owner_actor_id FROM outbox o "
                    "JOIN conversations c USING(conversation_id) WHERE message_id=?", (message_id,))
    saved = store.one("SELECT value FROM runtime_meta WHERE key=?", (DELIVERY + message_id,))
    if not row or not saved or (row["scope"], row["owner_actor_id"]) != ("private", "owner"):
        raise CoreError("OWNER_DELIVERY_BINDING_REQUIRED", 403)
    material = json.loads(saved["value"])
    job = _job(store, material["job_id"])
    view = _sources(store, settings, clock, job)
    if (job["state"] != "SUCCEEDED"
        or json.loads(row["authority_json"]) != view.authority.as_dict()
        or material != _material(store, row, job, view, _usage(store, job, view))):
        raise CoreError("OWNER_DELIVERY_SOURCE_CHANGED", 403)
    reply_revision.check_delivery(store, message_id)
    if job["kind"] == "poke":
        from companion import poke

        poke.check_delivery(store, settings, view, message_id, clock=clock)
    if problem := notices.delivery_problem(store, settings, message_id, clock()):
        raise CoreError(problem, 403)
    if row["action_type"] == "qq.private.proactive" and not proactive_allowed(
        store, settings, clock(),
    ):
        raise CoreError("PROACTIVE_SEND_PAUSED", 409)
    if row["reply_group_id"]:
        check_segment_ready(store, message_id, clock())
    if check is not None:
        if (check.authority != view.authority.as_dict()
            or check.event_id != row["origin_event_id"]
            or check.content_sha256 != row["content_sha256"]
            or check.lease_token != row["lease_token"] or row["state"] != "SENDING"
            or not row["lease_until"] or row["lease_until"] <= clock()):
            raise CoreError("OWNER_DELIVERY_LEASE_LOST", 409)
        if reply_revision.owner_input(store, row["origin_event_id"]):
            if check.reply_revision is None:
                raise CoreError("OWNER_DELIVERY_REVISION_REQUIRED", 403)
            reply_revision.require_reply_source(store, check.event_id, check.reply_revision)
    deadlines = [datetime.fromisoformat(row["lease_until"])] if row["lease_until"] else []
    if view.policy.expires_at:
        deadlines.append(view.policy.expires_at)
    return {"approved": True, **({"lease_until": min(deadlines).isoformat()} if deadlines else {})}


def send_check(store, settings, clock, body):
    from companion.voice_jobs import REPLY_RECEIPT

    with store.transaction():
        result = check_delivery(store, settings, clock, body.message_id, check=body)
        content = json.loads(store.one("SELECT content_json FROM outbox WHERE message_id=?",
                                        (body.message_id,))["content_json"])
        if content.get("image") is not None:
            owner_authority.current(store, settings, clock,
                                     purpose="meme", authority=body.authority)
        if body.voice is not None or store.one(
            "SELECT 1 FROM runtime_meta WHERE key=? AND json_extract(value,'$.state')='AUDIO'",
            (REPLY_RECEIPT + body.message_id,),
        ):
            from companion.voice_flow import VoiceFlow

            owner_authority.current(store, settings, clock,
                                    purpose="voice", authority=body.authority)
            VoiceFlow(store, settings, clock).validate_send(
                body.message_id, body.lease_token, body.voice)
        cumulative.admit_operation(store, settings, clock(), "send", body.message_id,
            paid_budget.sha(body.model_dump(mode="json")), logical=body.event_id)
        return result
