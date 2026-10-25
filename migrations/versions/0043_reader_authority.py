"""Pin native goals and Reader requests to their original owner grant."""

from alembic import op

revision = "0043_reader_authority"
down_revision = "0042_rem"
branch_labels = None
depends_on = None


def upgrade():
    for table in ("goals", "exploration_requests"):
        op.execute(f"ALTER TABLE {table} ADD COLUMN authority_json TEXT NOT NULL DEFAULT '{{}}' "
                   "CHECK(json_valid(authority_json) AND json_type(authority_json)='object')")


def downgrade():
    raise RuntimeError("OWNER_AUTHORITY_DOWNGRADE_REQUIRES_EXPLICIT_CUSTODY_RECONCILIATION")
