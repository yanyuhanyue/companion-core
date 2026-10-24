import hashlib
import json
import re
import sqlite3
from datetime import UTC, datetime
from types import SimpleNamespace

import httpx

from companion_onebot_bridge.delivery_receipts import DeliveryReceipts
from companion_onebot_bridge.instance_lock import acquire
from companion_onebot_bridge.onebot_client import bounded_post


def normalize(event, owner_id, bot_id, token="", protocol_epoch=""):
    if event.get_platform_name() != "aiocqhttp":
        return None
    if str(event.get_sender_id()) != owner_id or str(event.get_sender_id()) == bot_id:
        return None
    if event.get_group_id() or str(event.message_obj.self_id) != bot_id:
        return None
    origin = event.unified_msg_origin
    if not origin.endswith(f":FriendMessage:{owner_id}"):
        return None
    message_id = str(event.message_obj.message_id)
    if not message_id or message_id == "None":
        return None
    raw = getattr(event.message_obj, "raw_message", {})
    if isinstance(raw, dict) and raw.get("post_type") not in (None, "message"):
        return None
    if protocol_epoch and not re.fullmatch(r"[a-zA-Z0-9_-]{8,64}", protocol_epoch):
        raise ValueError("invalid protocol_epoch")
    generation = f"epoch:{protocol_epoch}:" if protocol_epoch else ""
    source_id = f"{bot_id}:{origin}:{generation}{message_id}"
    key = hashlib.sha256(source_id.encode()).hexdigest()
    text = event.message_str
    if token:
        text = text.replace(token, "[REDACTED]")
    for pattern in (
        r"(?i)\b(?:sk-|ghp_|gho_)[a-z0-9_-]{12,}",
        r"(?i)\bBearer\s+[^\s\"'<>]+",
        r"(?i)\b(?:api[_-]?key|password|passwd|token|cookie|skey|p_skey)\s*[:=]\s*[^\s,;]+",
        r"(?:密码|密钥)\s*[:：=]\s*[^\s,，;；]+",
    ):
        text = re.sub(pattern, "[REDACTED]", text)
    timestamp = datetime.fromtimestamp(float(event.message_obj.timestamp), UTC).isoformat()
    payload = {
        "schema_version": 1,
        "event_id": "evt_" + key,
        "source": "qq.onebot",
        "source_event_id": source_id,
        "event_type": "message.received",
        "occurred_at": timestamp,
        "conversation": {"platform_session_key": origin, "scope": "private"},
        "actor": {"platform_user_id": owner_id, "role": "owner"},
        # M0/M1 are text-only; an attachment-only message receives a text-only capability notice.
        "payload": {"text": text, "media": []},
        "trace_id": "trace_" + key,
    }
    return key, payload


