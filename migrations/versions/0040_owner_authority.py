"""Versioned owner authority in the original Core database; no automatic grant."""

from alembic import op

revision = "0040_owner_authority"
down_revision = "0039_evaluation"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""CREATE TABLE owner_authority_revisions (
        grant_id TEXT NOT NULL,
        revision INTEGER NOT NULL CHECK(revision>0),
        operation_id TEXT NOT NULL UNIQUE,
        request_sha256 TEXT NOT NULL,
        state TEXT NOT NULL CHECK(state IN ('ACTIVE','STOPPED')),
        policy_json TEXT NOT NULL
            CHECK(json_valid(policy_json) AND json_type(policy_json)='object'),
        policy_sha256 TEXT NOT NULL,
        policy_redacted INTEGER NOT NULL DEFAULT 0 CHECK(policy_redacted IN (0,1)),
        restore_generation TEXT NOT NULL,
        predecessor_sha256 TEXT,
        record_sha256 TEXT NOT NULL UNIQUE,
        journal_id TEXT NOT NULL,
        journal_path TEXT NOT NULL,
        created_at TEXT NOT NULL,
        PRIMARY KEY(grant_id,revision)
    )""")
    # The previous runtime is evidence, never an active owner grant after upgrade.
    op.execute("INSERT OR IGNORE INTO runtime_meta VALUES ('owner_authority_restore_generation',"
               "'initial')")
    op.execute("INSERT OR IGNORE INTO runtime_meta VALUES ('owner_authority_mode','OFF')")
    for table in ("jobs", "model_admissions", "outbox"):
        op.execute(f"ALTER TABLE {table} ADD COLUMN authority_json TEXT NOT NULL DEFAULT '{{}}' "
                   "CHECK(json_valid(authority_json) AND json_type(authority_json)='object')")


def downgrade():
    # Removing even an empty OFF marker can restore a formerly OPEN legacy runtime.
    # SQLite DDL may commit before an older revision's custody guard rejects rollback.
    raise RuntimeError("OWNER_AUTHORITY_DOWNGRADE_REQUIRES_EXPLICIT_CUSTODY_RECONCILIATION")
