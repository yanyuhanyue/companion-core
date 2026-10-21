"""Private, content-free trace inspection; never authorizes or repairs runtime state."""

import hashlib
import json
import re
import sqlite3
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path

from companion.ops_status import _ReadView, schema_head
from companion.privacy import epoch


def _hash(value):
    return hashlib.sha256(str(value).encode()).hexdigest()


def _digest(value):
    return value if isinstance(value, str) and re.fullmatch(r"[a-f0-9]{64}", value) else None


def _number(value):
    return value if type(value) is int and 0 <= value < 2**63 else None


def _state(value, allowed):
    return value if value in allowed else "UNKNOWN"


def _context(view, job_id, current_epoch):
    row = view.one("SELECT value FROM runtime_meta WHERE key=?",
                   ("live_text_context:" + job_id,))
    if row is None:
        return {"state": "NOT_RECORDED", "source_validity": "NOT_REVALIDATED"}
    if len(row["value"]) > 131072:
        raise ValueError("CONTEXT_TOO_LARGE")
    value = json.loads(row["value"])
    epoch = _number(value.get("privacy_epoch"))
    sources, memories = value.get("sources", []), value.get("memory_sources", [])
    images = value.get("image_sources", [])
    if not isinstance(sources, list) or len(sources) > 10:
        raise ValueError("CONTEXT_SOURCES_INVALID")
    if not isinstance(memories, list) or len(memories) > 6:
        raise ValueError("CONTEXT_MEMORIES_INVALID")
    if not isinstance(images, list) or len(images) > 1:
        raise ValueError("CONTEXT_IMAGES_INVALID")
    return {
        "state": "RECORDED" if epoch is not None and epoch == current_epoch else "STALE_EPOCH",
        "source_validity": "NOT_REVALIDATED",
        "privacy_epoch": epoch,
        "plan_sha256": _digest(value.get("plan_sha256")),
        "payload_sha256": _digest(value.get("payload_sha256")),
        "history": [{"event_ref_sha256": _hash(source["event_id"]),
                     "producer": "qq.onebot", "subject": "owner_private_turn"}
                    for source in sources],
        "memory": [{"memory_ref_sha256": _hash(source["memory_id"]),
                    "subject": _state(source.get("subject"), {"user", "project"}),
                    "evidence_sha256": _digest(source.get("evidence_sha256"))}
                   for source in memories],
        "images": [{
            "producer": "observation.qq_image", "subject": "owner_shared_image",
            "source_validity": "NOT_REVALIDATED",
            "privacy_epoch": _number(source["image"].get("privacy_epoch")),
            "event_ref_sha256": _hash(source["image"]["event_id"]),
            "item_ref_sha256": _hash(source["image"]["item_id"]),
            "source_sha256": _digest(source["image"]["source"].get("source_sha256")),
            "media_sha256": _digest(source["image"].get("media_sha256")),
            "payload_sha256": _digest(source["image"].get("payload_sha256")),
            **{key: _digest(source.get(key)) for key in (
                "item_sha256", "observation_sha256", "admission_sha256",
            )},
        } for source in images],
    }


