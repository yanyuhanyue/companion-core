"""Private fiction on native jobs, evidence, accounting and erasure; never facts."""

import json
from datetime import datetime
from zoneinfo import ZoneInfo

from companion import budget, owner_authority, paid_budget, reply_revision
from companion.contracts import CoreError
from companion.diary import day_bounds
from companion.memory import Memory
from companion.memory_context import MemoryContext, MemorySources, check_version, read_version
from companion.privacy import ErasureLedger, redact, sensitive
from companion.rem_protocol import RULES, RemDraft
from companion.store import after, encode, uid


def _authority(store, settings, clock, *, authority=None):
    if not settings.rem.enabled or not settings.memory_enabled or settings.mode != "live":
        raise CoreError("REM_DISABLED", 403)
    view = owner_authority.current(store, settings, clock, purpose="rem", authority=authority)
    owner_authority.current(store, settings, clock, purpose="memory", authority=view.authority)
    from companion.erasure import cleanup_contracts

    if not any(contract.name == "rem" for contract in cleanup_contracts()):
        raise CoreError("REM_ERASURE_CONTRACT_REQUIRED", 503)
    return view


def _context(store, settings):
    return MemoryContext(Memory(store, settings, ErasureLedger(settings.erasure_ledger)))


def _material(core, retention_days):
    """Nominate at most twenty facts and retain at most three unchanged native facts."""
    now, cfg = core.clock(), core.settings
    sources = MemorySources(core.memory, now)
    cutoff = datetime.fromisoformat(after(now, -86400 * retention_days))
    selected = []
    for row in core.store.rows(
        "SELECT * FROM memories WHERE privacy_scope='private' AND status='CONFIRMED' "
        "AND confidence>0 AND needs_review=0 ORDER BY updated_at DESC,memory_id LIMIT 20",
    ):
        evidence = sources.evidence(row["memory_id"])
        if (not evidence or not sources.current(row) or sensitive(row["content"])
            or redact(row["content"], cfg) != row["content"]
            or any(datetime.fromisoformat(item["ingested_at"]) <= cutoff
                   for root in evidence for item in sources._lineage(root["event_id"]))):
            continue
        selected.append({**row, "evidence_ids": [item["event_id"] for item in evidence]})
        if len(selected) == cfg.rem.max_source_memories:
            break
    if not selected:
        return None
    proof = core.memory_context.pin(now, rows=selected)
    inputs = [{key: row[key] for key in ("memory_id", "subject", "content", "status")}
              for row in selected]
    roots = [core.store.one("SELECT * FROM events WHERE event_id=?", (key,))
             for key in proof["source_event_ids"]]
    if any(root is None for root in roots):
        raise CoreError("PRIVACY_CHANGED", 409)
    expires = min([after(now, 86400 * cfg.rem.record_retention_days), *[
        after(row["ingested_at"], 86400 * retention_days) for row in roots]])
    return inputs, proof, roots[0]["conversation_id"], expires


def schedule_due(core):
    """Called by the existing selector; at most one creation per local day."""
    if not core.settings.rem.enabled:
        return None
    store, cfg, clock = core.store, core.settings, core.clock
    with store.transaction():
        try:
            view = _authority(store, cfg, clock)
        except CoreError:
            return None
        local = datetime.fromisoformat(clock()).astimezone(ZoneInfo(cfg.timezone))
        if local.hour < cfg.rem.local_hour or budget.chat_pending(store, clock()):
            return None
        day = local.date().isoformat()
        if store.one("SELECT 1 FROM rem_records WHERE local_date=? AND timezone=?",
                     (day, cfg.timezone)):
            return None
        material = _material(core, min(cfg.rem.source_retention_days,
                                      view.policy.source_retention_days,
                                      cfg.raw_event_retention_days))
        if material is None:
            return None
        inputs, proof, conversation, expires = material
        rem_id, event_id, now = uid("rem"), uid("evt_rem"), clock()
        store.insert("events", event_id=event_id, schema_version=1, source="system.control",
                     source_event_id=rem_id, conversation_id=conversation, actor_id="system",
                     event_type="rem.requested", occurred_at=now, ingested_at=now,
                     privacy_scope="private", body_json=encode({"rem_id": rem_id}),
                     trace_id=rem_id, memory_processed=1)
        job_id = core.new_job(event_id, "rem", rem_id, now)
        owner_authority.bind_job(store, cfg, clock, job_id, purpose="rem")
        store.execute("UPDATE jobs SET max_attempts=1 WHERE job_id=?", (job_id,))
        for key in proof["source_event_ids"]:
            store.insert("event_dependencies", event_id=event_id, source_event_id=key)
            store.insert("job_sources", job_id=job_id, event_id=key)
        store.insert("rem_records", rem_id=rem_id, job_id=job_id, local_date=day,
                     timezone=cfg.timezone, config_sha256=paid_budget.sha(cfg.rem.model_dump()),
                     source_memory_ids_json=encode([row["memory_id"] for row in inputs]),
                     source_event_ids_json=encode(proof["source_event_ids"]),
                     source_snapshot_json=encode({"inputs": inputs, "proof": proof}),
                     created_at=now, expires_at=expires)
        store.audit("rem.scheduled", rem_id, rem_id, now, kind="FICTION")
        return job_id


