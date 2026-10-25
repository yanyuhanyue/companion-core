"""Save settled Life wording into native Diary custody without another generation."""

import hashlib
import json
from contextlib import nullcontext
from datetime import datetime
from zoneinfo import ZoneInfo

from companion import owner_authority, paid_budget
from companion.contracts import CoreError
from companion.life_topics import LifeTopics, _paused
from companion.memory_context import check_version, read_version
from companion.privacy import advance_epoch, epoch, redact, sensitive
from companion.store import encode, uid

RECEIPT = "diary_life:"
GENERATOR = "life-expression-1"


def save(service, job_id, *, expected_content_sha256, authority):
    store, cfg, clock = service.store, service.settings, service.clock
    with nullcontext() if store.db.in_transaction else store.transaction():
        owner_authority.current(store, cfg, clock, purpose="life", authority=authority)
        owner_authority.current(store, cfg, clock, purpose="diary", authority=authority)
        if (not service.repo.enabled() or cfg.life_loop.mode != "SHADOW"
            or _paused(store, cfg, clock())):
            raise CoreError("LIFE_CONSUMPTION_PAUSED", 409)
        service.expire_evidence()
        version = read_version(service)
        data = LifeTopics(store, cfg, clock).retained_result(job_id, destination="PRIVATE_DIARY")
        text = data["result"]["text"]
        if paid_budget.sha({"text": text}) != expected_content_sha256:
            raise CoreError("LIFE_DRAFT_CHANGED", 409)
        if not text.strip() or sensitive(text) or redact(text, cfg) != text:
            raise CoreError("DIARY_SENSITIVE_CONTENT", 400)
        key = RECEIPT + job_id
        prior = store.one("SELECT value FROM runtime_meta WHERE key=?", (key,))
        if prior:
            receipt = json.loads(prior["value"])
            entry = store.one("SELECT status,version FROM diary_entries WHERE entry_id=?",
                              (receipt["entry_id"],))
            if not entry or entry["status"] != "COMMITTED":
                raise CoreError("DIARY_EXPRESSION_WITHDRAWN", 410)
            check_version(service, version)
            return {**receipt, "current_version": entry["version"], "reused": True}
        day = datetime.fromisoformat(data["result"]["updated_at"]).astimezone(
            ZoneInfo(cfg.timezone)).date().isoformat()
        if store.one("SELECT 1 FROM runtime_meta WHERE key=?",
                     ("diary_deleted:" + cfg.timezone + ":" + day,)):
            raise CoreError("DIARY_DELETED", 410)
        entry = store.one("SELECT * FROM diary_entries WHERE identity_id='companion' "
                          "AND local_date=? AND timezone=?", (day, cfg.timezone))
        if entry and entry["status"] != "COMMITTED":
            raise CoreError("DIARY_EXPRESSION_WITHDRAWN", 410)
        run = store.one("SELECT * FROM diary_runs WHERE identity_id='companion' "
                        "AND local_date=? AND timezone=?", (day, cfg.timezone))
        if run:
            usage = store.one("SELECT state FROM model_usage WHERE request_id=?",
                              (run["model_request_id"],))
            if run["state"] == "RUNNING" or usage and usage["state"] != "SETTLED":
                raise CoreError("DIARY_DAY_BUSY", 409)
        body = entry["body_md"] + "\n\n## 自主随记\n\n" + text if entry else text
        if len(body) > cfg.diary_max_body_chars:
            raise CoreError("DIARY_BODY_LIMIT", 409)
        original_usage = store.one("SELECT request_id,model FROM model_usage WHERE job_id=? "
                                   "AND scenario='activity' AND state='SETTLED' "
                                   "ORDER BY rowid DESC LIMIT 1", (job_id,))
        if original_usage is None:
            raise CoreError("LIFE_SETTLED_DRAFT_REQUIRED", 409)
        now, entry_id = clock(), entry["entry_id"] if entry else uid("diary")
        sources = [("life-result:" + job_id, data["job"]["event_id"], text), *[
            ("life-source:" + job_id + ":" + source["event_id"], source["event_id"], source["text"])
            for source in data["sources"]]]
        run_id = run["run_id"] if run else uid("diary_run")
        source_digest = paid_budget.sha({"prior": entry["source_set_sha256"] if entry else None,
                                        "job_id": job_id, "sources": [
            [source_id, event_id, hashlib.sha256(content.encode()).hexdigest()]
            for source_id, event_id, content in sources]})
        if run is None:
            store.insert("diary_runs", run_id=run_id, identity_id="companion", local_date=day,
                         timezone=cfg.timezone, state="COMMITTED", source_set_sha256=source_digest,
                         captured_privacy_epoch=epoch(store), max_attempts=1,
                         model_request_id=original_usage["request_id"], created_at=now,
                         updated_at=now)
        else:
            # Keep an earlier Diary admission, including its accounting identity.
            # The per-Life receipt and evidence identify this additional settled producer.
            store.execute("UPDATE diary_runs SET state='COMMITTED',lease_token=NULL,"
                           "next_attempt_at=NULL,last_error_code=NULL,source_set_sha256=?,"
                           "captured_privacy_epoch=?,updated_at=? WHERE run_id=?",
                           (source_digest, epoch(store), now, run_id))
        if entry:
            store.insert("diary_revisions", revision_id=uid("diary_revision"), entry_id=entry_id,
                         prior_title=entry["title"], prior_body_md=entry["body_md"],
                         reason="life_expression", changed_by="companion", created_at=now)
            store.execute("UPDATE diary_entries SET body_md=?,version=version+1,updated_at=?,"
                           "source_set_sha256=?,captured_privacy_epoch=? WHERE entry_id=?",
                           (body, now, source_digest, epoch(store), entry_id))
            entry_version = entry["version"] + 1
        else:
            store.insert("diary_entries", entry_id=entry_id, run_id=run_id, identity_id="companion",
                         local_date=day, timezone=cfg.timezone, title="自主随记", body_md=body,
                         status="COMMITTED", source_set_sha256=source_digest,
                         captured_privacy_epoch=epoch(store), generator_version=GENERATOR,
                         model_name=original_usage["model"], created_at=now, updated_at=now)
            entry_version = 1
        for source_id, event_id, content in sources:
            store.insert("diary_evidence", entry_id=entry_id, source_id=source_id,
                         event_id=event_id, outbox_message_id=None,
                         excerpt_sha256=hashlib.sha256(content.encode()).hexdigest())
        receipt = {"action": "diary.save", "source_job_id": job_id, "entry_id": entry_id,
                   "local_date": day, "timezone": cfg.timezone, "version": entry_version}
        # No prose or source copies: this native receipt survives projection erasure
        # so a retry can never append the same settled result to a rebuilt entry.
        store.insert("runtime_meta", key=key, value=encode(receipt))
        check_version(service, version)
        advance_epoch(store)
        store.audit("diary.life_saved", entry_id, job_id, now, source_job_id=job_id,
                    entry_version=entry_version)
        return {**receipt, "current_version": entry_version, "reused": False}
