import json
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path

from alembic import command
from alembic.config import Config


def uid(prefix):
    return f"{prefix}_{uuid.uuid4().hex}"


def utcnow():
    return datetime.now(UTC).isoformat(timespec="microseconds")


def after(now, seconds):
    return (datetime.fromisoformat(now) + timedelta(seconds=seconds)).isoformat(
        timespec="microseconds"
    )


def encode(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def migrate(path, revision="head"):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    migrations = Path(__file__).parent.parent / "migrations"
    if not migrations.exists():
        migrations = Path(__file__).parent / "migrations"
    cfg = Config()
    cfg.set_main_option("script_location", str(migrations))
    cfg.set_main_option("sqlalchemy.url", "sqlite:///" + Path(path).resolve().as_posix())
    command.upgrade(cfg, revision)


class Store:
    def __init__(self, path):
        self.path = path
        self.db = sqlite3.connect(path, isolation_level=None, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        for pragma in (
            "foreign_keys=ON",
            "busy_timeout=5000",
            "journal_mode=WAL",
            "synchronous=FULL",
            "secure_delete=ON",
        ):
            self.db.execute("PRAGMA " + pragma)

    @contextmanager
    def transaction(self):
        self.db.execute("BEGIN IMMEDIATE")
        try:
            yield self
            self.db.commit()
        except BaseException:
            self.db.rollback()
            raise

    def execute(self, sql, params=()):
        return self.db.execute(sql, params)

    def rows(self, sql, params=()):
        return [dict(row) for row in self.db.execute(sql, params).fetchall()]

    def one(self, sql, params=()):
        row = self.db.execute(sql, params).fetchone()
        return dict(row) if row else None

    def schema_capabilities(self):
        required = {
            "events": {"event_id", "redacted", "memory_processed", "fingerprint"},
            "jobs": {"lease_token", "state"},
            "outbox": {"lease_token", "state", "content_json"},
            "memories": {"needs_review", "fact_key"},
            "memory_vectors": {"model_version", "content_hash", "vector"},
            "runtime_meta": {"key", "value"},
            "diary_runs": {"captured_privacy_epoch", "lease_token", "source_set_sha256"},
            "diary_entries": {"body_md", "status", "version"},
            "diary_evidence": {"event_id", "outbox_message_id"},
            "diary_revisions": {"prior_title", "prior_body_md"},
            "observations": {"event_id", "result_status", "captured_privacy_epoch"},
            "event_dependencies": {"event_id", "source_event_id"},
            "consents": {"capabilities_json", "expires_at", "revoked_at"},
        }
        return all(
            columns <= {row["name"] for row in self.rows(f"PRAGMA table_info({table})")}
            for table, columns in required.items()
        )

    def insert(self, table, **values):
        # Identifiers are application constants, never API input.
        names = ",".join(values)
        marks = ",".join("?" for _ in values)
        self.execute(f"INSERT INTO {table} ({names}) VALUES ({marks})", tuple(values.values()))

    def audit(self, action, entity_id, trace_id, now, **safe_detail):
        self.insert(
            "audit_log",
            action=action,
            actor_id="system",
            entity_type="core",
            entity_id=entity_id,
            trace_id=trace_id,
            safe_detail_json=encode(safe_detail),
            created_at=now,
        )

    def close(self):
        self.db.close()
