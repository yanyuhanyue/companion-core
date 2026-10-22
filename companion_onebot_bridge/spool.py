import hashlib
import json
import os
import re
import sqlite3
from pathlib import Path

from .onebot_client import bounded_post


def encode(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


class Spool:
    """One OS-held instance; durable receipts retain hashes, not acknowledged text."""

    def __init__(self, config):
        self.config, self.db, self.lock = config, None, None
        self.directory = Path(config.data_dir)
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        if self.directory.is_symlink() or not self.directory.is_dir():
            raise ValueError("PRIVATE_DATA_DIRECTORY_REQUIRED")
        if os.name != "nt":
            if self.directory.stat().st_mode & 0o077:
                raise ValueError("PRIVATE_DATA_DIRECTORY_REQUIRED")
        try:
            lock_path = self.directory / "instance.lock"
            database = self.directory / "spool.sqlite3"
            for path in (
                lock_path,
                database,
                Path(str(database) + "-wal"),
                Path(str(database) + "-shm"),
            ):
                if path.is_symlink():
                    raise ValueError("SYMLINK_REFUSED")
            self.lock = lock_path.open("a+b")
            if os.name != "nt":
                os.chmod(lock_path, 0o600)
            if lock_path.stat().st_size == 0:
                self.lock.write(b"0")
                self.lock.flush()
            self.lock.seek(0)
            try:
                if os.name == "nt":
                    import msvcrt

                    msvcrt.locking(self.lock.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(self.lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError as error:
                raise RuntimeError("BRIDGE_ALREADY_RUNNING") from error
            self.db = sqlite3.connect(database)
            if os.name != "nt":
                os.chmod(database, 0o600)
            self.db.execute("PRAGMA journal_mode=WAL")
            self.db.execute("PRAGMA synchronous=FULL")
            self.db.execute("PRAGMA secure_delete=ON")
            self.db.execute("PRAGMA journal_size_limit=1048576")
            self.db.executescript(
                "CREATE TABLE IF NOT EXISTS identity (value TEXT NOT NULL);"
                "CREATE TABLE IF NOT EXISTS inbox (key TEXT PRIMARY KEY,fingerprint TEXT NOT NULL,"
                "body TEXT,trace TEXT NOT NULL,state TEXT NOT NULL DEFAULT 'pending');"
                "CREATE TABLE IF NOT EXISTS results (message_id TEXT PRIMARY KEY,"
                "body TEXT NOT NULL,state TEXT NOT NULL DEFAULT 'pending');"
            )
            identity = encode(
                [config.owner_qq_id, config.bot_qq_id, config.origin, config.protocol_epoch]
            )
            row = self.db.execute("SELECT value FROM identity").fetchone()
            if row and row[0] != identity:
                raise ValueError("BRIDGE_IDENTITY_CHANGED")
            if not row:
                with self.db:
                    self.db.execute("INSERT INTO identity VALUES (?)", (identity,))
            for accepted in self.db.execute(
                "SELECT key FROM inbox WHERE state IN ('accepted','erased') AND body IS NULL"
            ).fetchall():
                self.remove_capture_text(accepted[0])
        except BaseException:
            self.close()
            raise

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

    def close(self):
        if self.db:
            self.db.close()
            self.db = None
        if self.lock:
            # Closing the descriptor releases the OS lock, including on process death.
            self.lock.close()
            self.lock = None

    @property
    def pending_count(self):
        return self.db.execute("SELECT COUNT(*) FROM inbox WHERE state='pending'").fetchone()[0]

    def capacity(self, extra_bytes=0):
        size = sum(path.stat().st_size for path in self.directory.glob("spool.sqlite3*"))
        size += sum(path.stat().st_size for path in (self.directory / "captures").glob("*.json"))
        if size + extra_bytes + 32768 >= self.config.max_disk_bytes:
            raise ValueError("SPOOL_DISK_CAPACITY")

    def put(self, payload, fingerprint=None):
        body = encode(payload)
        fingerprint = (
            fingerprint
            or hashlib.sha256(
                encode({k: v for k, v in payload.items() if k != "trace_id"}).encode()
            ).hexdigest()
        )
        key = payload["event_id"]
        with self.db:
            prior = self.db.execute("SELECT fingerprint FROM inbox WHERE key=?", (key,)).fetchone()
            if prior:
                if prior[0] != fingerprint:
                    raise ValueError("SOURCE_EVENT_CONFLICT")
                return False
            if self.pending_count >= self.config.max_pending:
                raise ValueError("SPOOL_QUEUE_CAPACITY")
            self.capacity(len(body.encode()) * 3)
            self.db.execute(
                "INSERT INTO inbox(key,fingerprint,body,trace) VALUES (?,?,?,?)",
                (key, fingerprint, body, payload["trace_id"]),
            )
        return True

    async def flush_one(self, client):
        row = self.db.execute(
            "SELECT key,body FROM inbox WHERE state='pending' ORDER BY rowid LIMIT 1"
        ).fetchone()
        if not row:
            return False
        status, result = await bounded_post(
            client,
            "/internal/v1/inbox/events",
            json.loads(row[1]),
            headers={"X-Idempotency-Key": row[0]},
            statuses=(202, 410),
            with_status=True,
        )
        if status == 410:
            if result != {"error": "EVENT_ERASED"}:
                raise ValueError("INVALID_ERASURE_RECEIPT")
            with self.db:
                self.db.execute("UPDATE inbox SET state='erased',body=NULL WHERE key=?", (row[0],))
            self.remove_capture_text(row[0])
            self.db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            return json.loads(row[1])
        if (
            not isinstance(result, dict)
            or result.get("event_id") != row[0]
            or not isinstance(result.get("job_id"), str)
            or not result["job_id"].startswith("job_")
            or type(result.get("duplicate")) is not bool
            or result.get("state")
            not in {"QUEUED", "RUNNING", "RETRY_WAIT", "SUCCEEDED", "FAILED", "CANCELLED"}
        ):
            raise ValueError("INVALID_CORE_ACCEPTANCE")
        with self.db:
            self.db.execute("UPDATE inbox SET state='accepted',body=NULL WHERE key=?", (row[0],))
        self.remove_capture_text(row[0])
        self.db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        return json.loads(row[1])

    def remove_capture_text(self, event_id):
        # Same lifetime as inbox.body: Core owns accepted text and erasure replay.
        # Keep signed/hash receipts; never create a second plaintext archive here.
        if not self.db.execute(
            "SELECT 1 FROM sqlite_master WHERE name='window_inputs'"
        ).fetchone():
            return
        row = self.db.execute(
            "SELECT capture_json FROM window_inputs WHERE event_id=?", (event_id,)
        ).fetchone()
        if not row or not row[0]:
            return
        evidence_hash = json.loads(row[0])["evidence_sha256"]
        if not re.fullmatch(r"[a-f0-9]{64}", evidence_hash):
            raise ValueError("INVALID_CAPTURE_EVIDENCE")
        path = self.directory / "captures" / (evidence_hash + ".json")
        if path.is_symlink() or path.parent.is_symlink():
            raise ValueError("SYMLINK_REFUSED")
        if not path.exists():
            return
        path.unlink(missing_ok=True)
        if os.name != "nt" and path.parent.exists():
            descriptor = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(descriptor)
            finally:
                os.close(descriptor)

    def trace_for(self, delivery_key):
        match = re.fullmatch(r"reply:(evt_[a-f0-9]{64}):v(?:1|2:[0-2])", delivery_key or "")
        row = (
            self.db.execute("SELECT trace FROM inbox WHERE key=?", (match[1],)).fetchone()
            if match
            else None
        )
        return row[0] if row else None

    def start_delivery(self, item):
        self.capacity(4096)
        with self.db:
            if self.db.execute(
                "SELECT 1 FROM results WHERE message_id=?", (item["message_id"],)
            ).fetchone():
                raise ValueError("DELIVERY_ALREADY_ATTEMPTED")
            self.db.execute(
                "INSERT INTO results(message_id,body) VALUES (?,?)",
                (
                    item["message_id"],
                    encode(
                        {
                            "lease_token": item["lease_token"],
                            "result": "DELIVERY_UNCERTAIN",
                            "error_code": "BRIDGE_SEND_INTERRUPTED",
                        }
                    ),
                ),
            )

    def finish_delivery(self, message_id, provider_id):
        with self.db:
            row = self.db.execute(
                "SELECT body FROM results WHERE message_id=?", (message_id,)
            ).fetchone()
            body = json.loads(row[0])
            if provider_id is not None:
                body = {
                    "lease_token": body["lease_token"],
                    "result": "SENT",
                    "provider_message_id": provider_id,
                }
            self.db.execute(
                "UPDATE results SET body=? WHERE message_id=?", (encode(body), message_id)
            )

    async def flush_result(self, client):
        row = self.db.execute(
            "SELECT message_id,body FROM results WHERE state='pending' ORDER BY rowid LIMIT 1"
        ).fetchone()
        if not row:
            return False
        status, result = await bounded_post(
            client,
            f"/internal/v1/deliveries/{row[0]}/result",
            json.loads(row[1]),
            statuses=(200, 409),
            with_status=True,
        )
        if status == 409:
            # A human may already have reconciled it; never send it again.
            if result.get("error") not in {"STALE_DELIVERY_LEASE", "INVALID_DELIVERY_TRANSITION"}:
                raise ValueError("INVALID_CORE_RESULT_ACK")
            state = "rejected"
        else:
            expected = json.loads(row[1])["result"]
            if result != {"state": expected}:
                raise ValueError("INVALID_CORE_RESULT_ACK")
            state = "accepted"
        with self.db:
            self.db.execute("UPDATE results SET state=? WHERE message_id=?", (state, row[0]))
        self.db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        return True
