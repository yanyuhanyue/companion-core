"""Model-judged owner interaction signals feeding the mood projection."""

from alembic import op

revision = "0059_mood_appraisals"
down_revision = "0058_bili_discovery"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""CREATE TABLE mood_appraisals (
        event_id TEXT PRIMARY KEY REFERENCES events(event_id),
        reason TEXT NOT NULL CHECK(reason IN ('OWNER_AFFECTION','OWNER_HURT_BY_REPLY',
            'OWNER_DISTRESS','OWNER_EXPECTATION','OWNER_PLAYFUL')),
        quote_start INTEGER NOT NULL CHECK(quote_start>=0),
        quote_length INTEGER NOT NULL CHECK(quote_length BETWEEN 2 AND 200),
        quote_sha256 TEXT NOT NULL CHECK(length(quote_sha256)=64),
        request_id TEXT NOT NULL REFERENCES model_usage(request_id),
        created_at TEXT NOT NULL
    )""")
    op.execute("CREATE INDEX idx_mood_appraisals_created ON mood_appraisals(created_at)")


def downgrade():
    op.execute("DROP TABLE mood_appraisals")
