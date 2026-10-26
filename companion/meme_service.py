"""HTTP-facing discovery jobs and non-model resource reservations.

The existing jobs table owns scheduling. This service never fetches media, calls a
model, or sends a message; the authenticated Bridge operates under its lease.
"""

import json
from contextlib import nullcontext
from datetime import datetime
from typing import Literal
from zoneinfo import ZoneInfo

from pydantic import AwareDatetime, Field

from companion import owner_authority, owner_input, private_life, reply_revision
from companion.contracts import CoreError, StrictModel
from companion.expression import MemeIntent
from companion.store import after, encode, uid
from companion.transport import digest

META = "meme_discovery:"
FIELDS = ("queries", "requests", "download_bytes", "storage_bytes", "model_cost")


class DiscoveryRequest(StrictModel):
    event_id: str = Field(min_length=8, max_length=128)
    reply_revision: int = Field(ge=0, strict=True)
    intent: MemeIntent
    provider: Literal["community", "brave"]


class DiscoveryLeaseRequest(StrictModel):
    worker_id: str = Field(min_length=1, max_length=128)


class DiscoveryCheck(StrictModel):
    lease_token: str = Field(pattern=r"^lease_[a-f0-9]{32}$")
    attempt_key: str = Field(pattern=r"^meme_attempt_[a-f0-9]{32}$")


class DiscoveryLimits(StrictModel):
    queries: int = Field(default=1, ge=1, le=1, strict=True)
    requests: int = Field(default=12, ge=1, le=20, strict=True)
    download_bytes: int = Field(default=16 * 1024 * 1024, ge=1, le=32 * 1024 * 1024, strict=True)
    storage_bytes: int = Field(default=4 * 1024 * 1024, ge=1, le=16 * 1024 * 1024, strict=True)
    model_cost: int = Field(default=0, ge=0, le=0, strict=True)
    max_images: int = Field(default=3, ge=1, le=8, strict=True)


class DiscoveryReserve(DiscoveryCheck):
    limits: DiscoveryLimits


class DiscoveryUsage(StrictModel):
    # Accept an observed overrun for conservative accounting, rather than reject
    # the receipt and silently understate an already incurred responsibility.
    queries: int = Field(ge=0, le=2**53 - 1, strict=True)
    requests: int = Field(ge=0, le=2**53 - 1, strict=True)
    download_bytes: int = Field(ge=0, le=2**53 - 1, strict=True)
    storage_bytes: int = Field(ge=0, le=2**53 - 1, strict=True)
    model_cost: int = Field(default=0, ge=0, le=0, strict=True)


class DiscoveryResult(DiscoveryCheck):
    outcome: Literal["SETTLED", "UNKNOWN"]
    usage: DiscoveryUsage


class DiscoverySubmission(StrictModel):
    job_id: str
    event_id: str
    reply_revision: int
    state: Literal["QUEUED", "RUNNING", "SUCCEEDED", "FAILED", "CANCELLED"]
    duplicate: bool


class DiscoveryClaim(DiscoveryRequest, DiscoveryCheck):
    job_id: str
    lease_until: AwareDatetime


class DiscoveryLeaseResponse(StrictModel):
    item: DiscoveryClaim | None


class DiscoveryCheckResponse(StrictModel):
    approved: Literal[True] = True
    job_id: str
    attempt_key: str
    event_id: str
    reply_revision: int
    lease_until: AwareDatetime


class DiscoveryReservation(StrictModel):
    job_id: str
    attempt_key: str
    state: Literal["RESERVED"] = "RESERVED"
    lease_until: AwareDatetime
    limits: DiscoveryLimits


class DiscoverySettlement(StrictModel):
    job_id: str
    attempt_key: str
    state: Literal["SETTLED", "UNKNOWN"]


