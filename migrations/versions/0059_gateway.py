"""Client-scoped Gateway custody over the original model usage ledger."""

from alembic import op

revision = "0059_gateway"
down_revision = "0058_bili_discovery"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""CREATE TABLE gateway_clients (
        client_id TEXT PRIMARY KEY, key_sha256 TEXT NOT NULL UNIQUE,
        key_id TEXT NOT NULL UNIQUE, grant_json TEXT NOT NULL,
        revoked INTEGER NOT NULL DEFAULT 0 CHECK(revoked IN (0,1)), updated_at TEXT NOT NULL,
        restore_generation TEXT NOT NULL, journal_path TEXT NOT NULL
    )""")
    op.execute("""CREATE TABLE gateway_requests (
        request_id TEXT PRIMARY KEY, client_id TEXT NOT NULL REFERENCES gateway_clients(client_id),
        model_request_id TEXT NOT NULL UNIQUE, idempotency_sha256 TEXT NOT NULL,
        body_sha256 TEXT NOT NULL, frozen_json TEXT NOT NULL,
        request_cipher TEXT, response_cipher TEXT,
        state TEXT NOT NULL CHECK(state IN
            ('QUEUED','RUNNING','SUCCEEDED','FAILED','UNKNOWN','EXPIRED')),
        created_at TEXT NOT NULL, expires_at TEXT NOT NULL, deadline TEXT NOT NULL,
        lease_token TEXT, lease_until TEXT, error_code TEXT,
        error_status INTEGER CHECK(error_status BETWEEN 400 AND 599),
        UNIQUE(client_id,idempotency_sha256)
    )""")
    op.execute("CREATE INDEX gateway_pending ON gateway_requests(state,created_at)")


def downgrade():
    connection = op.get_bind()
    if connection.exec_driver_sql("SELECT 1 FROM gateway_clients LIMIT 1").first() or (
        connection.exec_driver_sql("SELECT 1 FROM gateway_requests LIMIT 1").first()
    ):
        raise RuntimeError("Gateway custody blocks downgrade")
    op.execute("DROP TABLE gateway_requests")
    op.execute("DROP TABLE gateway_clients")
