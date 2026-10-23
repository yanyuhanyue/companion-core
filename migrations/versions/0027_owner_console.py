"""Durable owner console sessions, configuration versions and operation receipts."""

from alembic import op

revision = "0027_owner_console"
down_revision = "0026_cloud_budget"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("CREATE TABLE operator_configuration (version INTEGER PRIMARY KEY,"
               "request_id TEXT NOT NULL UNIQUE,request_sha256 TEXT NOT NULL,"
               "encrypted_overrides TEXT NOT NULL,fields_json TEXT NOT NULL,"
               "created_at TEXT NOT NULL)")
    op.execute("CREATE TABLE console_sessions (token_sha256 TEXT PRIMARY KEY,"
               "csrf_token TEXT NOT NULL,credential_tag TEXT NOT NULL,"
               "created_at TEXT NOT NULL,expires_at TEXT NOT NULL)")
    op.execute("CREATE TABLE console_operations (request_id TEXT PRIMARY KEY,"
               "request_sha256 TEXT NOT NULL,state TEXT NOT NULL,"
               "result_json TEXT,created_at TEXT NOT NULL,updated_at TEXT NOT NULL)")
    op.execute("CREATE TABLE console_login_attempts (client_tag TEXT PRIMARY KEY,"
               "attempts INTEGER NOT NULL,expires_at TEXT NOT NULL)")


def downgrade():
    for table in ("operator_configuration", "console_operations"):
        if op.get_bind().exec_driver_sql(f"SELECT 1 FROM {table} LIMIT 1").first():
            raise RuntimeError("CONSOLE_DOWNGRADE_REQUIRES_EXPLICIT_DATA_REVIEW")
    for table in ("console_sessions", "console_login_attempts", "console_operations",
                  "operator_configuration"):
        op.execute(f"DROP TABLE {table}")