def policy_hash(settings):
    """Bind the existing discovery and caption policies as one native capability."""
    if not (settings.expression_enabled or settings.meme_discovery_enabled
            or settings.meme_semantics_enabled):
        return None
    paid = settings.paid_model
    value = {
        "discovery": settings.meme_discovery_enabled,
        "semantics": settings.meme_semantics_enabled,
        "expression": settings.expression_enabled,
        "caption_input": paid.meme_input.model_dump(mode="json")
        if paid and paid.meme_input else None,
        **{key: getattr(settings, key) for key in (
            "meme_caption_daily_micro_usd", "meme_discovery_daily_queries",
            "meme_discovery_daily_download_bytes", "meme_discovery_daily_storage_bytes",
            "expression_selection_probability", "expression_cooldown_minutes",
            "expression_meme_categories",
        )},
    }
    owner = getattr(settings, "meme_catalog_owner", "bridge")
    rules = getattr(settings, "meme_trusted_sources", ())
    cooldown = getattr(settings, "meme_cooldown_seconds", 1800)
    if owner != "bridge" or rules or cooldown != 1800:
        from companion.meme_policy import source_rules

        value["catalog"] = {"owner": owner, "sources": [r.model_dump(mode="json")
            for r in source_rules(settings)], "cooldown_seconds": cooldown}
    return digest(value)


def _scope(store, settings, clock):
    if (getattr(settings, "meme_catalog_owner", "bridge") == "core"
        or not getattr(settings, "meme_discovery_enabled", False)):
        raise CoreError("MEME_DISCOVERY_DISABLED", 403)
    return owner_authority.current(store, settings, clock, purpose="meme")


def _source(store, settings, approval, event_id, revision, clock):
    reply_revision.require_current(store, event_id, revision)
    event, _, _ = owner_input.source(store, settings, approval, event_id, clock(), purpose="meme")
    return event


def _metadata(store, job_id):
    row = store.one("SELECT value FROM runtime_meta WHERE key=?", (META + job_id,))
    if not row:
        raise CoreError("MEME_DISCOVERY_JOB_MISSING", 404)
    return json.loads(row["value"])


def _save(store, job_id, value):
    store.execute("INSERT OR REPLACE INTO runtime_meta(key,value) VALUES (?,?)",
                  (META + job_id, encode(value)))


def _owned(store, job_id, body):
    job = store.one("SELECT * FROM jobs WHERE job_id=? AND kind='meme_discovery'", (job_id,))
    value = _metadata(store, job_id)
    if (not job or value.get("attempt_key") != body.attempt_key
        or value.get("lease_token") != body.lease_token or value["event_id"] != job["event_id"]):
        raise CoreError("MEME_DISCOVERY_STALE_LEASE", 409)
    return job, value


def _check(store, settings, clock, job_id, body):
    approval = _scope(store, settings, clock)
    job, value = _owned(store, job_id, body)
    if (job["state"] != "RUNNING" or job["lease_token"] != body.lease_token
        or not job["lease_until"] or job["lease_until"] <= clock()):
        raise CoreError("MEME_DISCOVERY_STALE_LEASE", 409)
    owner_authority.check_job(store, settings, clock, job, purpose="meme", executing=True)
    _source(store, settings, approval, value["event_id"], value["reply_revision"], clock)
    reservation = store.one("SELECT state FROM meme_discovery_reservations WHERE job_id=?",
                            (job_id,))
    if reservation and reservation["state"] != "RESERVED":
        raise CoreError("MEME_DISCOVERY_ATTEMPT_FINISHED", 409)
    return job, value


