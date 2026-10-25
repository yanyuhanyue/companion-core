"""Pin native Voice jobs to their original owner grant without backfilling history."""

from alembic import op

revision = "0044_voice_owner_authority"
down_revision = "0043_reader_authority"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("ALTER TABLE voice_jobs ADD COLUMN authority_json TEXT NOT NULL DEFAULT '{}' "
               "CHECK(json_valid(authority_json) AND json_type(authority_json)='object')")


def downgrade():
    raise RuntimeError("VOICE_AUTHORITY_DOWNGRADE_REQUIRES_EXPLICIT_CUSTODY_RECONCILIATION")
