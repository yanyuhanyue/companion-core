import json
import os
import re
from datetime import date
from pathlib import Path
from zoneinfo import ZoneInfo

from companion.contracts import CoreError
from companion.store import encode, uid

SECRET_PATTERNS = [
    r"(?i)\b(?:sk-|ghp_|gho_)[a-z0-9_-]{12,}",
    r"(?i)\bBearer\s+[^\s\"'<>]+",
    r"(?i)\b(?:api[_-]?key|password|passwd|token|cookie|skey|p_skey)\s*[:=]\s*[^\s,;]+",
    r"(?:密码|密钥)\s*[:：=]\s*[^\s,，;；]+",
]


def redact(text, settings):
    for secret in (
        settings.bridge_token.get_secret_value(),
        settings.model_key.get_secret_value(),
        settings.diary_admin_token.get_secret_value(),
    ):
        if secret:
            text = text.replace(secret, "[REDACTED]")
    for pattern in SECRET_PATTERNS:
        text = re.sub(pattern, "[REDACTED]", text)
    return text


def sensitive(text):
    return bool(
        re.search(
            r"REDACTED|密码|密钥|身份证|银行卡|住址|诊断|病历|抑郁|性取向|宗教|政治立场|"
            r"我朋友|我同事|他患|她患|帖子|网页|系统规则|system prompt|ignore previous|^>",
            text,
            re.I | re.M,
        )
    )


def epoch(store):
    row = store.one("SELECT value FROM runtime_meta WHERE key='privacy_epoch'")
    return int(row["value"]) if row else 0


def advance_epoch(store):
    store.execute(
        "INSERT OR REPLACE INTO runtime_meta VALUES ('privacy_epoch',?)",
        (str(epoch(store) + 1),),
    )


def diary_copies(store, dates):
    """Resolve explicit diary commands, including records from before date metadata existed."""
    result = set()
    for row in store.rows(
        "SELECT event_id,body_json FROM events WHERE redacted=0 "
        "AND json_extract(body_json,'$.diary_command')=1"
    ):
        body = json.loads(row["body_json"])
        parts = body.get("text", "").split(maxsplit=2)
        if not parts:
            continue
        if parts[0] == "日记列表" or (parts[0] == "读日记" and len(parts) == 1):
            result.add(row["event_id"])
        elif parts[0] in {"读日记", "修改日记"} and len(parts) >= 2:
            if any(
                parts[1] == day and body.get("diary_timezone", timezone) == timezone
                for day, timezone in dates
            ):
                result.add(row["event_id"])
    return result