def _trace(view, trace_id):
    events = view.rows(
        "SELECT e.event_id,e.redacted FROM events e JOIN conversations c USING(conversation_id) "
        "WHERE e.trace_id=? AND e.actor_id='owner' AND e.privacy_scope='private' "
        "AND e.source='qq.onebot' AND c.scope='private' AND c.owner_actor_id='owner' LIMIT 2",
        (trace_id,),
    )
    if len(events) != 1:
        return {"state": "NOT_FOUND" if not events else "AMBIGUOUS_TRACE"}
    event = events[0]
    current_epoch = epoch(view)
    jobs = view.rows("SELECT job_id,state,attempts,last_error_code FROM jobs "
                     "WHERE event_id=? AND kind='chat' LIMIT 21", (event["event_id"],))
    if len(jobs) > 20:
        raise ValueError("TRACE_JOB_LIMIT")
    inspected = []
    for job in jobs:
        usage = view.rows(
            "SELECT m.state,m.scenario,m.input_tokens,m.cached_input_tokens,m.output_tokens,"
            "m.billed_micro_usd,m.pricing_known,a.io_started_at,a.attempt_number,"
            "a.reserved_micro_usd FROM model_usage m LEFT JOIN model_admissions a "
            "USING(request_id) WHERE m.job_id=? ORDER BY m.rowid LIMIT 101", (job["job_id"],),
        )
        if len(usage) > 100:
            raise ValueError("TRACE_USAGE_LIMIT")
        calls = []
        for row in usage:
            # RESERVED/UNKNOWN tokens may be estimates. Demo calls are never real usage proof.
            actual = row["state"] == "SETTLED" and row["io_started_at"] is not None
            calls.append({
                "state": _state(row["state"], {"RESERVED", "SETTLED", "UNKNOWN"}),
                "scenario": _state(row["scenario"], {"chat", "memory_review", "content_vision"}),
                "paid_dispatch_recorded": row["io_started_at"] is not None,
                "attempt_number": _number(row["attempt_number"]),
                "prompt_tokens": _number(row["input_tokens"]) if actual else None,
                "prompt_cache_hit_tokens": _number(row["cached_input_tokens"]) if actual else None,
                "completion_tokens": _number(row["output_tokens"]) if actual else None,
                "configured_price_micro_usd": (
                    _number(row["billed_micro_usd"]) if actual and row["pricing_known"] else None
                ),
                "accounted_or_reserved_micro_usd": _number(row["billed_micro_usd"]),
                "reservation_micro_usd": _number(row["reserved_micro_usd"]),
            })
        inspected.append({
            "job_ref_sha256": _hash(job["job_id"]),
            "state": _state(job["state"], {
                "QUEUED", "RUNNING", "RETRY_WAIT", "SUCCEEDED", "FAILED", "CANCELLED",
            }),
            "worker_attempts": _number(job["attempts"]),
            "failure_recorded": job["last_error_code"] is not None,
            "failure_code_sha256": (_hash(job["last_error_code"])
                                    if job["last_error_code"] else None),
            "context": ({"state": "REDACTED", "source_validity": "NOT_REVALIDATED"}
                        if event["redacted"] else _context(view, job["job_id"], current_epoch)),
            "model_attempt_count": len(calls), "model_calls": calls,
        })
    deliveries = view.rows(
        "SELECT state,COUNT(*) count FROM outbox WHERE origin_event_id=? "
        "AND action_type='qq.private.reply' GROUP BY state", (event["event_id"],),
    )
    states = {row["state"]: row["count"] for row in deliveries}
    return {"state": "REDACTED" if event["redacted"] else "RECORDED",
            "producer": "qq.onebot", "subject": "owner_private_input",
            "event_ref_sha256": _hash(event["event_id"]), "privacy_epoch": current_epoch,
            "jobs": inspected,
            "outbox": {state: states[state] for state in (
                "PENDING", "LEASED", "SENDING", "RETRY_WAIT", "SENT", "DELIVERY_UNCERTAIN",
                "FAILED", "CANCELLED",
            ) if state in states},
            "phone_received": None, "phone_e2e_ms": None, "provider_invoice_micro_usd": None}


def inspect_context(settings, trace_id):
    """Read one trace in one bounded transaction. Local config pins are labelled as such.

    This does not validate platform evidence, frozen sources or independent journals;
    their runtime guards retain authority. No plaintext, paths or credentials leave it.
    """
    if not isinstance(trace_id, str) or not 1 <= len(trace_id) <= 256:
        raise ValueError("INVALID_TRACE_ID")
    approval = settings.live_text_window
    result = {
        "schema_version": 1, "observed_at": datetime.now(UTC).isoformat(),
        "read_only": True, "diagnostic_only": True, "trace_ref_sha256": _hash(trace_id),
        "configured_candidate_sha": approval.candidate_sha if approval else None,
        "runtime_image_digest": None,
        "configured_approval_sha256": approval.approval_sha256 if approval else None,
        "configured_plan_sha256": approval.plan_sha256 if approval else None,
        "configured_features": {
            name: getattr(settings, name) for name in (
                "memory_enabled", "content_enabled", "affect_enabled", "diary_enabled",
                "consolidation_enabled", "reflection_enabled", "goals_enabled",
                "wake_enabled", "exploration_enabled", "social_enabled", "proactive_enabled",
            )
        },
        "configured_scopes": {
            "history": approval.history_scope if approval else "none",
            "memory": approval.memory_scope if approval else "none",
            "rich_input": getattr(approval, "rich_input_scope", "none"),
            "image": getattr(approval, "image_scope", "none"),
        },
        "billing_basis": "configured_rates_not_invoice", "blockers": [],
    }
    path = Path(settings.database).resolve()
    if not path.is_file():
        result["blockers"].append("DATABASE_MISSING")
        return result
    try:
        with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=1)) as db:
            db.row_factory = sqlite3.Row
            db.execute("PRAGMA query_only=ON")
            remaining = 1000

            def progress():
                nonlocal remaining
                remaining -= 1
                return remaining <= 0

            db.set_progress_handler(progress, 10000)
            db.execute("BEGIN")
            view = _ReadView(db)
            version = view.one("SELECT version_num FROM alembic_version")
            if not version or version["version_num"] != schema_head():
                result["blockers"].append("MIGRATION_REQUIRED")
                return result
            owner = view.one("SELECT value FROM runtime_meta WHERE key='owner_qq_id'")
            if not owner or owner["value"] != settings.owner_qq_id:
                result["blockers"].append("OWNER_IDENTITY_MISMATCH")
                return result
            result["trace"] = _trace(view, trace_id)
            db.rollback()
    except (sqlite3.Error, OSError, ValueError, TypeError, KeyError, AttributeError):
        result["blockers"].append("TRACE_SNAPSHOT_UNAVAILABLE")
    return result
