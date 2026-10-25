"""Local cutover gate; Core remains authoritative for old work and delivery outcomes."""

import hashlib
import json
import sqlite3
import time
from contextlib import closing
from pathlib import Path

from .instance_lock import acquire


def digest(value):
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def inventory(db):
    return {
        "inbox": dict(db.execute("SELECT state,COUNT(*) FROM inbox GROUP BY state")),
        "results": dict(db.execute("SELECT state,COUNT(*) FROM results GROUP BY state")),
    }


def require_drained(db):
    value = inventory(db)
    if value["inbox"].get("pending", 0) or value["results"].get("pending", 0):
        raise ValueError("ASTRBOT_RECONCILIATION_REQUIRED")
    return value


def read_marker(db):
    if not db.execute("SELECT 1 FROM sqlite_master WHERE name='native_handoff'").fetchone():
        raise ValueError("ASTRBOT_HANDOFF_REQUIRED")
    rows = db.execute("SELECT value FROM native_handoff").fetchall()
    if len(rows) != 1:
        raise ValueError("ASTRBOT_HANDOFF_REQUIRED")
    return json.loads(rows[0][0])


def acquire_handoff(config):
    path = Path(config.astrbot_handoff_path)
    if (not path.is_file() or path.is_symlink()
        or any(parent.is_symlink() for parent in path.parents)):
        raise ValueError("ASTRBOT_HANDOFF_DATABASE_REQUIRED")
    lock = acquire(str(path) + ".lock")
    try:
        with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as db:
            value = read_marker(db)
            require_drained(db)
            if (digest(value) != config.astrbot_handoff_sha256
                or value["native"] != [config.owner_qq_id, config.bot_qq_id,
                                       config.origin, config.protocol_epoch]
                or value["ingress_after"] != config.ingress_after):
                raise ValueError("ASTRBOT_HANDOFF_CHANGED")
            if time.time() <= value["ingress_after"]:
                raise ValueError("ASTRBOT_CUTOVER_NOT_REACHED")
        return lock
    except BaseException:
        lock.close()
        raise