class Spool(DeliveryReceipts):
    def __init__(self, path):
        self.path = path
        self.reconciliation_only = False
        self.config = SimpleNamespace(max_voice_lease_bytes=65536)
        self.lock = acquire(str(path) + ".lock")
        try:
            self.db = sqlite3.connect(path)
        except BaseException:
            self.lock.close()
            raise
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=FULL")
        self.db.execute("PRAGMA secure_delete=ON")
        self.db.execute(
            "CREATE TABLE IF NOT EXISTS inbox "
            "(key TEXT PRIMARY KEY,body TEXT NOT NULL,state TEXT NOT NULL DEFAULT 'pending')"
        )
        self.db.execute(
            "CREATE TABLE IF NOT EXISTS protocol_generations "
            "(epoch TEXT PRIMARY KEY,backend TEXT NOT NULL)"
        )
        self.db.execute("CREATE TABLE IF NOT EXISTS results "
                        "(message_id TEXT PRIMARY KEY,body TEXT NOT NULL,"
                        "state TEXT NOT NULL DEFAULT 'pending')")
        self.db.execute("CREATE TABLE IF NOT EXISTS delivery_identity (value TEXT NOT NULL)")
        self.db.execute("CREATE TABLE IF NOT EXISTS retired_inbox "
                        "(key TEXT PRIMARY KEY,value TEXT NOT NULL)")
        self.db.commit()

    def close(self):
        self.db.close()
        self.lock.close()

    def sealed(self):
        return bool(self.db.execute(
            "SELECT 1 FROM sqlite_master WHERE name='native_handoff'").fetchone())

    def bind_backend(self, backend, epoch):
        if backend not in {"napcat", "snowluma"}:
            raise ValueError("protocol_backend must be napcat or snowluma")
        if epoch and not re.fullmatch(r"[a-zA-Z0-9_-]{8,64}", epoch):
            raise ValueError("protocol_epoch must be 8-64 letters, digits, underscores or hyphens")
        with self.db:
            current = self.db.execute(
                "SELECT epoch,backend FROM protocol_generations ORDER BY rowid DESC LIMIT 1"
            ).fetchone()
            if current == (epoch, backend):
                return
            if not epoch and (current or backend != "napcat"):
                raise ValueError("switching backend requires a fresh protocol_epoch")
            if self.db.execute(
                "SELECT 1 FROM protocol_generations WHERE epoch=?", (epoch,)
            ).fetchone():
                raise ValueError("protocol_epoch cannot be reused, including during rollback")
            if (
                current
                and self.db.execute("SELECT 1 FROM inbox WHERE state='pending' LIMIT 1").fetchone()
            ):
                raise ValueError("drain the existing bridge inbox before switching backend")
            if self.db.execute("SELECT 1 FROM results WHERE state='pending' LIMIT 1").fetchone():
                raise ValueError("reconcile delivery receipts before switching backend")
            self.db.execute("INSERT INTO protocol_generations VALUES (?,?)", (epoch, backend))

    def put(self, key, payload):
        if self.sealed():
            raise ValueError("ASTRBOT_INPUT_RETIRED")
        self.bind_origin(payload)
        body = json.dumps(payload, ensure_ascii=False, sort_keys=True)
        with self.db:
            prior = self.db.execute("SELECT body FROM inbox WHERE key=?", (key,)).fetchone()
            if prior and prior[0] != body:
                raise ValueError("conflicting source event")
            self.db.execute("INSERT OR IGNORE INTO inbox(key,body) VALUES (?,?)", (key, body))

    async def flush_one(self, client):
        if self.sealed():
            raise ValueError("ASTRBOT_INPUT_RETIRED")
        row = self.db.execute(
            "SELECT key,body FROM inbox WHERE state='pending' ORDER BY rowid LIMIT 1"
        ).fetchone()
        if not row:
            return False
        payload = json.loads(row[1])
        try:
            status, result = await bounded_post(
                client, "/internal/v1/inbox/events", payload,
                headers={"X-Idempotency-Key": row[0]}, statuses=(202, 410), with_status=True,
            )
        except httpx.HTTPStatusError as error:
            if error.response.status_code in {400, 403, 409, 422}:
                with self.db:
                    self.db.execute("UPDATE inbox SET state='rejected' WHERE key=?", (row[0],))
            raise
        if status == 410:
            if result != {"error": "EVENT_ERASED"}:
                raise ValueError("INVALID_ERASURE_RECEIPT")
            state = "erased"
        else:
            from companion_onebot_bridge.spool import validate_acceptance

            validate_acceptance(result, payload["event_id"])
            state = "accepted"
        receipt = {"event_id": payload["event_id"], "state": state,
                   "body_sha256": hashlib.sha256(row[1].encode()).hexdigest(),
                   "image_slots": [item["slot"] for item in payload["payload"].get(
                       "shared_content", []) if item.get("kind") == "image"],
                   "core_receipt": result}
        with self.db:
            self.db.execute("INSERT OR REPLACE INTO retired_inbox VALUES (?,?)",
                            (row[0], json.dumps(receipt, sort_keys=True)))
            self.db.execute("DELETE FROM inbox WHERE key=?", (row[0],))
        self.db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        return True


    def bind_origin(self, payload):
        origin = payload["conversation"]["platform_session_key"]
        owner = payload["actor"]["platform_user_id"]
        bot = payload["source_event_id"].split(":", 1)[0]
        value = json.dumps([owner, bot, origin])
        with self.db:
            row = self.db.execute("SELECT value FROM delivery_identity").fetchone()
            if row and row[0] != value:
                raise ValueError("BRIDGE_IDENTITY_CHANGED")
            if not row:
                self.db.execute("INSERT INTO delivery_identity VALUES (?)", (value,))

    def delivery_identity(self, owner, bot):
        # Upgrade the original inbox in place; preserve each body and idempotency key.
        for row in self.db.execute("SELECT body FROM inbox"):
            self.bind_origin(json.loads(row[0]))
        row = self.db.execute("SELECT value FROM delivery_identity").fetchone()
        if not row:
            return None
        saved_owner, saved_bot, origin = json.loads(row[0])
        if (owner, bot) != (saved_owner, saved_bot):
            raise ValueError("BRIDGE_IDENTITY_CHANGED")
        return origin

    def check_backend(self, backend, epoch):
        current = self.db.execute(
            "SELECT epoch,backend FROM protocol_generations ORDER BY rowid DESC LIMIT 1"
        ).fetchone()
        if current != (epoch, backend):
            raise ValueError("BRIDGE_IDENTITY_CHANGED")

    def capacity(self, extra_bytes=0):
        from pathlib import Path

        if sum(p.stat().st_size for p in Path(self.path).parent.glob(
            Path(self.path).name + "*")) + extra_bytes >= 64 * 1024 * 1024:
            raise ValueError("SPOOL_DISK_CAPACITY")

    def trace_for(self, delivery_key):
        return None
