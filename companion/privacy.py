import json
import os
import re
from pathlib import Path

from companion.contracts import CoreError
from companion.store import encode, uid

SECRET_PATTERNS = [
    r"(?i)\b(?:sk-|ghp_|gho_)[a-z0-9_-]{12,}",
    r"(?i)\bBearer\s+[^\s\"'<>]+",
    r"(?i)\b(?:api[_-]?key|password|passwd|token|cookie|skey|p_skey)\s*[:=]\s*[^\s,;]+",
    r"(?:密码|密钥)\s*[:：=]\s*[^\s,，;；]+",
]


def redact(text, settings):
    for secret in (settings.bridge_token.get_secret_value(), settings.model_key.get_secret_value()):
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
                if record["seq"] != seq or not isinstance(record["event_ids"], list):
                    raise ValueError("invalid sequence")
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
        for memory_id in record["memory_ids"]:
            store.execute(
                "UPDATE memories SET content='',status='FORGOTTEN',fact_key='',"
                "confidence=0,updated_at=? WHERE memory_id=?",
                (record["at"], memory_id),
            )
            store.execute("DELETE FROM memory_revisions WHERE memory_id=?", (memory_id,))
            store.execute("DELETE FROM memory_evidence WHERE memory_id=?", (memory_id,))
        for event_id in record["event_ids"]:
            store.execute(
                "UPDATE events SET body_json='{}',redacted=1,memory_processed=1 WHERE event_id=?",
                (event_id,),
            )
            store.execute(
                "UPDATE jobs SET state='CANCELLED',lease_token=NULL "
                "WHERE event_id=? AND state IN ('QUEUED','RUNNING','RETRY_WAIT')",
                (event_id,),
            )
        # Summaries/replies may paraphrase the forgotten fact; invalidate all owner projections.
        store.execute("DELETE FROM conversation_summaries")
        store.execute("UPDATE episodes SET title='',summary='',status='REDACTED'")
        store.execute("DELETE FROM trait_evidence")
        store.execute("DELETE FROM traits")
        store.execute(
            "UPDATE outbox SET content_json='{}',content_sha256='',"
            "state=CASE WHEN state='SENDING' THEN 'DELIVERY_UNCERTAIN' "
            "WHEN state IN ('PENDING','LEASED','RETRY_WAIT') THEN 'CANCELLED' ELSE state END"
        )
        store.execute(
            "INSERT OR REPLACE INTO runtime_meta VALUES ('privacy_epoch',?)",
            (str(epoch(store) + 1),),
        )
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
