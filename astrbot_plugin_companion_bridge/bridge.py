import hashlib
import json
import re
import sqlite3
from datetime import UTC, datetime


def _unique_fields(items):
    fields = dict(items)
    if len(fields) != len(items):
        raise ValueError("duplicate receipt field")
    return fields


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


class Spool:
    def __init__(self, path):
        self.db = sqlite3.connect(path)
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
        self.db.commit()

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
            self.db.execute("INSERT INTO protocol_generations VALUES (?,?)", (epoch, backend))

    def put(self, key, payload):
        body = json.dumps(payload, ensure_ascii=False, sort_keys=True)
        with self.db:
            prior = self.db.execute("SELECT body FROM inbox WHERE key=?", (key,)).fetchone()
            if prior and prior[0] != body:
                raise ValueError("conflicting source event")
            self.db.execute("INSERT OR IGNORE INTO inbox(key,body) VALUES (?,?)", (key, body))

    async def flush_one(self, client):
        row = self.db.execute(
            "SELECT key,body FROM inbox WHERE state='pending' ORDER BY rowid LIMIT 1"
        ).fetchone()
        if not row:
            return
        response = await client.post(
            "/internal/v1/inbox/events",
            content=row[1].encode(),
            headers={"X-Idempotency-Key": row[0], "Content-Type": "application/json"},
        )
        erased = False
        if response.status_code == 410 and len(response.content) <= 65536:
            try:
                erased = response.json(object_pairs_hook=_unique_fields) == {
                    "error": "EVENT_ERASED"
                }
            except (ValueError, UnicodeError, RecursionError):
                pass
        if erased:
            with self.db:
                self.db.execute("DELETE FROM inbox WHERE key=?", (row[0],))
            self.db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            return
        with self.db:
            if response.status_code == 202:
                self.db.execute("DELETE FROM inbox WHERE key=?", (row[0],))
            elif response.status_code in {400, 403, 409, 422}:
                self.db.execute("UPDATE inbox SET state='rejected' WHERE key=?", (row[0],))
        response.raise_for_status()


async def deliver_one(client, item, send):
    message_id = item["message_id"]
    body = {"lease_token": item["lease_token"]}
    started = await client.post(f"/internal/v1/deliveries/{message_id}/begin-send", json=body)
    started.raise_for_status()
    # If begin-send times out, do not send: the server may already have committed SENDING.
    try:
        accepted = await send(item["conversation_platform_session_key"], item["content"]["text"])
        outcome = "SENT" if accepted is True else "DELIVERY_UNCERTAIN"
    except Exception:
        outcome = "DELIVERY_UNCERTAIN"
    result = await client.post(
        f"/internal/v1/deliveries/{message_id}/result", json={**body, "result": outcome}
    )
    result.raise_for_status()