def submit(store, settings, clock, body):
    with store.transaction():
        approval = _scope(store, settings, clock)
        _source(store, settings, approval, body.event_id, body.reply_revision, clock)
        request = body.model_dump()
        key = "meme_discovery:" + digest(request)
        prior = store.one("SELECT * FROM jobs WHERE idempotency_key=?", (key,))
        if prior:
            return DiscoverySubmission(
                job_id=prior["job_id"], event_id=body.event_id, reply_revision=body.reply_revision,
                state=prior["state"], duplicate=True,
            ).model_dump()
        now, job_id = clock(), uid("job")
        store.insert("jobs", job_id=job_id, event_id=body.event_id, kind="meme_discovery",
                     state="QUEUED", idempotency_key=key, max_attempts=1, context_tracked=1,
                     created_at=now, updated_at=now)
        owner_authority.bind_job(store, settings, clock, job_id, purpose="meme")
        store.insert("job_sources", job_id=job_id, event_id=body.event_id)
        _save(store, job_id, request)
        return DiscoverySubmission(job_id=job_id, event_id=body.event_id,
            reply_revision=body.reply_revision, state="QUEUED", duplicate=False).model_dump()


def recover(store, settings, now):
    """An expired reserved attempt remains charged and cannot be dispatched again."""
    with nullcontext() if store.db.in_transaction else store.transaction():
        for job in store.rows(
            "SELECT * FROM jobs WHERE kind='meme_discovery' "
            "AND state IN ('RUNNING','CANCELLED','FAILED') "
            "AND lease_until<=?", (now,),
        ):
            reserved = store.one("SELECT * FROM meme_discovery_reservations WHERE job_id=?",
                                 (job["job_id"],))
            if reserved:
                if reserved["state"] == "RESERVED":
                    store.execute("UPDATE meme_discovery_reservations SET state='UNKNOWN',"
                                  "updated_at=? WHERE job_id=?", (now, job["job_id"]))
                if job["state"] == "RUNNING":
                    store.execute("UPDATE jobs SET state='FAILED',lease_token=NULL,updated_at=?,"
                                  "last_error_code='MEME_DISCOVERY_UNKNOWN' WHERE job_id=?",
                                  (now, job["job_id"]))
            elif job["state"] == "RUNNING":
                store.execute("UPDATE jobs SET state='QUEUED',lease_token=NULL,lease_until=NULL,"
                              "attempts=0,updated_at=? WHERE job_id=?", (now, job["job_id"]))


def lease(store, settings, clock, body):
    recover(store, settings, clock())
    with store.transaction():
        approval = _scope(store, settings, clock)
        if (private_life.owner_waiting(store, settings, clock())
            or private_life.busy(store, clock())):
            return {"item": None}
        # One active discovery lease also bounds non-model network concurrency.
        if store.one("SELECT 1 FROM jobs WHERE kind='meme_discovery' AND state='RUNNING' LIMIT 1"):
            return {"item": None}
        for job in store.rows("SELECT * FROM jobs WHERE kind='meme_discovery' AND state='QUEUED' "
                               "ORDER BY rowid LIMIT 32"):
            value = _metadata(store, job["job_id"])
            try:
                owner_authority.check_job(store, settings, clock, job, purpose="meme")
                _source(store, settings, approval, value["event_id"], value["reply_revision"],
                        clock)
            except CoreError as error:
                store.execute("UPDATE jobs SET state='CANCELLED',last_error_code=?,updated_at=? "
                              "WHERE job_id=?", (error.code, clock(), job["job_id"]))
                continue
            token, attempt, now = uid("lease"), uid("meme_attempt"), clock()
            until = after(now, settings.lease_seconds)
            store.execute("UPDATE jobs SET state='RUNNING',lease_owner=?,lease_token=?,"
                          "lease_until=?,"
                          "attempts=1,updated_at=? WHERE job_id=?",
                          (body.worker_id, token, until, now, job["job_id"]))
            _save(store, job["job_id"], {**value, "attempt_key": attempt, "lease_token": token,
                                       "lease_until": until})
            claim = DiscoveryClaim(job_id=job["job_id"], lease_token=token, attempt_key=attempt,
                                   lease_until=until,
                                   **{key: value[key] for key in DiscoveryRequest.model_fields})
            return DiscoveryLeaseResponse(item=claim).model_dump(mode="json")
        return {"item": None}