def check_job(store, settings, clock, job_id, *, lease_token=None):
    row = store.one("SELECT r.*,j.state,j.lease_token,j.lease_until,j.event_id "
                    "FROM rem_records r JOIN jobs j USING(job_id) "
                    "WHERE r.job_id=? AND j.kind='rem'", (job_id,))
    if (not row or row["redacted"] or row["state"] != "RUNNING"
        or not row["lease_until"] or row["lease_until"] <= clock()
        or row["expires_at"] <= clock()
        or lease_token is not None and row["lease_token"] != lease_token):
        raise CoreError("REM_JOB_FENCED", 409)
    view = owner_authority.check_job(store, settings, clock, row, purpose="rem", executing=True)
    _authority(store, settings, clock, authority=view.authority)
    if row["config_sha256"] != paid_budget.sha(settings.rem.model_dump()):
        raise CoreError("REM_POLICY_CHANGED", 409)
    reply_revision.check_sources(store, job_id)
    snapshot = json.loads(row["source_snapshot_json"])
    _context(store, settings).revalidate(snapshot["proof"], clock())
    return row, snapshot


def admit(store, settings, clock, job_id, estimated_input, estimate):
    """Called inside original budget.reserve; original usage is the only counter."""
    if not store.db.in_transaction:
        raise RuntimeError("REM_ADMISSION_TRANSACTION_REQUIRED")
    check_job(store, settings, clock, job_id)
    cfg = settings.rem
    if estimated_input > cfg.max_input_tokens:
        raise CoreError("REM_INPUT_LIMIT", 429)
    local = datetime.fromisoformat(clock()).astimezone(ZoneInfo(settings.timezone)).date()
    day = day_bounds(local, settings.timezone)[0]
    month = day_bounds(local.replace(day=1), settings.timezone)[0]
    usage = store.one("SELECT COUNT(*) n,COALESCE(SUM(billed_micro_usd),0) cost "
                      "FROM model_usage WHERE scenario='rem' AND occurred_at>=?", (day,))
    monthly = store.one("SELECT COALESCE(SUM(billed_micro_usd),0) cost FROM model_usage "
                        "WHERE scenario='rem' AND occurred_at>=?", (month,))["cost"]
    if not cfg.daily_micro_usd or not cfg.monthly_micro_usd:
        raise CoreError("SCENARIO_BUDGET_DISABLED", 429)
    if usage["n"] >= 1:
        raise CoreError("SCENARIO_CALL_LIMIT", 429)
    if usage["cost"] + estimate > cfg.daily_micro_usd:
        raise CoreError("DAILY_BUDGET_EXHAUSTED", 429)
    if monthly + estimate > cfg.monthly_micro_usd:
        raise CoreError("SCENARIO_MONTHLY_BUDGET_EXHAUSTED", 429)
    if store.one("SELECT 1 FROM model_usage WHERE job_id=?", (job_id,)):
        raise CoreError("REM_ALREADY_ATTEMPTED", 409)


async def run(core, job):
    store, settings, clock = core.store, core.settings, core.clock
    def source_check():
        return check_job(store, settings, clock, job["job_id"], lease_token=job["lease_token"])
    try:
        row, snapshot = source_check()
        messages = [{"role": "system", "content": RULES},
                    {"role": "user", "content": encode({"memories": snapshot["inputs"]})}]
        request_id = budget.reserve(store, settings, job["job_id"], "rem", messages, clock(),
            clock=clock, logical_request_id=row["rem_id"], source_check=source_check)
        with store.transaction():
            source_check()
            store.execute("UPDATE rem_records SET model_request_id=? WHERE rem_id=?",
                           (request_id, row["rem_id"]))
        response = await budget.complete(core.model, store, settings, request_id, messages,
                                         "rem", clock, source_check=source_check)
        usage = store.one("SELECT state FROM model_usage WHERE request_id=?", (request_id,))
        if not usage or usage["state"] != "SETTLED":
            raise CoreError("MODEL_USAGE_UNRESOLVED", 409)
        if response.finish_reason != "stop":
            raise CoreError("REM_INCOMPLETE_RESPONSE", 502)
        draft = RemDraft.model_validate_json(response.text)
        if sensitive(response.text) or redact(response.text, settings) != response.text:
            raise CoreError("REM_SENSITIVE_CONTENT", 400)
        output = {"kind": "FICTION", "decision": draft.decision,
                  "title": draft.title, "text": draft.text,
                  "questions": [{"text": text, "state": "UNVERIFIED"} for text in draft.questions]}
        with store.transaction():
            source_check()
            store.execute("UPDATE rem_records SET output_json=? WHERE rem_id=?",
                           (encode(output), row["rem_id"]))
            store.execute("UPDATE jobs SET state='SUCCEEDED',lease_token=NULL,updated_at=? "
                           "WHERE job_id=? AND lease_token=?", (clock(), job["job_id"],
                                                                job["lease_token"]))
            store.audit("rem.completed", row["rem_id"], job["trace_id"], clock(),
                        kind="FICTION", decision=draft.decision)
    except Exception as error:
        code = error.code if isinstance(error, CoreError) else "REM_INVALID_RESULT"
        with store.transaction():
            if code == "BACKGROUND_YIELD_TO_CHAT" and not store.one(
                "SELECT 1 FROM model_usage WHERE job_id=?", (job["job_id"],),
            ):
                store.execute("UPDATE jobs SET state='RETRY_WAIT',lease_token=NULL,updated_at=?,"
                               "next_attempt_at=?,last_error_code=? WHERE job_id=? "
                               "AND state='RUNNING' AND lease_token=?",
                               (clock(), after(clock(), 60), code, job["job_id"],
                                job["lease_token"]))
                return True
            store.execute("UPDATE jobs SET state='FAILED',lease_token=NULL,last_error_code=?,"
                           "updated_at=? WHERE job_id=? AND state='RUNNING' AND lease_token=?",
                           (code, clock(), job["job_id"], job["lease_token"]))
            store.audit("rem.failed", job["job_id"], job["trace_id"], clock(), code=code)
    return True


