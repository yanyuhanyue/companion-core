"""Current owner permission over native Outbox records and their original task evidence."""

import json
from datetime import datetime

from companion import cumulative, notices, owner_authority, owner_input, paid_budget, reply_revision
from companion.contracts import CoreError
from companion.private_life import event_digest
from companion.store import encode

DELIVERY = "owner_delivery:"


def _job(store, job_id):
    return store.one("SELECT j.*,e.conversation_id,e.body_json FROM jobs j "
                     "JOIN events e USING(event_id) WHERE job_id=?", (job_id,))


def _sources(store, settings, clock, job, *, executing=False):
    if job is None:
        raise CoreError("OWNER_DELIVERY_JOB_REQUIRED", 403)
    view = owner_authority.check_job(store, settings, clock, job,
        purpose=owner_authority.model_purpose(store, job["kind"], job["job_id"]),
        executing=executing)
    reply_revision.check_job(store, job)
    if job["kind"] == "chat":
        owner_input.source(store, settings, view, job["event_id"], clock(), allow_commands=True)
    elif job["kind"] == "activity":
        notices.validate(store, settings, job, clock())
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
        and not any(row["scenario"] == job["kind"] for row in rows)):
        raise CoreError("OWNER_DELIVERY_SETTLED_MODEL_REQUIRED", 403)
    return [row["request_id"] for row in rows]


def _material(store, row, job, view, requests):
    event = store.one("SELECT * FROM events WHERE event_id=?", (row["origin_event_id"],))
    if (not event or row["origin_event_id"] != job["event_id"]
        or row["conversation_id"] != job["conversation_id"]
        or row["action_type"] not in {"qq.private.reply", "qq.private.proactive"}
        or row["content_sha256"] != paid_budget.sha(json.loads(row["content_json"]))):
        raise CoreError("OWNER_DELIVERY_SOURCE_CHANGED", 403)
    return {"authority": view.authority.as_dict(), "job_id": job["job_id"],
            "message_id": row["message_id"], "event_id": row["origin_event_id"],
            "event_sha256": event_digest(event), "content_sha256": row["content_sha256"],
            "delivery_key": row["delivery_key"], "action_type": row["action_type"],
            "request_ids": requests}


def record_replies(store, settings, clock, job, message_ids):
    """Pin each native Outbox item in the transaction that creates its complete group."""
    from companion.transport import reply_group, segment_envelopes

    if not store.db.in_transaction:
        raise RuntimeError("owner delivery recording requires the enqueue transaction")
    if not message_ids:
        return
    view = _sources(store, settings, clock, job, executing=True)
    requests = _usage(store, job, view)
    group = reply_group(store, message_ids[0])
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
