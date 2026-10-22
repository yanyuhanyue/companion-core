import hashlib
import json

from companion.contracts import CoreError
from companion.store import after, encode, uid


def digest(value):
    return hashlib.sha256(encode(value).encode()).hexdigest()


def enqueue(store, event_id, conversation_id, text, now, action="qq.private.reply", key=None):
    if action not in {"qq.private.reply", "qq.private.proactive"}:
        raise CoreError("CAPABILITY_DISABLED", 403)
    content = {"text": text}
    key = key or f"reply:{event_id}:v1"
    previous = store.one("SELECT message_id FROM outbox WHERE delivery_key=?", (key,))
    if previous:
        return previous["message_id"]
    message_id = uid("msg")
    store.insert(
        "outbox",
        message_id=message_id,
        origin_event_id=event_id,
        conversation_id=conversation_id,
        action_type=action,
        content_json=encode(content),
        content_sha256=digest(content),
        state="PENDING",
        delivery_key=key,
        created_at=now,
        updated_at=now,
    )
    return message_id


def recover_deliveries(store, now):
    store.execute(
        "UPDATE outbox SET state='PENDING',lease_token=NULL,lease_until=NULL "
        "WHERE state='LEASED' AND lease_until<=?",
        (now,),
    )
    store.execute(
        "UPDATE outbox SET state='DELIVERY_UNCERTAIN',updated_at=?,"
        "last_error_code='SEND_LEASE_EXPIRED' WHERE state='SENDING' AND lease_until<=?",
        (now, now),
    )


def cancel_proactive_work(store, now):
    # Caller holds the write transaction. Started I/O keeps its uncertain accounting.
    store.execute(
        "UPDATE model_usage SET state='UNKNOWN' WHERE state='RESERVED' AND job_id IN "
        "(SELECT job_id FROM jobs WHERE kind='activity' AND state='RUNNING')"
    )
    store.execute(
        "UPDATE jobs SET state='CANCELLED',lease_token=NULL,updated_at=?,"
        "last_error_code='AUTONOMY_PAUSED' WHERE kind='activity' "
        "AND state IN ('QUEUED','RUNNING','RETRY_WAIT')",
        (now,),
    )
    store.execute(
        "UPDATE outbox SET state='CANCELLED',lease_token=NULL,updated_at=?,"
        "last_error_code='AUTONOMY_PAUSED' WHERE action_type='qq.private.proactive' "
        "AND state IN ('PENDING','LEASED','RETRY_WAIT')",
        (now,),
    )


def lease_deliveries(store, settings, request, now):
    from companion.social import proactive_allowed, share_delivery_problem

    with store.transaction():
        recover_deliveries(store, now)
        if not proactive_allowed(store, settings, now):
            cancel_proactive_work(store, now)
        # One delivery at a time per conversation. Uncertain items are never replayed;
        # later ordinary replies can continue so the owner can reconcile over QQ.
        rows = store.rows(
            "SELECT o.*,c.platform_session_key FROM outbox o JOIN conversations c "
            "ON c.conversation_id=o.conversation_id WHERE o.state IN ('PENDING','RETRY_WAIT') "
            "AND (o.next_attempt_at IS NULL OR o.next_attempt_at<=?) AND c.scope='private' "
            "AND c.owner_actor_id='owner' AND o.action_type IN "
            "('qq.private.reply','qq.private.proactive') AND NOT EXISTS (SELECT 1 FROM outbox b "
            "WHERE b.conversation_id=o.conversation_id AND b.rowid<o.rowid AND b.state IN "
            "('PENDING','RETRY_WAIT','LEASED','SENDING')) "
            "ORDER BY o.rowid LIMIT ?",
            (now, request.limit),
        )
        items = []
        for row in rows:
            problem = share_delivery_problem(store, settings, row["message_id"], now)
            if problem:
                store.execute(
                    "UPDATE outbox SET state='CANCELLED',lease_token=NULL,updated_at=?,"
                    "last_error_code=? WHERE message_id=?",
                    (now, problem, row["message_id"]),
                )
                continue
            token = uid("lease")
            store.execute(
                "UPDATE outbox SET state='LEASED',lease_owner=?,lease_token=?,"
                "lease_until=?,updated_at=? WHERE message_id=?",
                (
                    request.worker_id,
                    token,
                    after(now, settings.lease_seconds),
                    now,
                    row["message_id"],
                ),
            )
            items.append(
                {
                    "message_id": row["message_id"],
                    "conversation_platform_session_key": row["platform_session_key"],
                    "content": json.loads(row["content_json"]),
                    "lease_token": token,
                    "lease_until": after(now, settings.lease_seconds),
                    "delivery_key": row["delivery_key"],
                    "action_type": row["action_type"],
                }
            )
        return {"items": items}


