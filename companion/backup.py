import os
import sqlite3
import tempfile
from pathlib import Path

from cryptography.fernet import Fernet

from companion.contracts import CoreError
from companion.privacy import ErasureLedger, advance_epoch
from companion.store import Store, migrate, utcnow


def backup(database, destination, key):
    target = Path(destination)
    if target.exists():
        raise CoreError("BACKUP_DESTINATION_EXISTS")
    target.parent.mkdir(parents=True, exist_ok=True)
    cipher = Fernet(key)
    # Stdlib backup API includes committed WAL pages. Temporary directory is OS-private.
    with tempfile.TemporaryDirectory(dir=Path(database).resolve().parent) as directory:
        snapshot = Path(directory) / "snapshot.sqlite3"
        source = sqlite3.connect(f"file:{Path(database).resolve().as_posix()}?mode=ro", uri=True)
        dest = sqlite3.connect(snapshot)
        try:
            source.backup(dest)
        finally:
            source.close()
            dest.close()
        with target.open("xb") as stream:
            stream.write(cipher.encrypt(snapshot.read_bytes()))
            stream.flush()
            os.fsync(stream.fileno())


def restore(source, destination, key, ledger_path):
    dest = Path(destination)
    if dest.exists():
        raise CoreError("RESTORE_REQUIRES_NEW_PATH")
    # Never initialize a new ledger during restore: newest independent copy is mandatory.
    ledger = ErasureLedger(ledger_path)
    ledger.read()
    payload = Fernet(key).decrypt(Path(source).read_bytes())
    dest.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=dest.parent) as directory:
        staged = Path(directory) / "restored.sqlite3"
        staged.write_bytes(payload)
        migrate(str(staged))
        store = Store(str(staged))
        try:
            identity = store.one("SELECT value FROM runtime_meta WHERE key='ledger_id'")
            if not identity or identity["value"] != ledger.read()[0]["ledger_id"]:
                raise CoreError("ERASURE_LEDGER_MISMATCH", 503)
            ledger.replay(store)
            # A snapshot cannot authorize a device session that existed before restoration.
            with store.transaction():
                store.execute(
                    "UPDATE outbox SET state='DELIVERY_UNCERTAIN',lease_token=NULL,"
                    "last_error_code='RESTORED_DELIVERY_REQUIRES_RECONCILIATION' "
                    "WHERE state IN ('PENDING','LEASED','SENDING','RETRY_WAIT')"
                )
                store.execute("UPDATE model_usage SET state='UNKNOWN' WHERE state='RESERVED'")
                if store.execute(
                    "UPDATE consents SET revoked_at=? WHERE revoked_at IS NULL", (utcnow(),)
                ).rowcount:
                    advance_epoch(store)
            if store.one("PRAGMA integrity_check")["integrity_check"] != "ok":
                raise CoreError("BACKUP_INTEGRITY_FAILED")
            if store.rows("PRAGMA foreign_key_check"):
                raise CoreError("BACKUP_FOREIGN_KEYS_FAILED")
            store.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            # Compact erased cells out of the newly restored file before exposing it.
            store.execute("VACUUM")
        finally:
            store.close()
        os.replace(staged, dest)