def check(store, settings, clock, job_id, body):
    with store.transaction():
        job, value = _check(store, settings, clock, job_id, body)
        return DiscoveryCheckResponse(job_id=job_id, attempt_key=body.attempt_key,
            event_id=value["event_id"], reply_revision=value["reply_revision"],
            lease_until=job["lease_until"]).model_dump(mode="json")


def reserve(store, settings, clock, job_id, body):
    with store.transaction():
        job, value = _check(store, settings, clock, job_id, body)
        if store.one("SELECT 1 FROM meme_discovery_reservations WHERE job_id=? OR attempt_key=?",
                      (job_id, body.attempt_key)):
            raise CoreError("MEME_DISCOVERY_ATTEMPT_ALREADY_RESERVED", 409)
        now = clock()
        day = datetime.fromisoformat(now).astimezone(ZoneInfo(settings.timezone)).date().isoformat()
        limits = body.limits.model_dump()
        for field in ("queries", "download_bytes", "storage_bytes"):
            total = store.one(
                f"SELECT COALESCE(SUM(CASE WHEN state='SETTLED' THEN {field} "
                f"ELSE MAX(reserved_{field},{field}) END),0) AS n "
                "FROM meme_discovery_reservations WHERE day=?", (day,),
            )["n"]
            if total + limits[field] > getattr(settings, "meme_discovery_daily_" + field, 0):
                raise CoreError("MEME_DISCOVERY_DAILY_" + field.upper() + "_EXHAUSTED", 429)
        store.insert("meme_discovery_reservations", job_id=job_id, attempt_key=body.attempt_key,
                     day=day, state="RESERVED", created_at=now, updated_at=now,
                     **{"reserved_" + field: limits[field] for field in FIELDS},
                     **{field: 0 for field in FIELDS})
        _save(store, job_id, {**value, "limits": limits})
        return DiscoveryReservation(job_id=job_id, attempt_key=body.attempt_key,
            lease_until=job["lease_until"], limits=body.limits).model_dump(mode="json")


def result(store, settings, clock, job_id, body):
    # Accounting is allowed after STOP, source withdrawal and lease expiry. The
    # immutable attempt witness can establish responsibility, never new permission.
    with store.transaction():
        job, value = _owned(store, job_id, body)
        row = store.one("SELECT * FROM meme_discovery_reservations "
                        "WHERE job_id=? AND attempt_key=?",
                        (job_id, body.attempt_key))
        if not row:
            raise CoreError("MEME_DISCOVERY_RESERVATION_REQUIRED", 409)
        usage = body.usage.model_dump()
        if row["state"] == "SETTLED":
            if body.outcome != "SETTLED" or any(usage[field] != row[field] for field in FIELDS):
                raise CoreError("MEME_DISCOVERY_RESULT_CONFLICT", 409)
            return DiscoverySettlement(job_id=job_id, attempt_key=body.attempt_key,
                                        state="SETTLED").model_dump()
        state = "UNKNOWN" if row["state"] == "UNKNOWN" or body.outcome == "UNKNOWN" or any(
            usage[field] > row["reserved_" + field] for field in FIELDS
        ) else "SETTLED"
        observed = {field: max(row[field], usage[field]) for field in FIELDS}
        now = clock()
        store.execute("UPDATE meme_discovery_reservations SET state=?,queries=?,requests=?,"
                      "download_bytes=?,storage_bytes=?,model_cost=?,updated_at=? WHERE job_id=?",
                      (state, *(observed[field] for field in FIELDS), now, job_id))
        if job["state"] == "RUNNING":
            active = reply_revision.current(store, value["event_id"], value["reply_revision"])
            final = ("SUCCEEDED" if state == "SETTLED" else "FAILED") if active else "CANCELLED"
            store.execute("UPDATE jobs SET state=?,lease_token=NULL,updated_at=?,last_error_code=? "
                          "WHERE job_id=?", (final, now,
                          None if state == "SETTLED" else "MEME_DISCOVERY_UNKNOWN", job_id))
        return DiscoverySettlement(job_id=job_id, attempt_key=body.attempt_key,
                                    state=state).model_dump()