def recover_interrupted(store, now):
    store.execute("UPDATE jobs SET state='CANCELLED',lease_token=NULL,updated_at=?,"
                   "last_error_code='REM_INTERRUPTED' WHERE kind='rem' "
                   "AND state='RUNNING' AND lease_until<=?", (now, now))


def erase(store, event_ids):
    if not event_ids or not store.one("SELECT 1 FROM sqlite_master WHERE name='rem_records'"):
        return
    keys = encode(sorted(event_ids))
    for row in store.rows("SELECT r.rem_id,r.job_id FROM rem_records r JOIN jobs j USING(job_id) "
                          "WHERE r.redacted=0 AND (j.event_id IN (SELECT value FROM json_each(?)) "
                          "OR EXISTS (SELECT 1 FROM json_each(r.source_event_ids_json) s "
                          "WHERE s.value IN (SELECT value FROM json_each(?))))", (keys, keys)):
        _scrub(store, row, "REM_SOURCE_ERASED")


def _scrub(store, row, code):
    store.execute("UPDATE rem_records SET redacted=1,source_snapshot_json='{}',output_json='{}',"
                   "source_memory_ids_json='[]',source_event_ids_json='[]' WHERE rem_id=?",
                   (row["rem_id"],))
    store.execute("UPDATE jobs SET state='CANCELLED',lease_token=NULL,last_error_code=? "
                   "WHERE job_id=? AND state IN ('QUEUED','RUNNING','RETRY_WAIT')",
                   (code, row["job_id"]))


def expire(store, ledger, now):
    if not store.one("SELECT 1 FROM sqlite_master WHERE name='rem_records'"):
        return
    rows = store.rows("SELECT j.event_id FROM rem_records r JOIN jobs j USING(job_id) "
                      "WHERE r.redacted=0 AND r.expires_at<=? ORDER BY r.expires_at LIMIT 1000",
                      (now,))
    if rows:
        # Expire only the derived REM event. Original memory sources and their
        # facts remain intact; the original external Ledger makes restore replay safe.
        ledger.expire_events(store, [row["event_id"] for row in rows], now, retention_cutoff=now)


def records(core, *, limit=20):
    """Owner-control projection; outer API authenticates owner, even after REM stops."""
    if type(limit) is not int or not 1 <= limit <= 50:
        raise CoreError("INVALID_REM_LIMIT", 400)
    version, result = read_version(core.memory), []
    for row in core.store.rows("SELECT r.*,j.state,j.last_error_code FROM rem_records r "
                               "JOIN jobs j USING(job_id) ORDER BY r.created_at DESC,r.rem_id "
                               "LIMIT ?", (limit,)):
        available = not row["redacted"] and row["expires_at"] > core.clock()
        if available:
            try:
                core.memory_context.revalidate(json.loads(row["source_snapshot_json"])["proof"],
                                                core.clock())
            except (CoreError, ValueError, KeyError):
                available = False
        output = json.loads(row["output_json"]) if available else {}
        usage = core.store.one("SELECT request_id,state,input_tokens,output_tokens,"
                               "cached_input_tokens,billed_micro_usd FROM model_usage "
                               "WHERE job_id=? AND scenario='rem' ORDER BY rowid DESC LIMIT 1",
                               (row["job_id"],))
        result.append({**{key: row[key] for key in (
            "rem_id", "job_id", "local_date", "timezone", "state", "last_error_code",
            "redacted", "expires_at")}, "kind": "FICTION", "source_available": available,
            "decision": output.get("decision"), "title": output.get("title", ""),
            "text": output.get("text", ""), "questions": output.get("questions", []),
            "source_memory_ids": json.loads(row["source_memory_ids_json"]) if available else [],
            "source_event_ids": json.loads(row["source_event_ids_json"]) if available else [],
            "usage": usage})
    check_version(core.memory, version)
    return result
