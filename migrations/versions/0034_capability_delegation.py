"""Owner delegation of bounded runtime adjustments and the adjustments themselves."""

from alembic import op

revision = "0034_capability_delegation"
down_revision = "0033_model_usage_job_index"
branch_labels = None
depends_on = None


def upgrade():
    op.execute(
        "CREATE TABLE capability_delegations ("
        "capability TEXT PRIMARY KEY,"
        "mode TEXT NOT NULL CHECK(mode IN ('owner','propose','self')),"
        "allow_pause INTEGER NOT NULL CHECK(allow_pause IN (0,1)),"
        "ranges_json TEXT NOT NULL,"
        "updated_at TEXT NOT NULL)"
    )
    op.execute(
        "CREATE TABLE capability_overrides ("
        "override_id TEXT PRIMARY KEY,"
        "capability TEXT NOT NULL,"
        "field TEXT NOT NULL,"
        "value_json TEXT NOT NULL,"
        "suggestion_id TEXT REFERENCES reflection_suggestions(suggestion_id),"
        "reason TEXT NOT NULL,"
        "state TEXT NOT NULL CHECK(state IN "
        "('PROPOSED','ACTIVE','REJECTED','REVERTED','EXPIRED','SUPERSEDED','DISCARDED')),"
        "created_at TEXT NOT NULL,"
        "decided_at TEXT,"
        "expires_at TEXT)"
    )
    op.execute(
        "CREATE INDEX idx_capability_overrides_current "
        "ON capability_overrides(capability,field,state)"
    )


def downgrade():
    op.execute("DROP INDEX idx_capability_overrides_current")
    op.execute("DROP TABLE capability_overrides")
    op.execute("DROP TABLE capability_delegations")