def begin_send(store, message_id, token, now, settings=None, *, clock=None):
    problem = None
    with store.transaction():
        from companion import live_text

        if settings is not None and live_text.active(store, settings):
            live_text.check_delivery(store, settings, clock, message_id)
            now = clock()
        pending = store.one(
            "SELECT action_type FROM outbox WHERE message_id=? AND state='LEASED' "
            "AND lease_token=? AND lease_until>?",
            (message_id, token, now),
        )
        if pending:
            from companion.social import proactive_allowed, share_delivery_problem

            if pending["action_type"] == "qq.private.proactive" and not proactive_allowed(
                store, settings, now
            ):
                raise CoreError("PROACTIVE_SEND_PAUSED", 409)
            problem = share_delivery_problem(store, settings, message_id, now)
        if problem:
            store.execute(
                "UPDATE outbox SET state='CANCELLED',lease_token=NULL,updated_at=?,"
                "last_error_code=? WHERE message_id=?", (now, problem, message_id)
            )
        else:
            changed = store.execute(
                "UPDATE outbox SET state='SENDING',attempt_count=attempt_count+1,updated_at=? "
                "WHERE message_id=? AND state='LEASED' AND lease_token=? AND lease_until>?",
                (now, message_id, token, now),
            ).rowcount
            if changed != 1:
                raise CoreError("STALE_DELIVERY_LEASE")
    if problem:
        raise CoreError(problem, 409)
    return {"state": "SENDING"}


def delivery_result(store, settings, message_id, result, now):
    with store.transaction():
        row = store.one(
            "SELECT * FROM outbox WHERE message_id=? AND lease_token=?",
            (message_id, result.lease_token),
        )
        if not row:
            raise CoreError("STALE_DELIVERY_LEASE")
        if row["state"] == "SENT" and result.result == "SENT":
            return {"state": "SENT"}
        if row["state"] not in {"LEASED", "SENDING", "DELIVERY_UNCERTAIN"}:
            raise CoreError("INVALID_DELIVERY_TRANSITION")
        if result.result == "SENT" and row["state"] == "LEASED":
            raise CoreError("SEND_NOT_STARTED")
        if result.result == "RETRYABLE_FAILURE":
            if row["state"] != "LEASED":
                # Once I/O starts, a generic failure cannot establish non-delivery.
                state = "DELIVERY_UNCERTAIN"
            else:
                state = "RETRY_WAIT" if row["attempt_count"] < settings.max_attempts else "FAILED"
        else:
            state = {
                "SENT": "SENT",
                "DELIVERY_UNCERTAIN": "DELIVERY_UNCERTAIN",
                "PERMANENT_FAILURE": "FAILED",
            }[result.result]
        store.execute(
            "UPDATE outbox SET state=?,provider_message_id=?,last_error_code=?,"
            "sent_at=?,updated_at=?,next_attempt_at=?,attempt_count=attempt_count+? "
            "WHERE message_id=?",
            (
                state,
                result.provider_message_id,
                result.error_code,
                now if state == "SENT" else None,
                now,
                after(now, 5),
                int(row["state"] == "LEASED"),
                message_id,
            ),
        )
        store.audit("delivery.result", message_id, "delivery", now, state=state)
        from companion.goals import acknowledge_commitment_delivery

        acknowledge_commitment_delivery(store, message_id, state, now)
        return {"state": state}


def reconcile_delivery(store, message_id, outcome, now):
    if outcome not in {"SENT", "CANCELLED"}:
        raise CoreError("INVALID_RECONCILIATION", 400)
    with store.transaction():
        changed = store.execute(
            "UPDATE outbox SET state=?,updated_at=?,sent_at=?,lease_token=NULL "
            "WHERE message_id=? AND state='DELIVERY_UNCERTAIN' AND action_type IN "
            "('qq.private.reply','qq.private.proactive')",
            (outcome, now, now if outcome == "SENT" else None, message_id),
        ).rowcount
        if changed != 1:
            raise CoreError("NOT_UNCERTAIN")
        store.audit("delivery.owner_reconciled", message_id, "owner", now, outcome=outcome)
        from companion.goals import acknowledge_commitment_delivery

        acknowledge_commitment_delivery(store, message_id, outcome, now)
        return {"state": outcome}
