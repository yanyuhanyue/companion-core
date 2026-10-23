"""Durable reply revisions and actual prompt source provenance."""

from alembic import op

revision = "0022_input_reply_revision"
down_revision = "0021_reflection_admission_retry"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("ALTER TABLE conversations ADD COLUMN reply_revision INTEGER NOT NULL "
               "DEFAULT 0 CHECK(reply_revision>=0)")
    op.execute("ALTER TABLE events ADD COLUMN reply_revision INTEGER NOT NULL "
               "DEFAULT 0 CHECK(reply_revision>=0)")
    op.execute("ALTER TABLE jobs ADD COLUMN context_tracked INTEGER NOT NULL "
               "DEFAULT 0 CHECK(context_tracked IN (0,1))")
    op.execute("CREATE TABLE job_sources (job_id TEXT NOT NULL REFERENCES jobs(job_id) "
               "ON DELETE CASCADE,event_id TEXT NOT NULL REFERENCES events(event_id),"
               "PRIMARY KEY(job_id,event_id))")
    op.execute("CREATE INDEX job_sources_event ON job_sources(event_id,job_id)")


def downgrade():
    connection = op.get_bind()
    if (connection.exec_driver_sql(
        "SELECT 1 FROM conversations WHERE reply_revision>0 LIMIT 1"
    ).first() or connection.exec_driver_sql(
        "SELECT 1 FROM events WHERE reply_revision>0 LIMIT 1"
    ).first() or connection.exec_driver_sql(
        "SELECT 1 FROM job_sources s JOIN jobs j USING(job_id) "
        "WHERE s.event_id<>j.event_id LIMIT 1"
    ).first()):
        raise RuntimeError("REPLY_REVISION_DOWNGRADE_REQUIRES_EXPLICIT_DATA_REVIEW")
    op.execute("DROP TABLE job_sources")
    op.execute("ALTER TABLE jobs DROP COLUMN context_tracked")
    op.execute("ALTER TABLE events DROP COLUMN reply_revision")
    op.execute("ALTER TABLE conversations DROP COLUMN reply_revision")