class ErasureLedger:
    def __init__(self, path):
        self.path = Path(path)

    def initialize(self, store):
        existing = store.one("SELECT value FROM runtime_meta WHERE key='ledger_id'")
        if not self.path.exists():
            if existing:
                raise CoreError("ERASURE_LEDGER_MISSING", 503)
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("x", encoding="utf-8") as stream:
                stream.write(encode({"ledger_id": uid("ledger")}) + "\n")
                stream.flush()
                os.fsync(stream.fileno())
        lines = self.read()
        ledger_id = lines[0]["ledger_id"]
        if existing and existing["value"] != ledger_id:
            raise CoreError("ERASURE_LEDGER_MISMATCH", 503)
        store.execute("INSERT OR IGNORE INTO runtime_meta VALUES ('ledger_id',?)", (ledger_id,))
        self.replay(store)

    def read(self):
        try:
            lines = [json.loads(line) for line in self.path.read_text("utf-8").splitlines()]
            if not lines or not isinstance(lines[0].get("ledger_id"), str):
                raise ValueError("missing header")
            for seq, record in enumerate(lines[1:], 1):
                if (
                    record["seq"] != seq
                    or not isinstance(record["event_ids"], list)
                    or not isinstance(record["memory_ids"], list)
                    or not all(
                        isinstance(value, str)
                        for value in record["event_ids"] + record["memory_ids"]
                    )
                ):
                    raise ValueError("invalid sequence")
                if record.get("target_type", "memory") not in {"memory", "event_batch", "diary"}:
                    raise ValueError("unknown erasure target")
                if record.get("target_type") == "diary":
                    if date.fromisoformat(record["diary_day"]).isoformat() != record["diary_day"]:
                        raise ValueError("invalid diary date")
                    ZoneInfo(record["diary_timezone"])
            return lines
        except (ValueError, KeyError, OSError, TypeError) as error:
            raise CoreError("ERASURE_LEDGER_INVALID", 503) from error

    def append(self, store, memory_id, now):
        # Caller holds a DB write transaction, serializing ledger appends across API workers.
        rows = store.rows("SELECT memory_id,event_id FROM memory_evidence")
        memory_ids, event_ids = {memory_id}, set()
        relatives = store.rows("SELECT memory_id,supersedes_memory_id FROM memories")
        while True:
            previous = (len(memory_ids), len(event_ids))
            for row in relatives:
                if row["memory_id"] in memory_ids or row["supersedes_memory_id"] in memory_ids:
                    memory_ids.add(row["memory_id"])
                    if row["supersedes_memory_id"]:
                        memory_ids.add(row["supersedes_memory_id"])
            for row in rows:
                if row["memory_id"] in memory_ids:
                    event_ids.add(row["event_id"])
                if row["event_id"] in event_ids:
                    memory_ids.add(row["memory_id"])
            if previous == (len(memory_ids), len(event_ids)):
                break
        record = {
            "seq": len(self.read()),
            "erasure_id": uid("erase"),
            "memory_id": memory_id,
            "memory_ids": sorted(memory_ids),
            "event_ids": sorted(event_ids),
            "at": now,
        }
        with self.path.open("a", encoding="utf-8") as stream:
            stream.write(encode(record) + "\n")
            stream.flush()
            os.fsync(stream.fileno())
        self.apply(store, record)

    def expire_events(self, store, event_ids, now):
        record = {
            "seq": len(self.read()),
            "erasure_id": uid("erase"),
            "memory_id": uid("retention"),
            "memory_ids": [],
            "event_ids": event_ids,
            "at": now,
            "target_type": "event_batch",
        }
        with self.path.open("a", encoding="utf-8") as stream:
            stream.write(encode(record) + "\n")
            stream.flush()
            os.fsync(stream.fileno())
        self.apply(store, record)

    def forget_diary(self, store, day, timezone, now):
        target = "companion:" + timezone + ":" + day
        if store.one(
            "SELECT 1 FROM privacy_erasure_ledger WHERE target_type='diary' AND target_id=?",
            (target,),
        ):
            return
        copies = diary_copies(store, {(day, timezone)})
        record = {
            "seq": len(self.read()),
            "erasure_id": uid("erase"),
            "memory_id": target,
            "memory_ids": [],
            "event_ids": sorted(copies),
            "at": now,
            "target_type": "diary",
            "diary_day": day,
            "diary_timezone": timezone,
        }
        with self.path.open("a", encoding="utf-8") as stream:
            stream.write(encode(record) + "\n")
            stream.flush()
            os.fsync(stream.fileno())
        self.apply(store, record)

    def erase_diary_projections(self, store, record):
        if not store.one("SELECT 1 FROM sqlite_master WHERE type='table' AND name='diary_entries'"):
            return set()
        targets = set()
        if record.get("target_type") == "diary":
            day, timezone = record["diary_day"], record["diary_timezone"]
            targets = {
                row["entry_id"]
                for row in store.rows(
                    "SELECT entry_id FROM diary_entries WHERE identity_id='companion' "
                    "AND local_date=? AND timezone=?",
                    (day, timezone),
                )
            }
            # A date tombstone also survives restoration of a backup taken before generation.
            store.execute(
                "INSERT OR REPLACE INTO runtime_meta VALUES (?,?)",
                ("diary_deleted:" + timezone + ":" + day, record["erasure_id"]),
            )
            store.execute(
                "UPDATE diary_runs SET state='CANCELLED',lease_token=NULL,"
                "last_error_code='DIARY_DELETED',updated_at=? WHERE local_date=? "
                "AND timezone=?",
                (record["at"], day, timezone),
            )
        affected = set(targets)
        for event_id in record["event_ids"]:
            affected.update(
                row["entry_id"]
                for row in store.rows(
                    "SELECT entry_id FROM diary_evidence WHERE event_id=?",
                    (event_id,),
                )
            )
        if record.get("target_type") != "diary":
            # Raw-fact erasure still clears every reply that could paraphrase that fact.
            affected.update(
                row["entry_id"]
                for row in store.rows(
                    "SELECT entry_id FROM diary_evidence WHERE outbox_message_id IS NOT NULL"
                )
            )
            affected.update(
                row["entry_id"]
                for row in store.rows(
                    "SELECT d.entry_id FROM diary_entries d WHERE status='COMMITTED' AND "
                    "(generator_version<>'diary-1' OR NOT EXISTS "
                    "(SELECT 1 FROM diary_evidence e WHERE e.entry_id=d.entry_id))"
                )
            )
        dates = {
            (row["local_date"], row["timezone"])
            for row in store.rows("SELECT entry_id,local_date,timezone FROM diary_entries")
            if row["entry_id"] in affected
        }
        copies = diary_copies(store, dates) if dates else set()
        for entry_id in affected:
            store.execute(
                "UPDATE diary_entries SET title='',body_md='',source_set_sha256='',"
                "status=CASE WHEN status='DELETED' THEN 'DELETED' ELSE ? END,updated_at=? "
                "WHERE entry_id=?",
                ("DELETED" if entry_id in targets else "REDACTED", record["at"], entry_id),
            )
            store.execute("DELETE FROM diary_revisions WHERE entry_id=?", (entry_id,))
            store.execute("DELETE FROM diary_evidence WHERE entry_id=?", (entry_id,))
        # Never retry a model output captured before an erasure, even after a process crash.
        store.execute(
            "UPDATE diary_runs SET state='CANCELLED',lease_token=NULL,"
            "last_error_code='PRIVACY_CHANGED',updated_at=? "
            "WHERE state IN ('RUNNING','RETRY_WAIT')",
            (record["at"],),
        )
        return copies

    def replay(self, store):
        lines = self.read()
        applied = store.one(
            "SELECT MAX(restore_min_replay_version) AS n FROM privacy_erasure_ledger"
        )
        if (applied["n"] or 0) > len(lines) - 1:
            raise CoreError("ERASURE_LEDGER_TRUNCATED", 503)
        with store.transaction():
            for record in lines[1:]:
                if not store.one(
                    "SELECT 1 FROM privacy_erasure_ledger WHERE erasure_id=?",
                    (record["erasure_id"],),
                ):
                    self.apply(store, record)

    def apply(self, store, record):
        copies = self.erase_diary_projections(store, record)
        for memory_id in record["memory_ids"]:
            store.execute(
                "UPDATE memories SET content='',status='FORGOTTEN',fact_key='',"
                "confidence=0,updated_at=? WHERE memory_id=?",
                (record["at"], memory_id),
            )
            store.execute("DELETE FROM memory_revisions WHERE memory_id=?", (memory_id,))
            store.execute("DELETE FROM memory_evidence WHERE memory_id=?", (memory_id,))
        event_ids = set(record["event_ids"]) | copies
        for event_id in event_ids:
            store.execute(
                "UPDATE events SET body_json='{}',redacted=1,memory_processed=1 WHERE event_id=?",
                (event_id,),
            )
            store.execute(
                "UPDATE jobs SET state='CANCELLED',lease_token=NULL,"
                "last_error_code='PRIVACY_CHANGED' "
                "WHERE event_id=? AND state IN ('QUEUED','RUNNING','RETRY_WAIT')",
                (event_id,),
            )
        clear_reply = (
            "UPDATE outbox SET content_json='{}',content_sha256='',"
            "state=CASE WHEN state='SENDING' THEN 'DELIVERY_UNCERTAIN' "
            "WHEN state IN ('PENDING','LEASED','RETRY_WAIT') THEN 'CANCELLED' ELSE state END"
        )
        if record.get("target_type") == "diary":
            # Diary commands never enter chat context, memory extraction, or diary sources.
            for event_id in event_ids:
                store.execute(clear_reply + " WHERE origin_event_id=?", (event_id,))
        else:
            # Summaries/replies may paraphrase the forgotten raw fact.
            store.execute("DELETE FROM conversation_summaries")
            store.execute("UPDATE episodes SET title='',summary='',status='REDACTED'")
            store.execute("DELETE FROM trait_evidence")
            store.execute("DELETE FROM traits")
            store.execute(clear_reply)
        advance_epoch(store)
        store.execute(
            "INSERT OR IGNORE INTO privacy_erasure_ledger VALUES (?,?,?,?,?,?)",
            (
                record["erasure_id"],
                record.get("target_type", "memory"),
                record["memory_id"],
                record["at"],
                record["seq"],
                "COMPLETE",
            ),
        )
